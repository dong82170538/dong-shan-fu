"""Real active Engine process locking and forced-owner termination."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from commerce_core.store import Store
from tests.support import ROOT,offline_env,wait_until

class EngineProcessTests(unittest.TestCase):
    def test_live_engine_lock_contention_kill_and_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'core.sqlite3';ready=Path(folder)/'owner-ready.json'
            code='''import json,os,sys,threading
from pathlib import Path
from commerce_core.store import Store
from commerce_core.engine import Engine
from commerce_core.domain import evaluate
from tests.support import fixture,product,POLICY,wait_until
class Controlled:
 def run(self,raw,policy,attempt):
  if raw['external_id']=='p2':threading.Event().wait(60)
  return evaluate(raw,policy)
s=Store(sys.argv[1]);f=s.import_fixture(fixture(product('p1'),product('p2')));t=s.submit(f['id'],POLICY,'active-owner')['id'];e=Engine(s,1,Controlled()).start()
wait_until(lambda:s.task(t)['counts']['succeeded']==1 and s.task(t)['counts']['running']==1)
p=Path(sys.argv[2]);temp=p.with_suffix('.tmp');temp.write_text(json.dumps({'pid':os.getpid(),'database':s.path,'task':t,'engine':e.status(),'state':s.task(t)}));temp.replace(p)
threading.Event().wait(60)
'''
            owner=subprocess.Popen([sys.executable,'-c',code,str(db),str(ready)],cwd=ROOT,env=offline_env(),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            try:
                wait_until(ready.is_file,timeout=15)
                initial=json.loads(ready.read_text());self.assertEqual(initial['pid'],owner.pid)
                self.assertEqual(initial['engine']['health'],'ready')
                competitor='''import json,os,sys
from commerce_core.store import Store
from commerce_core.engine import Engine
s=Store(sys.argv[1])
try:Engine(s).start()
except RuntimeError:print(json.dumps({'pid':os.getpid(),'database':s.path,'lock':'denied_owner_active','task':s.task(sys.argv[2])}))
else:raise AssertionError('Second live engine acquired lock')
'''
                result=subprocess.run([sys.executable,'-c',competitor,str(db),initial['task']],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr);contender=json.loads(result.stdout)
                self.assertNotEqual(contender['pid'],owner.pid)
                self.assertEqual(contender['task']['state'],'running')
                self.assertEqual(contender['task']['counts']['succeeded'],1)
                owner.kill();owner.wait(timeout=10)
                recovery='''import json,os,sys
from commerce_core.store import Store
from commerce_core.engine import Engine
from tests.support import wait_until
s=Store(sys.argv[1]);e=Engine(s,1).start()
try:
 before=s.task(sys.argv[2]);assert before['state']=='interrupted';s.control(sys.argv[2],'resume');wait_until(lambda:s.task_state(sys.argv[2])=='completed')
 print(json.dumps({'pid':os.getpid(),'database':s.path,'before':before,'after':s.task(sys.argv[2]),'engine':e.status()}))
finally:e.stop()
'''
                result=subprocess.run([sys.executable,'-c',recovery,str(db),initial['task']],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr);recovered=json.loads(result.stdout)
                self.assertEqual(recovered['before']['counts']['succeeded'],1)
                self.assertEqual(recovered['before']['counts']['pending'],1)
                self.assertEqual(recovered['after']['counts']['succeeded'],2)
                self.assertEqual([i['attempt'] for i in recovered['after']['items']],[1,2])
                self.assertEqual(recovered['after']['counts']['published'],0)
                self.assertEqual(initial['database'],contender['database']);self.assertEqual(initial['database'],recovered['database'])
                store=Store(db)
                with store.connect() as connection:
                    self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                    self.assertEqual(connection.execute('SELECT count(*) FROM products').fetchone()[0],2)
                if os.environ.get('COMMERCE_EVIDENCE_DIR'):
                    target=Path(os.environ['COMMERCE_EVIDENCE_DIR'])/'test_live_engine_lock_contention_kill_and_recovery';target.mkdir(parents=True,exist_ok=True)
                    (target/'evidence.json').write_text(json.dumps({'owner':initial,'contender':contender,'recovery':recovered},indent=2))
                    dest=sqlite3.connect(target/'core.sqlite3')
                    try:
                        with store.connect() as source:source.backup(dest)
                    finally:dest.close()
            finally:
                if owner.poll() is None:owner.kill();owner.wait(timeout=10)
                owner.stderr.close()
