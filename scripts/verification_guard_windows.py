"""Windows acceptance-controller Job guard, allowing inner R2 tests to run.

The controller itself never joins the Job. This is not a way to bypass R2's
nested-wrapper refusal: an inherited R2 marker is rejected, never stripped.
"""
import math,os,time
from owned_process import OwnershipUnsupported
from owned_process_windows import _base,_Drain,_terminal_decision,MARKER
import _winjob as N

def run_verification_owned(argv,*,cwd,env=None,timeout=60,stop_event=None):
    if os.name!='nt' or not N.abi_audit()['pass']:raise OwnershipUnsupported('WINDOWS_JOB_VERIFICATION_REQUIRED')
    if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0<timeout<=600:raise ValueError('bounded timeout required')
    environment=dict(os.environ if env is None else env)
    if os.environ.get(MARKER) or environment.get(MARKER):raise OwnershipUnsupported('VERIFICATION_CONTROLLER_CANNOT_BYPASS_NESTED_R2_MARKER')
    r=_base();r['supervisor_pid']=os.getpid();r['backend']='windows_verification_job_controller';r['ownership']='own anonymous non-inheritable Job, atomic pre-resume assignment; controller outside Job; retained identity handles only'
    start=time.monotonic();deadline=start+timeout;job=None;drains=[];settle_end=None
    try:
        if stop_event and stop_event.is_set():r.update(status='FAIL',reason='stopped_before_start');return r
        job=N.OwnedJob();root=job.start_suspended(argv,str(cwd),environment);r['runner_pid']=root['pid'];r['ownership_identity']=root;r['pre_execution_job_membership']=True
        fds=job.read_fds;job.read_fds=[]
        for fd in fds:drains.append(_Drain(os.fdopen(fd,'rb',buffering=0),128*1024).start())
        if time.monotonic()>=deadline:raise TimeoutError('deadline before resume')
        job.resume()
        while True:
            job.observe();code=job.root_exit();active=job.accounting()['active'] if code is not None else None
            stopped=bool(stop_event and stop_event.is_set());now=time.monotonic()
            decision=_terminal_decision(now=now,deadline=deadline,owner_alive=True,stopped=stopped,exit_code=code,active=active)
            if code is not None:r['exit_code']=code
            if decision is not None:
                if decision[0]=='PASS' and settle_end is not None and now>=settle_end:decision=('FAIL','root_exited_with_live_descendants',False)
                r.update(status=decision[0],reason=decision[1],timed_out=decision[2]);r['terminal_observed_seconds']=now-start;break
            if code==0:
                if settle_end is None:settle_end=min(deadline,now+.25)
                if now>=settle_end:r.update(status='FAIL',reason='root_exited_with_live_descendants');break
                time.sleep(min(.01,max(0,settle_end-now)))
            else:time.sleep(min(.02,max(0,deadline-now)))
    except BaseException as e:
        r.update(status='FAIL' if job and job.resumed else 'BLOCKED',reason=repr(e))
        if isinstance(e,TimeoutError):r.update(status='FAIL',reason='timeout',timed_out=True)
    finally:
        if job:
            r['cleanup']=job.cleanup(4)
            try:
                r['owned_processes']=job.records()
                if job.launched:r['exit_code']=job.root_exit()
            except BaseException:r['cleanup']['confirmed']=False
            r['handle_close_errors']=job.close()
            if r['handle_close_errors']:r['cleanup']['confirmed']=False
        for d in drains:d.thread.join(1)
        for i,stream in enumerate(('stdout','stderr')):
            if i<len(drains):
                d=drains[i];r[stream]=d.text();r[stream+'_bytes']=d.total;r[stream+'_truncated']=d.total>len(d.data)
                if d.thread.is_alive() or d.error:r['cleanup']['confirmed']=False
        if not r['cleanup']['confirmed']:r.update(status='FAIL',reason='cleanup_unconfirmed')
        completion=time.monotonic();r['completion_observed_seconds']=completion-start;r['deadline_seconds']=timeout
        if completion>=deadline:r['timed_out']=True
        if r['status']=='PASS':
            final=_terminal_decision(now=completion,deadline=deadline,owner_alive=True,stopped=bool(stop_event and stop_event.is_set()),exit_code=r['exit_code'],active=0)
            r.update(status=final[0],reason=final[1],timed_out=r['timed_out'] or final[2])
        r['seconds']=completion-start
    return r
