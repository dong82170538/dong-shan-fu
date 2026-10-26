from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from tests.offline_guard import sitecustomize
from tests.support import ROOT, POLICY, product, fixture, ManualClock, DeferredAdapter, wait_until, offline_env
from commerce_core.domain import ValidationError, evaluate, check_range, validate_policy
from commerce_core.engine import Engine
from commerce_core.store import Store, AuthRequired, ConflictError, SCHEMA
from commerce_core.money import MoneyLedger
from commerce_core.sessions import SessionGuard
from commerce_core.scheduling import DailyScheduler, next_due
from commerce_core.transport import DeadlineRequest
from commerce_core.extension_rules import preview


class OfflineWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'core.sqlite3'
        self.clock=ManualClock();self.clock.now=1792889400
        self.store=Store(self.path,clock=self.clock)
        self.engines=[];self.adapters=[];self.evidence=[]

    def tearDown(self):
        for a in self.adapters:a.release.set()
        for e in self.engines:e.stop()
        if os.environ.get('COMMERCE_EVIDENCE_DIR'):
            folder=Path(os.environ['COMMERCE_EVIDENCE_DIR'])/self._testMethodName
            folder.mkdir(parents=True,exist_ok=True)
            destination=sqlite3.connect(folder/'core.sqlite3')
            try:
                with self.store.connect() as source:source.backup(destination)
            finally:destination.close()
            (folder/'evidence.json').write_text(json.dumps({'database':str(self.path),'checkpoints':self.evidence,'tasks':self.store.list_tasks(),'money':MoneyLedger(self.store).list(),'daily_schedules':DailyScheduler(self.store).list()},ensure_ascii=False,indent=2))
        self.tmp.cleanup()

    def imported(self,*products):
        return self.store.import_fixture(fixture(*products))['id']

    def engine(self,**options):
        e=Engine(self.store,clock=self.clock,**options).start();self.engines.append(e);return e

    def test_TS05_request_deadline_separate_from_item_deadline(self):
        future=Future()
        request=DeadlineRequest(future,clock=self.clock)
        item_deadline=self.clock()+120
        self.clock.advance(9.999)
        self.assertEqual(request.poll()[0],'pending')
        self.clock.advance(.001)
        self.assertEqual(request.poll()[0],'timed_out')
        self.assertLess(self.clock(),item_deadline)
        self.clock.advance(.001);future.set_result({'code':0,'data':'late synthetic response'})
        self.clock.advance(110)
        self.assertEqual(request.poll(),('timed_out',None))
        self.assertEqual(request.events,['http_timeout'])
        self.assertEqual(request.attempts,1)
        self.evidence.append({'request_state':request.state,'item_deadline':item_deadline,'request_events':request.events,'attempts':request.attempts,'transport':'injected Future, no merchant HTTP'})

    def test_request_completed_on_time_remains_success_after_slow_poll(self):
        f=Future();r=DeadlineRequest(f,clock=self.clock)
        self.clock.advance(1);f.set_result({'code':0})
        self.clock.advance(100)
        self.assertEqual(r.poll(),('completed',{'code':0}))

    def test_request_cancel_and_poll_have_one_terminal_state(self):
        entered=threading.Event();release=threading.Event();cancel_started=threading.Event()
        class PausingFuture(Future):
            def done(self):
                entered.set()
                if not release.wait(2):raise AssertionError('Test did not release poll')
                return super().done()
        f=PausingFuture();request=DeadlineRequest(f,clock=self.clock)
        f.set_result({'code':0})
        results={}
        poller=threading.Thread(target=lambda:results.update(poll=request.poll()))
        def cancel():
            cancel_started.set();results['cancel']=request.cancel()
        canceler=threading.Thread(target=cancel)
        poller.start();self.assertTrue(entered.wait(2))
        canceler.start();self.assertTrue(cancel_started.wait(2));release.set()
        poller.join(2);canceler.join(2)
        self.assertFalse(poller.is_alive());self.assertFalse(canceler.is_alive())
        if results['cancel']=='canceled':self.assertEqual(request.state,'canceled')
        else:self.assertEqual((results['cancel'],request.state),('completed','completed'))
        pending=DeadlineRequest(Future(),clock=self.clock)
        self.assertEqual(pending.cancel(),'canceled')
        self.assertEqual(pending.poll(),('canceled',None))

    def test_request_blocked_observer_cannot_suspend_hard_deadline(self):
        entered=threading.Event();release=threading.Event();future=Future()
        def observer(_):
            entered.set()
            if not release.wait(2):raise AssertionError('Test observer was not released')
        future.add_done_callback(observer)
        request=DeadlineRequest(future,clock=self.clock)
        worker=threading.Thread(target=lambda:future.set_result({'code':0}))
        worker.start();self.assertTrue(entered.wait(2))
        self.clock.advance(20)
        self.assertEqual(request.poll(),('timed_out',None))
        release.set();worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(request.poll(),('timed_out',None))
        self.assertEqual(request.events,['http_timeout'])

    def test_TS06_bounded_auto_retry_and_cancel_during_backoff(self):
        class FirstDeferred(DeferredAdapter):
            def run(self,raw,policy,attempt):
                if attempt==1:return super().run(raw,policy,attempt)
                with self.lock:self.calls+=1
                return evaluate(raw,policy)
        adapter=FirstDeferred();self.adapters.append(adapter)
        fid=self.imported(product())
        retry={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500}
        task=self.store.submit(fid,POLICY,'auto',10000,retry_policy=retry)['id']
        engine=self.engine(concurrency=1,adapter=adapter)
        self.assertTrue(adapter.started.wait(2))
        self.clock.advance(10)
        wait_until(lambda:self.store.task(task)['counts']['retry_wait']==1)
        adapter.release.set();wait_until(lambda:not engine.active)
        self.clock.advance(.499)
        calls=self.clock.calls;wait_until(lambda:self.clock.calls>calls+2)
        self.assertEqual(adapter.calls,1)
        self.clock.advance(.002)
        wait_until(lambda:self.store.task(task)['state']=='completed')
        result=self.store.task(task)
        self.assertEqual(result['counts']['succeeded'],1)
        self.assertEqual(result['items'][0]['attempt'],2)
        self.assertEqual(adapter.calls,2)
        self.evidence.append({'normal':result})
        engine.stop()
        cancel_adapter=FirstDeferred();self.adapters.append(cancel_adapter)
        canceled=self.store.submit(fid,POLICY,'cancel_backoff',10000,retry_policy=retry)['id']
        cancel_engine=self.engine(concurrency=1,adapter=cancel_adapter)
        self.assertTrue(cancel_adapter.started.wait(2))
        self.clock.advance(10)
        wait_until(lambda:self.store.task(canceled)['counts']['retry_wait']==1)
        self.store.control(canceled,'cancel')
        self.clock.advance(1);cancel_adapter.release.set()
        wait_until(lambda:not cancel_engine.active)
        self.assertEqual(self.store.task(canceled)['state'],'canceled')
        self.assertEqual(self.store.task(canceled)['items'][0]['attempt'],1)
        self.assertEqual(cancel_adapter.calls,1)
        self.evidence.append({'canceled':self.store.task(canceled)})

    def test_automatic_retry_exhaustion_is_bounded(self):
        fid=self.imported(product(fail_attempts=10))
        task=self.store.submit(fid,POLICY,'max2',retry_policy={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500})['id']
        self.engine()
        wait_until(lambda:self.store.task(task)['counts']['retry_wait']==1)
        self.clock.advance(.5)
        wait_until(lambda:self.store.task(task)['state']=='completed_with_errors')
        self.assertEqual(self.store.task(task)['items'][0]['attempt'],2)
        self.assertRaises(ConflictError,self.store.control,task,'retry')

    def test_automatic_save_failure_rolls_back_then_retries(self):
        task=self.store.submit(self.imported(product(save_fail_attempts=1)),POLICY,'save_auto',retry_policy={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500})['id']
        self.engine()
        wait_until(lambda:self.store.task(task)['counts']['retry_wait']==1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM skus').fetchone()[0],0)
        self.clock.advance(.5)
        wait_until(lambda:self.store.task(task)['state']=='completed')
        self.assertEqual(self.store.task(task)['counts']['succeeded'],1)
        self.assertEqual(self.store.task(task)['items'][0]['attempt'],2)

    def test_backoff_restart_is_interrupted_until_explicit_resume(self):
        task=self.store.submit(self.imported(product(fail_attempts=1)),POLICY,'restart_auto',retry_policy={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500})['id']
        e=self.engine();wait_until(lambda:self.store.task(task)['counts']['retry_wait']==1)
        e.stop();self.assertEqual(self.store.task(task)['state'],'interrupted')
        self.clock.advance(1)
        second=self.engine();calls=self.clock.calls;wait_until(lambda:self.clock.calls>calls+2)
        self.assertEqual(self.store.task(task)['items'][0]['attempt'],1)
        self.store.control(task,'resume')
        wait_until(lambda:self.store.task(task)['state']=='completed')
        self.assertEqual(self.store.task(task)['items'][0]['attempt'],2)

    def test_auth_expiry_cancels_pending_automatic_retry(self):
        guard=SessionGuard(self.store);account='fixture-retry'
        session=guard.fake_login(account,'test_fixture')
        task=self.store.submit(self.imported(product(fail_attempts=10)),POLICY,'auth_retry',account_id=account,retry_policy={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500})['id']
        self.engine();wait_until(lambda:self.store.task(task)['counts']['retry_wait']==1)
        guard.response(account,session['epoch'],402)
        self.clock.advance(1);calls=self.clock.calls;wait_until(lambda:self.clock.calls>calls+2)
        result=self.store.task(task)
        self.assertEqual(result['state'],'canceled')
        self.assertEqual(result['items'][0]['attempt'],1)

    def test_logged_out_or_expired_account_cannot_retry_old_failed_task(self):
        guard=SessionGuard(self.store)
        fid=self.imported(product(fail_attempts=1))
        self.engine()
        for mode in ('logout','expire'):
            account='fixture-'+mode;session=guard.fake_login(account,'test_fixture')
            task=self.store.submit(fid,POLICY,'old_failure',account_id=account)['id']
            wait_until(lambda:self.store.task(task)['state']=='completed_with_errors')
            if mode=='logout':guard.logout(account)
            else:guard.response(account,session['epoch'],401)
            self.assertRaises(AuthRequired,self.store.control,task,'retry')
            self.assertEqual(self.store.task(task)['items'][0]['attempt'],1)
            guard.fake_login(account,'test_fixture')
            self.store.control(task,'retry')
            wait_until(lambda:self.store.task(task)['state']=='completed')
            self.assertEqual(self.store.task(task)['items'][0]['attempt'],2)

    def test_TS12_context_money_persisted_and_untagged_rejected(self):
        ledger=MoneyLedger(self.store)
        for context,amount,currency in [('auto_selection','100.00','CNY'),('inspection','1000.00','RUB')]:
            ledger.save({'source':'test_fixture','context':context,'resMoney':{'amount':amount,'currency':currency,'context':context}})
        self.assertRaises(ValidationError,ledger.save,{'source':'test_fixture','context':'auto_selection','resMoney':{'amount':'100.00','context':'auto_selection'}})
        code='import os,json,sys;from commerce_core.store import Store;from commerce_core.money import MoneyLedger;s=Store(sys.argv[1]);print(json.dumps({"pid":os.getpid(),"db":s.path,"records":MoneyLedger(s).list()}))'
        child=subprocess.run([sys.executable,'-c',code,str(self.path)],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        data=json.loads(child.stdout);self.assertNotEqual(data['pid'],os.getpid())
        self.assertEqual(len(data['records']),2)
        display={r['context']:r['currency']+' '+r['amount'] for r in data['records']}
        self.assertEqual(display,{'auto_selection':'CNY 100.00','inspection':'RUB 1000.00'})
        self.evidence.append(data)

    def test_TS15_session_epoch_expires_and_cancels_only_its_account(self):
        guard=SessionGuard(self.store);account='fixture-A'
        session=guard.fake_login(account,'test_fixture')
        fid=self.imported(product())
        task=self.store.submit(fid,POLICY,'selection',account_id=account)['id']
        inspection=self.store.submit(fid,POLICY,'inspection',account_id=account,kind='inspection_fixture')['id']
        other=self.store.submit(fid,POLICY,'other',account_id='fixture-B')['id']
        self.store.schedule(fid,POLICY,self.clock()+100,account)
        DailyScheduler(self.store).create(fid,POLICY,'Asia/Shanghai','09:00',account)
        adapter=DeferredAdapter();self.adapters.append(adapter)
        engine=self.engine(adapter=adapter,concurrency=2)
        wait_until(lambda:sum(self.store.task(t)['counts']['running'] for t in (task,inspection))==2)
        expired=guard.response(account,session['epoch'],401)
        self.assertFalse(expired['new_work_allowed'])
        ignored=guard.response(account,session['epoch'],0)
        self.assertTrue(ignored['ignored'])
        self.assertEqual(guard.state(account)['state'],'expired')
        self.assertRaises(AuthRequired,self.store.submit,fid,POLICY,'blocked',account_id=account)
        adapter.release.set();wait_until(lambda:not engine.active and self.store.task(other)['state']=='completed')
        for ident in (task,inspection):
            result=self.store.task(ident)
            self.assertEqual(result['state'],'canceled')
            self.assertEqual(result['counts']['succeeded'],0)
        self.assertEqual(self.store.list_schedules()[0]['state'],'canceled')
        self.assertEqual(DailyScheduler(self.store).list()[0]['state'],'canceled')
        logout=guard.logout(account,remote_confirmed=False)
        self.assertEqual(logout['remote_logout'],'unconfirmed')
        self.assertFalse(logout['local_credentials_present'])
        self.assertRaises(AuthRequired,guard.require,account)
        self.assertRaises(AuthRequired,self.store.submit,fid,POLICY,'still_blocked',account_id=account)
        self.evidence.append({'session_before':session,'expired':expired,'late_response':ignored,'logout':logout,'selection':self.store.task(task),'inspection':self.store.task(inspection)})
        # Only an explicit new fixture authentication can reopen the local guard.
        fresh=guard.fake_login(account,'test_fixture')
        self.assertGreater(fresh['epoch'],session['epoch'])
        self.assertEqual(guard.require(account)['state'],'authenticated_fixture')

    def test_TS17_daily_missed_occurrences_durable_across_two_restarts(self):
        stopped=datetime.fromisoformat('2026-10-24T00:50:00+00:00').timestamp()
        restarted=datetime.fromisoformat('2026-10-25T01:05:00+00:00').timestamp()
        self.clock.now=stopped
        ident=DailyScheduler(self.store).create(self.imported(product()),POLICY,'Asia/Shanghai','09:00')['id']
        code='''import json,os,sys
from commerce_core.store import Store
from commerce_core.scheduling import DailyScheduler
s=Store(sys.argv[1],clock=lambda:float(sys.argv[2]));s.recover();print(json.dumps({'pid':os.getpid(),'db':s.path,'schedules':DailyScheduler(s).list(),'tasks':s.list_tasks()}))
'''
        outputs=[]
        for _ in range(2):
            child=subprocess.run([sys.executable,'-c',code,str(self.path),str(restarted)],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=10)
            self.assertEqual(child.returncode,0,child.stderr)
            outputs.append(json.loads(child.stdout))
        self.assertNotEqual(outputs[0]['pid'],outputs[1]['pid'])
        expected_next=datetime.fromisoformat('2026-10-26T01:00:00+00:00').timestamp()
        for data in outputs:
            schedule=data['schedules'][0]
            self.assertEqual(schedule['id'],ident)
            self.assertEqual(schedule['next_due'],expected_next)
            self.assertEqual([o['state'] for o in schedule['occurrences']],['missed','missed'])
            self.assertEqual(data['tasks'],[])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE code='schedule_missed'").fetchone()[0],2)
        self.evidence.extend(outputs)

    def test_daily_due_task_submits_once_and_completes(self):
        self.clock.now=datetime.fromisoformat('2026-10-24T00:59:50+00:00').timestamp()
        DailyScheduler(self.store).create(self.imported(product()),POLICY,'Asia/Shanghai','09:00')
        self.engine();self.clock.advance(10)
        wait_until(lambda:len(self.store.list_tasks())==1 and self.store.list_tasks()[0]['state']=='completed')
        with self.store.transaction() as db:DailyScheduler(self.store).tick(db)
        self.assertEqual(len(self.store.list_tasks()),1)
        self.assertEqual(DailyScheduler(self.store).list()[0]['occurrences'][0]['state'],'submitted')

    def test_TS19_extension_preview_validation_and_no_publish(self):
        s1={'goods_id':'s1','offer_id':'fixture-1','price':'100.00','old_price':'160.00','min_price':'50.00','currency':'RUB'}
        s2={**s1,'goods_id':'s2','min_price':'49.99'}
        def body(rows):return {'source':'test_fixture','candidate_origin':'https://fixture.invalid','publish':False,'selected_skus':rows}
        duplicate=preview(body([s1,{**s1,'goods_id':'s2'}]))
        minimum=preview(body([s2]))
        self.assertIn('duplicate_offer_id',[e['code'] for e in duplicate['errors']])
        self.assertIn('below_min_price',[e['code'] for e in minimum['errors']])
        valid=preview(body([s1]))
        self.assertEqual(valid['state'],'dry_run_preview')
        self.assertEqual(valid['skus'][0]['min_price'],'50.00')
        self.assertEqual(valid['publish_calls'],0)
        self.assertFalse(valid['host_integrated'])
        self.evidence.append({'duplicate':duplicate,'minimum':minimum,'valid':valid})

    def test_schema_v1_migration_preserves_task_and_idempotency(self):
        from commerce_core.domain import canonical,validate_policy
        path=Path(self.tmp.name)/'v1.sqlite3'
        db=sqlite3.connect(path)
        try:
            db.executescript(SCHEMA)
            f=fixture(product());p=validate_policy(POLICY)
            db.execute('INSERT INTO fixtures VALUES (?,?,?,?,?,?)',('legacy-fixture','Legacy test fixture',canonical(f),1,1,0))
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)',('legacy-task','manual/old-key','old-hash','legacy-fixture',canonical(p),'queued',0,2000,0,0))
            db.execute('INSERT INTO items(task_id,item_no,raw) VALUES (?,?,?)',('legacy-task',0,canonical(product())))
            db.commit()
        finally:db.close()
        migrated=Store(path)
        self.assertEqual(migrated.submit('legacy-fixture',POLICY,'old-key')['id'],'legacy-task')
        reopened=Store(path)
        self.assertEqual(reopened.submit('legacy-fixture',POLICY,'old-key')['id'],'legacy-task')
        self.assertEqual(reopened.task('legacy-task')['counts']['pending'],1)

    def test_concurrent_v1_migration_keeps_single_idempotency_namespace(self):
        from commerce_core.domain import canonical,validate_policy
        path=Path(self.tmp.name)/'concurrent-v1.sqlite3'
        db=sqlite3.connect(path)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            db.execute('INSERT INTO fixtures VALUES (?,?,?,?,?,?)',('legacy-fixture','Legacy test fixture',canonical(fixture(product())),1,1,0))
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)',('legacy-task','manual/old-key','old-hash','legacy-fixture',canonical(validate_policy(POLICY)),'queued',0,2000,0,0))
            db.execute('INSERT INTO items(task_id,item_no,raw) VALUES (?,?,?)',('legacy-task',0,canonical(product())))
            db.commit()
        finally:db.close()
        barrier=threading.Barrier(8)
        def open_and_replay(_):
            barrier.wait(timeout=5)
            return Store(path).submit('legacy-fixture',POLICY,'old-key')
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(open_and_replay,range(8)))
        self.assertTrue(all(r['id']=='legacy-task' and r['deduplicated'] for r in results))
        reopened=Store(path)
        self.assertEqual(len(reopened.list_tasks()),1)
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT dedupe_key FROM tasks').fetchone()[0],'manual/fixture-default/old-key')

    def test_max_physical_attempts_includes_crash_and_pause_restarts(self):
        fid=self.imported(product())
        retry={'mode':'automatic_fixture','max_attempts':2,'backoff_ms':500}
        for kind in ('crash','pause'):
            task=self.store.submit(fid,POLICY,'budget_'+kind,retry_policy=retry)['id']
            first=self.store.claim();self.assertEqual(first['attempt'],1)
            self.store.fail(first,'timed_out');self.clock.advance(.5)
            second=self.store.claim();self.assertEqual(second['attempt'],2)
            if kind=='crash':self.store.recover()
            else:self.store.control(task,'pause')
            result=self.store.control(task,'resume')
            self.assertEqual(result['state'],'completed_with_errors')
            self.assertIsNone(self.store.claim())
            self.assertEqual(self.store.task(task)['items'][0]['attempt'],2)
            self.assertEqual(self.store.task(task)['items'][0]['error'],'attempt_budget_exhausted')

    def test_fifo_is_stable_when_created_timestamps_are_equal(self):
        fid=self.imported(product())
        ids=[self.store.submit(fid,POLICY,'fifo'+str(i))['id'] for i in range(5)]
        claimed=[]
        for _ in ids:
            item=self.store.claim();claimed.append(item['task_id'])
            self.store.complete(item,evaluate(item['raw'],item['policy']))
        self.assertEqual(claimed,ids)

    def test_past_dst_gap_or_fold_does_not_block_valid_next_day(self):
        fall=datetime.fromisoformat('2026-11-01T08:00:00+00:00').timestamp()
        spring=datetime.fromisoformat('2026-03-08T08:00:00+00:00').timestamp()
        self.assertEqual(next_due(fall,'America/New_York','01:30'),datetime.fromisoformat('2026-11-02T06:30:00+00:00').timestamp())
        self.assertEqual(next_due(spring,'America/New_York','02:30'),datetime.fromisoformat('2026-03-09T06:30:00+00:00').timestamp())
        before_fold=datetime.fromisoformat('2026-11-01T04:00:00+00:00').timestamp()
        self.assertRaises(ValidationError,next_due,before_fold,'America/New_York','01:30')

    def test_TS07_observed_batch_return_defect_is_isolated_from_service(self):
        from tests.observed_legacy import observed_auto_save
        products=[f'b-product-{i:04d}' for i in range(1,1002)]
        results=[]
        for fails in (True,False):
            path=Path(self.tmp.name)/('legacy_failed.sqlite3' if fails else 'legacy_success.sqlite3')
            db=sqlite3.connect(path);db.execute('CREATE TABLE committed (id TEXT PRIMARY KEY)');db.commit()
            calls=[]
            def mock_save(batch):
                calls.append(len(batch))
                if fails and len(calls)==2:return {'code':500,'message':'FICTIONAL_SAVE_FAILURE'}
                before=db.total_changes
                with db:db.executemany('INSERT OR IGNORE INTO committed VALUES (?)',[(ident,) for ident in batch])
                return {'code':0,'data':{'newCount':db.total_changes-before}}
            try:
                returned=observed_auto_save(products,mock_save)
                committed=db.execute('SELECT count(*) FROM committed').fetchone()[0]
                self.assertEqual(calls,[1000,1])
                self.assertEqual((returned,committed),(0,1000) if fails else (1,1001))
                results.append({'second_batch_fails':fails,'function_return':returned,'actual_mock_commits':committed,'batch_sizes':calls})
                if os.environ.get('COMMERCE_EVIDENCE_DIR'):
                    folder=Path(os.environ['COMMERCE_EVIDENCE_DIR'])/self._testMethodName;folder.mkdir(parents=True,exist_ok=True)
                    dest=sqlite3.connect(folder/path.name)
                    try:db.backup(dest)
                    finally:dest.close()
            finally:db.close()
        self.evidence.append({'source':'parent static evidence interface.services.js:239-251','observed_legacy':results,'production_policy':'Correct cumulative committed counts; TS-08 verifies1001-item partial failure/retry','main_pushData_scope':'Usually single-item; no claim main chain suffered this multi-batch defect'})

    def test_TS11_legacy_currency_mapping_only_in_reference_profile(self):
        from tests.observed_legacy import observed_desktop_fx,observed_extension_fx
        rates={'CNY/RUB':'12','CNY/EUR':'0.125'}
        self.assertEqual(observed_desktop_fx('100','YN',rates),'1200.00')
        self.assertEqual(observed_desktop_fx('100','EUR',rates),'800.00')
        self.assertEqual(observed_extension_fx('100','¥','?',rates),-1)
        self.assertRaises(ValidationError,validate_policy,{'settlement_currency':'YN'})
        self.assertRaises(ValidationError,validate_policy,{'settlement_currency':'EUR'})
        actual=evaluate(product(),validate_policy(POLICY))['skus'][0]['price']
        self.assertEqual(actual['sale'],{'amount':'1080.00','currency':'RUB'})
        self.evidence.append({'source':'parent static evidence utils/index.js:34-78; extension bv','observed_legacy':{'YN':'1200.00','EUR':'800.00','unknown_symbol':-1},'financial_correctness_claim':False,'normal_product_contract_unchanged':True})

    def test_TS14_legacy_local_gate_compared_with_safe_session_epoch(self):
        from tests.observed_legacy import ObservedLocalGate
        legacy=ObservedLocalGate(disk_expired=True)
        after401=legacy.response(401)
        legacy.response(402)
        after0=legacy.response(0)
        self.assertEqual(after401,{'gate':True,'expired':True})
        self.assertEqual(after0,{'gate':False,'expired':False})
        self.assertEqual(legacy.stop_invocations,1)
        self.assertTrue(legacy.non_login_allowed())
        guard=SessionGuard(self.store);account='fixture-gate'
        session=guard.fake_login(account,'test_fixture')
        guard.response(account,session['epoch'],401)
        guard.response(account,session['epoch'],402)
        late=guard.response(account,session['epoch'],0)
        self.assertTrue(late['ignored']);self.assertEqual(guard.state(account)['state'],'expired')
        self.assertRaises(AuthRequired,guard.require,account)
        self.evidence.append({'source':'parent static evidence utils/request.js:15-50','observed_legacy':{'after401':after401,'after_late0':after0,'stop_invocations':legacy.stop_invocations,'non_login_local_gate_allows':legacy.non_login_allowed()},'new_service':{'late_response':late,'session':guard.state(account)},'server_authentication_bypass_claim':False})

    def test_legacy_reference_is_never_imported_by_production_service(self):
        code="import json,sys;import commerce_core.server;print(json.dumps([name for name in sys.modules if 'observed_legacy' in name]))"
        child=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(child.stdout),[])

    def test_TS20_inclusive_range_and_explicit_missing_profiles(self):
        p=product();p['selection']['sold_count']=150
        rejected=evaluate(p,validate_policy(POLICY))
        p['selection']['sold_count']=151
        accepted=evaluate(p,validate_policy(POLICY))
        self.assertEqual(rejected['accepted_skus'],0)
        self.assertEqual(accepted['accepted_skus'],1)
        self.assertTrue(check_range(None,0,1,profile='legacy_static_v1'))
        self.assertFalse(check_range('bad',0,1,profile='legacy_static_v1'))
        self.assertTrue(check_range('bad',None,None,profile='legacy_static_v1'))
        self.assertTrue(check_range('0',0,1,profile='legacy_static_v1'))
        self.assertTrue(check_range('1',0,1,profile='legacy_static_v1'))
        self.assertFalse(check_range(None,0,1,profile='strict_v1'))
        self.evidence.append({'profile':'legacy_static_v1','sold150':rejected['accepted_skus'],'sold151':accepted['accepted_skus'],'missing_0_to_1':check_range(None,0,1,profile='legacy_static_v1'),'nan_with_bounds':check_range('bad',0,1,profile='legacy_static_v1'),'both_bounds_null':check_range('bad',None,None,profile='legacy_static_v1')})
