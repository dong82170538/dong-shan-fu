"""Synchronous, bounded Windows owned-process runner. Standard library only."""
import os,sys,json,time,threading,subprocess,pathlib,math,argparse,secrets
CAPTURE_LIMIT=128*1024
MARKER='WINDOWS_OWNED_RUNNER_ACTIVE'
MODULE=pathlib.Path(__file__).resolve()

def _base(reason='not_started',status='BLOCKED'):
    return dict(status=status,reason=reason,timed_out=False,supervisor_pid=None,runner_pid=None,exit_code=None,cleanup=dict(confirmed=True,remaining_pids=[],seconds=0),stdout='',stderr='',owned_processes=[],seconds=0,platform=sys.platform,ownership='dedicated supervisor owns one non-inheritable Job; command assigned atomically before first instruction; only Job-owned handles/identities are managed')

def _validate(argv,cwd,env,timeout):
    if sys.platform!='win32':raise ValueError('Windows required')
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0<timeout<=600:raise ValueError('timeout must be finite and 0 < timeout <= 600')
    if not isinstance(argv,(list,tuple)) or not argv or any(not isinstance(x,str) or '\0' in x for x in argv):raise ValueError('argv must be a nonempty array of strings without NUL')
    if not argv[0]:raise ValueError('empty executable')
    if not pathlib.Path(cwd).is_dir():raise ValueError('cwd must be an existing directory')
    if env is not None and (not isinstance(env,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not k or '=' in k or '\0' in k+v for k,v in env.items())):raise ValueError('env must map valid strings to strings')
    if os.environ.get(MARKER):raise ValueError('nested wrapper refused before supervisor/command creation')

class _Drain:
    def __init__(self,stream,limit):
        self.stream=stream;self.limit=limit;self.data=bytearray();self.total=0;self.error=None
        self.thread=threading.Thread(target=self._read,daemon=True)
    def _read(self):
        try:
            while True:
                chunk=self.stream.read(4096)
                if not chunk:break
                self.total+=len(chunk)
                left=max(0,self.limit-len(self.data))
                if left:self.data.extend(chunk[:left])
        except BaseException as e:self.error=repr(e)
        finally:
            try:self.stream.close()
            except OSError:pass
    def start(self):self.thread.start();return self
    def text(self):return self.data.decode('utf-8',errors='replace')

def _report_body(r):return {k:v for k,v in r.items() if k not in ('stdout','stderr')}

def _save_report(path,r):
    if not path:return
    p=pathlib.Path(path);tmp=p.with_name(p.name+'.'+str(os.getpid())+'.tmp')
    try:
        with tmp.open('x',encoding='utf-8') as f:json.dump(_report_body(r),f,ensure_ascii=False,indent=2)
        os.rename(tmp,p) # Windows refuses replacement of an existing report.
    finally:
        try:tmp.unlink(missing_ok=True)
        except OSError:pass

def _supervise(cfg):
    import _winjob as N
    r=_base();r['supervisor_pid']=os.getpid();start=time.monotonic();job=None;owners=[];drains=[];stopped=threading.Event();stage='setup'
    def controls():
        try:
            for line in sys.stdin:
                if line.strip()=='STOP':stopped.set()
        except OSError:stopped.set()
    try:
        if cfg['caller_pid']!=os.getppid():raise ValueError('supervisor caller is not its direct parent')
        h,ident=N.open_known_owner(cfg['caller_pid'],cfg['caller_identity']);owners.append(h)
        if cfg.get('external_owner'):
            h,ident=N.open_known_owner(cfg['external_owner']['pid'],cfg['external_owner']);owners.append(h)
        # Owner relationship was checked by caller while alive; identity is pinned through retained handles.
        threading.Thread(target=controls,daemon=True).start()
        job=N.OwnedJob()
        environment=dict(os.environ if cfg['env'] is None else cfg['env']);environment[MARKER]=secrets.token_hex(16)
        if stopped.is_set() or any(not N.live(h) for h in owners):raise ValueError('owner/cancellation ended before command creation')
        root=job.start_suspended(cfg['argv'],cfg['cwd'],environment)
        r['runner_pid']=root['pid'];r['ownership_identity']=root;r['pre_execution_job_membership']=True
        fds=job.read_fds;job.read_fds=[] # readers now own the descriptors; no concurrent fd close/reuse.
        for fd in fds:drains.append(_Drain(os.fdopen(fd,'rb',buffering=0),CAPTURE_LIMIT).start())
        if stopped.is_set() or any(not N.live(h) for h in owners):raise ValueError('owner/cancellation ended before command resume')
        stage='running';deadline=start+cfg['timeout']
        if time.monotonic()>=deadline:raise TimeoutError('command deadline before resume')
        job.resume()
        while True:
            job.observe()
            if any(not N.live(h) for h in owners):r.update(status='FAIL',reason='owner_exited');break
            if stopped.is_set():r.update(status='FAIL',reason='stopped');break
            if time.monotonic()>=deadline:r.update(status='FAIL',reason='timeout',timed_out=True);break
            code=job.root_exit()
            if code is not None:
                r['exit_code']=code
                if code:r.update(status='FAIL',reason='command_nonzero')
                else:
                    # The root handle may signal just before its helper processes finish.
                    # Only natural quiescence may pass; persistent descendants still fail.
                    settle_start=time.monotonic();settle_end=min(deadline,settle_start+.25)
                    while job.accounting()['active'] and time.monotonic()<settle_end:
                        job.observe()
                        if stopped.is_set() or any(not N.live(h) for h in owners):break
                        time.sleep(.01)
                    r['post_exit_settle_seconds']=round(time.monotonic()-settle_start,5)
                    if any(not N.live(h) for h in owners):r.update(status='FAIL',reason='owner_exited')
                    elif stopped.is_set():r.update(status='FAIL',reason='stopped')
                    elif job.accounting()['active']:
                        if time.monotonic()>=deadline:r.update(status='FAIL',reason='timeout',timed_out=True)
                        else:r.update(status='FAIL',reason='root_exited_with_live_descendants')
                    else:r.update(status='PASS',reason='command_exit_zero')
                break
            if time.monotonic()>=deadline:r.update(status='FAIL',reason='timeout',timed_out=True);break
            time.sleep(.02)
    except BaseException as e:
        if isinstance(e,TimeoutError):r.update(status='FAIL',reason='timeout',timed_out=True)
        else:r.update(status='BLOCKED' if stage=='setup' else 'FAIL',reason=('setup_refused: ' if stage=='setup' else 'supervisor_exception: ')+repr(e))
        r['error_code']=getattr(e,'winerror',None) or getattr(e,'errno',None)
    finally:
        if job:
            try:
                r['cleanup']=job.cleanup(4)
                if job.launched:
                    r['exit_code']=job.root_exit();r['owned_processes']=job.records()
                r['job_accounting']=r['cleanup'].get('accounting')
            except BaseException as e:r['cleanup']=dict(confirmed=False,remaining_pids=[],seconds=0,error=repr(e))
            handle_errors=job.close();r['handle_close_errors']=handle_errors
            if handle_errors:r['cleanup']['confirmed']=False
        for d in drains:d.thread.join(timeout=1)
        for i,name in enumerate(('stdout','stderr')):
            if i<len(drains):
                d=drains[i];r[name]=d.text();r[name+'_bytes']=d.total;r[name+'_truncated']=d.total>len(d.data)
                if d.thread.is_alive() or d.error:r.update(status='FAIL',reason='output_drain_unconfirmed');r['cleanup']['confirmed']=False
        for h in owners:
            try:N.close(h)
            except OSError:r['cleanup']['confirmed']=False
        if not r['cleanup']['confirmed']:r.update(status='FAIL',reason='cleanup_unconfirmed')
        if r['status']=='PASS' and (r['exit_code']!=0 or r['timed_out']):r.update(status='FAIL',reason='success_invariant_failed')
        r['seconds']=round(time.monotonic()-start,5)
        try:_save_report(cfg.get('report'),r)
        except BaseException as e:r.update(status='FAIL',reason='report_write_failed: '+repr(e))
    return r

def _run(argv,*,cwd,env=None,timeout=60,stop_event=None,external_owner=None,report=None):
    start=time.monotonic();p=None;r=_base()
    try:
        _validate(argv,cwd,env,timeout)
        if stop_event is not None and stop_event.is_set():return _base('stopped_before_start','FAIL')
        import _winjob as N
        if not N.abi_audit()['pass']:raise ValueError('Win32 x64 ABI unsupported')
        if report and (pathlib.Path(report).exists() or not pathlib.Path(report).parent.is_dir()):raise ValueError('report must be a new file in an existing directory')
        cfg=dict(argv=list(argv),cwd=str(pathlib.Path(cwd).resolve()),env=env,timeout=timeout,caller_pid=os.getpid(),caller_identity=N.identity(N.K.GetCurrentProcess()),external_owner=external_owner,report=report)
        p=subprocess.Popen([sys.executable,'-B',str(MODULE),'--_supervisor'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cwd,shell=False,creationflags=subprocess.CREATE_NO_WINDOW)
        p.stdin.write((json.dumps(cfg,ensure_ascii=True)+'\n').encode());p.stdin.flush()
        out=_Drain(p.stdout,2*1024*1024).start();err=_Drain(p.stderr,16*1024).start();sent=False;deadline=time.monotonic()+timeout+12
        while p.poll() is None:
            if stop_event is not None and stop_event.is_set() and not sent:
                try:p.stdin.write(b'STOP\n');p.stdin.flush()
                except OSError:pass
                sent=True
            if time.monotonic()>deadline:
                # Only the dedicated supervisor Popen handle; its sole Job handle closes on exit.
                p.kill();p.wait(timeout=2);raise TimeoutError('supervisor watchdog; cleanup not externally proven')
            time.sleep(.02)
        out.thread.join(2);err.thread.join(2)
        if out.thread.is_alive() or err.thread.is_alive() or out.error:raise RuntimeError('supervisor protocol drain incomplete')
        if out.total>out.limit:raise RuntimeError('supervisor protocol exceeded bound')
        r=json.loads(out.text())
        if not isinstance(r,dict) or r.get('supervisor_pid')!=p.pid:raise RuntimeError('supervisor protocol identity invalid')
        if p.returncode!=0:r.update(status='FAIL',reason='supervisor_nonzero')
        r['supervisor_exit_code']=p.returncode;r['supervisor_stderr_bytes']=err.total
    except BaseException as e:
        r=_base(('launch_refused: ' if p is None else 'supervisor_failure: ')+repr(e),'BLOCKED' if p is None else 'FAIL')
        if isinstance(e,TimeoutError):r['timed_out']=True
        if p is not None:r['supervisor_pid']=p.pid;r['cleanup']['confirmed']=False
    finally:
        if p is not None:
            if p.poll() is None:
                try:p.kill();p.wait(timeout=2)
                except (OSError,subprocess.TimeoutExpired):pass
                r.update(status='FAIL',reason='supervisor_terminated_in_finally');r['cleanup']['confirmed']=False
            if p.stdin:
                try:p.stdin.close()
                except OSError:pass
        r['seconds']=round(time.monotonic()-start,5)
    return r

def run_owned(argv,*,cwd,env=None,timeout=60,stop_event=None):
    """Run an argv array in a fresh supervisor/Job; never attach to existing trees."""
    return _run(argv,cwd=cwd,env=env,timeout=timeout,stop_event=stop_event)

def main():
    if sys.argv[1:]==['--_supervisor']:
        try:cfg=json.loads(sys.stdin.buffer.readline(2*1024*1024));r=_supervise(cfg);print(json.dumps(r,ensure_ascii=True),flush=True);return 0
        except BaseException:return 1
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=float,default=60);parser.add_argument('--report',required=True);parser.add_argument('--cwd',required=True);parser.add_argument('--owner-pid',type=int,required=True)
    parser.add_argument('command',nargs=argparse.REMAINDER);a=parser.parse_args()
    argv=a.command[1:] if a.command[:1]==['--'] else a.command
    h=None;report=str(pathlib.Path(a.report).resolve())
    try:
        if a.owner_pid!=os.getppid():raise ValueError('--owner-pid must be the known direct calling parent')
        import _winjob as N
        h,owner=N.open_known_owner(a.owner_pid)
        r=_run(argv,cwd=a.cwd,timeout=a.timeout,external_owner=owner,report=report)
    except BaseException as e:r=_base('owner_refused: '+repr(e))
    finally:
        if h:N.close(h)
    if not pathlib.Path(report).exists():
        try:_save_report(report,r)
        except OSError:pass
    sys.stdout.write(r['stdout']);sys.stderr.write(r['stderr'])
    sys.stderr.write(json.dumps(_report_body(r),ensure_ascii=True)+'\n')
    return {'PASS':0,'FAIL':1,'BLOCKED':2}.get(r['status'],1)
if __name__=='__main__':sys.exit(main())


