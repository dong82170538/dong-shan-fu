"""Real integrated Windows Job/Node supervision regressions, not component-only."""
import hashlib,json,os,pathlib,shutil,subprocess,sys,threading,time,unittest,uuid
from tests.support import temporary_directory,ROOT,offline_env,wait_until
sys.path.insert(0,str(ROOT/'scripts'))
from owned_process import run_owned,OwnershipUnsupported

@unittest.skipUnless(sys.platform=='win32','Windows Job regressions; POSIX has separate pidfd tests')
class WindowsSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=temporary_directory(prefix='dsf-windows-job-');self.folder=pathlib.Path(self.temp.name);self.nonce=uuid.uuid4().hex
    def tearDown(self):self.temp.cleanup()
    def env(self):return dict(offline_env(),DSF_PYTHON=sys.executable,DSF_HEARTBEAT_DIR=str(self.folder),DSF_HEARTBEAT_NONCE=self.nonce)
    def tree(self,mode='wait'):return [sys.executable,'-B',str(ROOT/'tests/process_fixtures/heartbeat_tree.py'),'--folder',str(self.folder),'--nonce',self.nonce,'--mode',mode]
    def node(self,ms):
        code="const {runTests}=require('./desktop/scripts/test.cjs');const r=runTests({files:[require('node:path').resolve('tests/process_fixtures/hanging-node.cjs')],timeoutMs:"+str(ms)+",out:"+json.dumps(str(self.folder/'node-reports'))+"});process.exitCode=r.status==='PASS'?0:1;"
        return ['node','-e',code]
    def proof(self,result):
        import msvcrt,_winjob as N
        self.assertTrue(result['cleanup']['confirmed'],result);self.assertEqual(result['cleanup']['remaining_pids'],[])
        known={(p['pid'],p['created_100ns']) for p in result['owned_processes']}
        rows=[];hearts={};locks=[]
        for role in ('root','child','leaf'):
            row=json.loads((self.folder/(role+'.json')).read_text(encoding='utf-8'));self.assertEqual(row['nonce'],self.nonce)
            self.assertIn((row['pid'],row['created_100ns']),known)
            h=N.K.OpenProcess(0x100000|0x1000,False,row['pid'])
            if h:
                try:
                    now=N.identity(h);self.assertTrue(now['created_100ns']!=row['created_100ns'] or not N.live(h))
                finally:N.close(h)
            else:self.assertEqual(N.C.get_last_error(),87)
            with (self.folder/(role+'.lock')).open('r+b') as lock:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            beat=self.folder/(role+'.heartbeat');self.assertGreater(beat.stat().st_size,0);hearts[role]=hashlib.sha256(beat.read_bytes()).hexdigest();rows.append(row);locks.append(role)
        time.sleep(.08)
        self.assertEqual(hearts,{role:hashlib.sha256((self.folder/(role+'.heartbeat')).read_bytes()).hexdigest() for role in hearts})
        return dict(processes=rows,locks_reacquired=locks,heartbeat_sha256=hearts,nonempty_heartbeats=True,all_owned_exited=True)
    def save(self,name,record):
        out=pathlib.Path(os.environ.get('COMMERCE_EVIDENCE_DIR',ROOT/'artifacts'/'test-evidence'))/name;out.mkdir(parents=True,exist_ok=True)
        for p in self.folder.iterdir():
            if p.is_file():shutil.copyfile(p,out/p.name)
        (out/'evidence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    def reusable(self):
        r=run_owned([sys.executable,'-B','-c','print("integrated_normal_after_cleanup")'],cwd=ROOT,env=self.env(),timeout=4)
        self.assertEqual(r['status'],'PASS',r);self.assertEqual(r['stdout'].strip(),'integrated_normal_after_cleanup')
    def test_windows_outer_timeout_cleans_real_grandchildren(self):
        r=run_owned(self.tree(),cwd=ROOT,env=self.env(),timeout=2)
        self.assertEqual((r['status'],r['reason'],r['timed_out']),('FAIL','timeout',True));proof=self.proof(r);self.reusable();self.save('windows-outer-timeout',dict(supervision=r,proof=proof))
    def test_windows_abnormal_runner_exit_cleans_live_descendants(self):
        r=run_owned(self.tree('abnormal'),cwd=ROOT,env=self.env(),timeout=5)
        self.assertEqual((r['status'],r['exit_code']),('FAIL',23));proof=self.proof(r);self.reusable();self.save('windows-runner-abnormal',dict(supervision=r,proof=proof))
    def test_windows_zero_exit_with_live_descendants_stays_fail(self):
        r=run_owned(self.tree('zero_orphan'),cwd=ROOT,env=self.env(),timeout=5)
        self.assertEqual((r['status'],r['reason']),('FAIL','root_exited_with_live_descendants'));proof=self.proof(r);self.reusable();self.save('windows-zero-orphan',dict(supervision=r,proof=proof))
    def test_windows_stop_event_cleans_real_tree(self):
        stop=threading.Event();timer=threading.Timer(1.5,stop.set);timer.start()
        try:r=run_owned(self.tree(),cwd=ROOT,env=self.env(),timeout=6,stop_event=stop)
        finally:timer.cancel()
        self.assertEqual((r['status'],r['reason']),('FAIL','stopped'));proof=self.proof(r);self.reusable();self.save('windows-stop-event',dict(supervision=r,proof=proof))
    def test_windows_unrelated_preexisting_child_is_untouched(self):
        child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(20)'],stdin=subprocess.DEVNULL)
        try:
            r=run_owned([sys.executable,'-B','-c','print("owned only")'],cwd=ROOT,env=self.env(),timeout=4)
            self.assertEqual(r['status'],'PASS',r);self.assertIsNone(child.poll());self.assertNotIn(child.pid,[x['pid'] for x in r['owned_processes']])
            self.save('windows-existing-child-untouched',dict(supervision=r,existing_test_child_pid=child.pid,existing_child_still_alive=True))
        finally:
            child.kill();child.wait(timeout=3)
    def test_windows_inner_node_timeout_is_not_masked(self):
        p=subprocess.Popen(self.node(2200),cwd=ROOT,env=self.env(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:out,err=p.communicate(timeout=12)
        finally:
            if p.poll() is None:p.kill();p.wait(timeout=3)
        self.assertNotEqual(p.returncode,0)
        inner=json.loads((self.folder/'node-reports'/'desktop-process-ownership.json').read_text(encoding='utf-8'))
        self.assertEqual((inner['status'],inner['reason']),('FAIL','timeout'));proof=self.proof(inner);self.reusable();self.save('windows-node-inner-timeout',dict(inner=inner,proof=proof,node_exit=p.returncode))
    def test_windows_outer_timeout_during_inner_node_job(self):
        from verification_guard_windows import run_verification_owned
        r=run_verification_owned(self.node(10000),cwd=ROOT,env=self.env(),timeout=3)
        self.assertEqual((r['status'],r['reason']),('FAIL','timeout'));proof=self.proof(r);self.reusable();self.save('windows-outer-during-inner-node',dict(outer=r,proof=proof))
    def test_windows_node_owner_death_cleans_inner_job(self):
        from verification_guard_windows import run_verification_owned
        report=self.folder/'node-reports'/'desktop-process-ownership.json'
        script='''import pathlib,subprocess,time
p=subprocess.Popen(COMMAND)
try:
 end=time.monotonic()+6
 while not pathlib.Path(READY).exists():
  if time.monotonic()>end:raise TimeoutError('fixture not ready')
  time.sleep(.02)
 p.kill();p.wait(timeout=3)
 while not pathlib.Path(REPORT).exists():
  if time.monotonic()>end+4:raise TimeoutError('owner cleanup report not available')
  time.sleep(.02)
finally:
 if p.poll() is None:p.kill();p.wait(timeout=3)
'''.replace('COMMAND',repr(self.node(10000))).replace('READY',repr(str(self.folder/'ready'))).replace('REPORT',repr(str(report)))
        r=run_verification_owned([sys.executable,'-B','-c',script],cwd=ROOT,env=self.env(),timeout=12)
        self.assertEqual(r['status'],'PASS',r)
        inner=json.loads(report.read_text(encoding='utf-8'));self.assertEqual((inner['status'],inner['reason']),('FAIL','owner_exited'))
        proof=self.proof(inner);self.reusable();self.save('windows-node-owner-death',dict(inner=inner,outer=r,proof=proof))
    def test_windows_nested_public_wrapper_refuses_without_command(self):
        marker=self.folder/'must-not-run';report=self.folder/'nested.json'
        command=[sys.executable,'-B',str(ROOT/'scripts'/'owned_process.py'),'--timeout','3','--report',str(report),'--cwd',str(ROOT),'--owner-pid','PARENT','--',sys.executable,'-B','-c','import pathlib;pathlib.Path('+repr(str(marker))+').write_text("bad")']
        code='import os,subprocess;cmd='+repr(command)+';cmd[cmd.index("PARENT")]=str(os.getpid());p=subprocess.run(cmd,timeout=7);raise SystemExit(0 if p.returncode==2 else 1)'
        r=run_owned([sys.executable,'-B','-c',code],cwd=ROOT,env=self.env(),timeout=10)
        self.assertEqual(r['status'],'PASS',r);self.assertFalse(marker.exists());inner=json.loads(report.read_text(encoding='utf-8'));self.assertEqual(inner['status'],'BLOCKED');self.assertIsNone(inner['runner_pid']);self.save('windows-nested-wrapper-refused',dict(outer=r,inner=inner,command_started=False))

    def test_windows_text_pipe_graceful_shutdown_releases_database_lock(self):
        import re,msvcrt,urllib.request
        from process_tools import startup_line
        db=self.folder/'stdio.sqlite3'
        p=subprocess.Popen([sys.executable,'-B','-X','utf8','-m','commerce_core','--db',str(db),'serve','--port','0','--stdio-control'],cwd=ROOT,env=self.env(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        startup=None;success=False;forced=False;startup_error=None;stderr=None
        try:
            line=startup_line(p);startup=line;match=re.match(r'Fixture-only UI: (http://127\.0\.0\.1:\d+)',line);self.assertIsNotNone(match,line)
            with urllib.request.urlopen(match.group(1)+'/api/health',timeout=3) as response:
                status=response.status;health=json.load(response)
            self.assertEqual(status,200);self.assertEqual(health['health'],'ready')
            # Windows TextIO writes CRLF; no terminate/kill on the success path.
            p.stdin.write('DSF_SHUTDOWN\n');p.stdin.flush();p.stdin.close();p.wait(timeout=6)
            stderr=p.stderr.read(32768);self.assertEqual(p.returncode,0,stderr)
            with pathlib.Path(str(db)+'.lock').open('r+b') as lock:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            success=True
            self.save('windows-text-stdio-stop',{'status':'PASS','pid':p.pid,'exit_code':p.returncode,'health_http_status':status,'health':health,'database':str(db),'database_lock_reacquired':True,'forced_termination':False,'startup_line':startup,'reader_encoding':p.stdout.encoding,'parent_utf8_mode':sys.flags.utf8_mode})
        except BaseException as problem:
            startup_error=type(problem).__name__+':'+str(problem);raise
        finally:
            if p.poll() is None:forced=True;p.kill();p.wait(timeout=3)
            if not success:
                self.save('windows-text-stdio-stop-failure',{'status':'FAIL','pid':p.pid,'exit_code':p.returncode,'startup_line':startup,'reader_encoding':p.stdout.encoding,'parent_utf8_mode':sys.flags.utf8_mode,'error':startup_error,'stderr':stderr if stderr is not None else p.stderr.read(32768),'forced_cleanup':forced})
            for stream in (p.stdin,p.stdout,p.stderr):
                if stream and not stream.closed:stream.close()


    def test_windows_crlf_reader_is_utf8_even_when_parent_locale_is_cp936(self):
        evidence=self.folder/'locale-evidence'
        code='import unittest;test=unittest.defaultTestLoader.loadTestsFromName("tests.test_windows_supervision.WindowsSupervisionTests.test_windows_text_pipe_graceful_shutdown_releases_database_lock");result=unittest.TextTestRunner(verbosity=2).run(test);raise SystemExit(0 if result.wasSuccessful() else 1)'
        env=dict(self.env(),PYTHONUTF8='0',COMMERCE_EVIDENCE_DIR=str(evidence))
        result=run_owned([sys.executable,'-B','-X','utf8=0','-c',code],cwd=ROOT,env=env,timeout=12)
        self.assertEqual(result['status'],'PASS',result)
        proof=json.loads((evidence/'windows-text-stdio-stop/evidence.json').read_text(encoding='utf-8'))
        self.assertEqual(proof['parent_utf8_mode'],0);self.assertEqual(proof['reader_encoding'],'utf-8');self.assertEqual(proof['exit_code'],0);self.assertTrue(proof['database_lock_reacquired']);self.assertFalse(proof['forced_termination'])
        self.save('windows-cp936-parent-crlf-proof',{'supervision':result,'stdio_test':proof})
