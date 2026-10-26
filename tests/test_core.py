from tests.support import temporary_directory
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from tests.offline_guard import sitecustomize  # Install deny guard in parent, too.
from commerce_core.domain import ValidationError, evaluate, validate_fixture, validate_policy, quote
from commerce_core.engine import Engine
from commerce_core.store import Store, ConflictError
from commerce_core.server import csv_export
from tests.support import ROOT, POLICY, DeferredAdapter, ManualClock, fixture, product, wait_until, offline_env


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.db = Path(self.tmp.name) / 'core.sqlite3'
        self.store = Store(self.db)
        self.engines = []
        self.adapters = []
        self.checkpoints = []

    def tearDown(self):
        for adapter in self.adapters:
            adapter.release.set()
        for engine in self.engines:
            engine.stop()
        if os.environ.get('COMMERCE_EVIDENCE_DIR'):
            folder = Path(os.environ['COMMERCE_EVIDENCE_DIR']) / self._testMethodName
            folder.mkdir(parents=True, exist_ok=True)
            dest = sqlite3.connect(folder / 'core.sqlite3')
            try:
                with self.store.connect() as source:
                    source.backup(dest)
            finally:
                dest.close()
            evidence = {'test': self.id(), 'original_database_path': str(self.db),
                        'checkpoints': self.checkpoints, 'tasks': self.store.list_tasks(),
                        'exports': [self.store.export(t['id']) for t in self.store.list_tasks()]}
            (folder / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        self.tmp.cleanup()

    def record(self, name, value):
        self.checkpoints.append({'name': name, 'value': value})

    def submit(self, *products, policy=POLICY, key='test', timeout_ms=2000):
        f = self.store.import_fixture(fixture(*products))
        return self.store.submit(f['id'], policy, key, timeout_ms)['id']

    def engine(self, **kwargs):
        engine = Engine(self.store, **kwargs).start()
        self.engines.append(engine)
        return engine

    def deferred(self):
        a = DeferredAdapter()
        self.adapters.append(a)
        return a

    def done(self, task_id, timeout=10):
        wait_until(lambda: self.store.task_state(task_id) in {'completed', 'completed_with_errors', 'canceled'}, timeout=timeout)
        return self.store.task(task_id)

    def test_TS01_pipeline_second_process(self):
        pipeline = json.loads((ROOT / 'fixtures/pipeline.json').read_text())
        imported = self.store.import_fixture(pipeline)
        self.assertEqual((imported['raw_count'], imported['unique_count']), (3, 2))
        task = self.store.submit(imported['id'], POLICY, 'pipeline')['id']
        worker = subprocess.run([sys.executable, '-m', 'commerce_core', '--db', str(self.db), 'work'], cwd=ROOT, env=offline_env(), capture_output=True, text=True, timeout=20)
        self.assertEqual(worker.returncode, 0, worker.stderr)
        reader = subprocess.run([sys.executable, '-c', 'import os,json,sys; from commerce_core.store import Store; print(json.dumps({"pid":os.getpid(),"export":Store(sys.argv[1]).export(sys.argv[2])}))', str(self.db), task], cwd=ROOT, env=offline_env(), capture_output=True, text=True, timeout=10)
        self.assertEqual(reader.returncode, 0, reader.stderr)
        data = json.loads(reader.stdout)
        write_receipt = json.loads(worker.stdout)
        self.record('cross_process', {'writer_pid':write_receipt['process_id'], 'reader_pid':data['pid'], 'writer_database':write_receipt['database_path'], 'reader_database':str(self.db), 'counts':data['export']['task']['counts']})
        self.assertNotEqual(write_receipt['process_id'], data['pid'])
        self.assertNotEqual(os.getpid(), data['pid'])
        result = data['export']['task']
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['counts']['succeeded'], 1)
        self.assertEqual(result['counts']['filtered'], 1)
        self.assertEqual(data['export']['published'], 0)
        price = result['items'][0]['result']['skus'][0]['price']
        self.assertEqual(price['sale'], {'amount': '1080.00', 'currency': 'RUB'})
        self.assertEqual(price['strike'], {'amount': '1920.00', 'currency': 'RUB'})
        self.assertEqual(price['fee_quote']['provenance'], 'fictional_mock_not_fee_computation')
        self.assertEqual(result['items'][1]['result']['skus'][0]['reasons'], ['rejected_soldCount'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertIn(b'1080.00', csv_export(data['export']))

    def test_TS02_real_kill_restart_manual_resume(self):
        code = '''import json,os,sys,threading
from commerce_core.store import Store
from commerce_core.domain import evaluate
from tests.support import fixture,product,POLICY
s=Store(sys.argv[1]);f=s.import_fixture(fixture(product('p1'),product('p2')))
t=s.submit(f['id'],POLICY,'crash')['id'];c=s.claim();s.complete(c,evaluate(c['raw'],c['policy']));s.claim()
print(json.dumps({'pid':os.getpid(),'task':t}),flush=True)
threading.Event().wait(60)
'''
        p = subprocess.Popen([sys.executable, '-c', code, str(self.db)], cwd=ROOT, env=offline_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            first = json.loads(p.stdout.readline())
            p.kill()
            p.wait(timeout=10)
        finally:
            if p.poll() is None: p.kill(); p.wait()
            p.stdout.close(); p.stderr.close()
        second_code = '''import json,os,sys
from commerce_core.store import Store
from commerce_core.engine import Engine
s=Store(sys.argv[1]); e=Engine(s).start()
try: print(json.dumps({'pid':os.getpid(),'task':s.task(sys.argv[2])}))
finally: e.stop()
'''
        second = subprocess.run([sys.executable, '-c', second_code, str(self.db), first['task']], cwd=ROOT, env=offline_env(), capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 0, second.stderr)
        recovered = json.loads(second.stdout)
        self.assertNotEqual(first['pid'], recovered['pid'])
        task = recovered['task']
        self.record('forced_kill_readback', {'first_pid':first['pid'], 'second_pid':recovered['pid'], 'same_database':str(self.db), 'state':task['state'], 'counts':task['counts']})
        self.assertEqual(task['state'], 'interrupted')
        self.assertEqual(task['counts']['succeeded'], 1)
        self.assertEqual(task['counts']['pending'], 1)
        self.store.control(first['task'], 'resume')
        self.engine()
        result = self.done(first['task'])
        self.assertEqual(result['counts']['succeeded'], 2)
        self.assertEqual([i['attempt'] for i in result['items']], [1, 2])

    def test_TS03_cancel_late_result_cannot_commit(self):
        a = self.deferred()
        task = self.submit(product())
        self.engine(adapter=a)
        self.assertTrue(a.started.wait(2))
        self.store.control(task, 'cancel')
        a.release.set()
        wait_until(lambda: any(e['code'] == 'stale_result_ignored' for e in self.store.task(task)['events']))
        result = self.store.task(task)
        self.record('after_cancel_and_late_callback', result)
        self.assertEqual(result['state'], 'canceled')
        self.assertEqual(result['counts']['succeeded'], 0)
        self.assertEqual(result['counts']['canceled'], 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0], 0)
        self.assertRaises(ConflictError, self.store.control, task, 'retry')

    def test_TS04_concurrent_idempotency_restart_and_conflict(self):
        f = self.store.import_fixture(fixture())
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: self.store.submit(f['id'], POLICY, 'idem')['id'], range(16)))
        self.assertEqual(len(set(ids)), 1)
        code = '''import json,os,sys
from commerce_core.store import Store,ConflictError
from tests.support import POLICY
s=Store(sys.argv[1]); r=s.submit(sys.argv[2],POLICY,'idem')
try: s.submit(sys.argv[2],{**POLICY,'fx_rate':'13'},'idem')
except ConflictError as e: conflict=str(e)
else: raise AssertionError('Conflict was accepted')
print(json.dumps({'pid':os.getpid(),'db':s.path,'replay':r,'conflict':conflict}))
'''
        child = subprocess.run([sys.executable,'-c',code,str(self.db),f['id']],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        receipt = json.loads(child.stdout)
        self.assertNotEqual(receipt['pid'],os.getpid())
        self.assertEqual(receipt['db'],str(self.db))
        replay = receipt['replay']
        self.assertTrue(replay['deduplicated'])
        self.assertEqual(replay['id'], ids[0])
        with self.assertRaises(ConflictError) as conflict:
            self.store.submit(f['id'], {**POLICY, 'fx_rate': '13'}, 'idem')
        self.record('idempotency', {'writer_pid':os.getpid(),'concurrent_ids':ids, 'second_process':receipt, 'conflict':str(conflict.exception)})
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_concurrency_ceiling_including_timed_out_workers(self):
        a, clock = self.deferred(), ManualClock()
        task = self.submit(*[product(str(i)) for i in range(5)], timeout_ms=100)
        e = self.engine(adapter=a, concurrency=2, clock=clock)
        wait_until(lambda: a.calls == 2)
        clock.advance(1)
        wait_until(lambda: self.store.task(task)['counts']['timed_out'] == 2)
        self.assertEqual(a.calls, 2)  # Occupied physical workers retain slots.
        self.assertLessEqual(a.peak, 2)
        a.release.set()
        result = self.done(task)
        self.assertEqual(result['counts']['succeeded'], 3)
        self.assertEqual(result['counts']['timed_out'], 2)
        self.assertEqual(result['counts']['published'], 0)
        self.assertLessEqual(e.max_observed_workers, 2)

    def test_pause_fences_and_explicit_resume(self):
        a = self.deferred()
        task = self.submit(product())
        e = self.engine(adapter=a, concurrency=1)
        self.assertTrue(a.started.wait(2))
        self.store.control(task, 'pause')
        a.release.set()
        wait_until(lambda: not e.active)
        self.assertEqual(self.store.task(task)['state'], 'paused')
        self.assertEqual(self.store.task(task)['counts']['succeeded'], 0)
        self.store.control(task, 'resume')
        self.assertEqual(self.done(task)['counts']['succeeded'], 1)

    def test_timeout_virtual_clock_late_result_then_manual_retry(self):
        a, clock = self.deferred(), ManualClock()
        task = self.submit(product(), timeout_ms=10000)
        e = self.engine(adapter=a, clock=clock)
        self.assertTrue(a.started.wait(2))
        clock.advance(10.001)
        wait_until(lambda: self.store.task(task)['state'] == 'completed_with_errors')
        a.release.set()
        wait_until(lambda: not e.active)
        self.assertEqual(self.store.task(task)['counts']['succeeded'], 0)
        self.store.control(task, 'retry')
        result = self.done(task)
        self.assertEqual(result['counts']['succeeded'], 1)
        self.assertEqual(result['items'][0]['attempt'], 2)

    def test_TS08_1001_partial_save_failure_restart_retry_only_failed(self):
        products = [product(f'b-product-{i:04d}') for i in range(1,1002)]
        products[-1]['test_behavior'] = {'save_fail_attempts': 1}
        task = self.submit(*products)
        batch_started=time.monotonic()
        e = self.engine(concurrency=4)
        # This is durability/counting acceptance, not a disk throughput SLA.
        # Windows FULL-fsync writes progressed 704/1001 in the first 30s run.
        wait_budget=90 if os.name=='nt' else 30
        result = self.done(task,timeout=wait_budget)
        with self.store.connect() as connection:
            self.record('batch_durability_budget',{'seconds':time.monotonic()-batch_started,'wait_budget':wait_budget,'journal_mode':connection.execute('PRAGMA journal_mode').fetchone()[0],'synchronous':connection.execute('PRAGMA synchronous').fetchone()[0]})
        self.assertEqual((result['counts']['succeeded'], result['counts']['failed']), (1000, 1))
        self.record('first_save_attempt', {'counts':result['counts'], 'failed_item':result['items'][-1]})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0], 1000)
            self.assertEqual(db.execute('SELECT count(*) FROM skus').fetchone()[0], 1000)
            self.assertIsNotNone(db.execute("SELECT raw FROM items WHERE task_id=? AND state='failed'", (task,)).fetchone())
        e.stop()
        code = '''import json,os,sys
from commerce_core.store import Store
from commerce_core.engine import Engine
from tests.support import wait_until
s=Store(sys.argv[1]); task=sys.argv[2]; before=s.task(task)['counts'];s.control(task,'retry');e=Engine(s,4).start()
try:
 wait_until(lambda:s.task(task)['state']=='completed');r=s.task(task)
 print(json.dumps({'pid':os.getpid(),'database':s.path,'before':before,'after':r['counts'],'retried':[i['item_no'] for i in r['items'] if i['attempt']==2]}))
finally:e.stop()
'''
        child = subprocess.run([sys.executable,'-c',code,str(self.db),task],cwd=ROOT,env=offline_env(),capture_output=True,text=True,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr)
        receipt = json.loads(child.stdout)
        self.assertNotEqual(receipt['pid'],os.getpid())
        self.assertEqual(receipt['database'],str(self.db))
        self.record('second_process_retry',receipt)
        result = self.store.task(task)
        self.assertEqual(result['counts']['succeeded'], 1001)
        self.assertEqual([r['item_no'] for r in result['items'] if r['attempt'] == 2], [1000])
        self.record('after_failed_item_retry', {'counts':result['counts'], 'retried_items':[i['item_no'] for i in result['items'] if i['attempt'] == 2]})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0], 1001)

    def test_TS09_counts_filtered_failed_committed_separate(self):
        p2 = product('empty'); p2['skus'][0]['stock'] = 0
        task = self.submit(product('ok'), p2, product('failure', save_fail_attempts=1))
        self.engine()
        counts = self.done(task)['counts']
        self.assertEqual((counts['processed'], counts['succeeded'], counts['filtered'], counts['failed'], counts['published']), (3,1,1,1,0))

    def test_retry_limit_is_bounded(self):
        task = self.submit(product(fail_attempts=10))
        self.engine()
        for attempt in range(1,4):
            result = self.done(task)
            self.assertEqual(result['items'][0]['attempt'], attempt)
            if attempt < 3: self.store.control(task, 'retry')
        self.assertRaises(ConflictError, self.store.control, task, 'retry')

    def test_queued_tasks_persist_and_recover(self):
        task = self.submit(product())
        self.store = Store(self.db)
        self.engine()
        self.assertEqual(self.done(task)['counts']['succeeded'], 1)

    def test_single_engine_lock(self):
        self.engine()
        other = Engine(self.store)
        self.assertRaises(RuntimeError, other.start)

    def test_external_network_is_denied(self):
        with socket.socket() as sock:
            self.assertRaises(PermissionError, sock.connect, ('203.0.113.1',443))

    def test_logs_do_not_echo_credentials(self):
        raw = fixture(); raw['products'][0]['token'] = 'fictional_sensitive_marker'
        self.assertRaises(ValidationError, self.store.import_fixture, raw)
        class LeakyAdapter:
            def run(self, *_): raise RuntimeError('password=fictional_sensitive_marker')
        task = self.submit(product())
        self.engine(adapter=LeakyAdapter())
        events = json.dumps(self.done(task)['events'])
        self.assertNotIn('fictional_sensitive_marker', events)
        self.assertNotIn('password', events)

    def test_malformed_adapter_results_fail_without_stopping_queue(self):
        class MalformedAdapter:
            value = None
            def run(self, *_): return self.value
        adapter = MalformedAdapter()
        engine = self.engine(adapter=adapter)
        for i,value in enumerate((None, {}, {'accepted_skus':1})):
            adapter.value = value
            task = self.submit(product(),key='malformed'+str(i))
            result = self.done(task)
            self.assertEqual(result['counts']['failed'],1)
            self.assertEqual(result['items'][0]['error'],'adapter_failed')
            self.assertTrue(engine.thread.is_alive())
        adapter.value = evaluate(product(),validate_policy(POLICY))
        task=self.submit(product(),key='valid_after_malformed')
        self.assertEqual(self.done(task)['counts']['succeeded'],1)
        self.assertEqual(engine.status()['health'],'ready')

    def test_adapter_cannot_mutate_identity_cost_currency_stock_or_policy(self):
        class MutatingAdapter:
            mode='identity'
            def run(self,raw,policy,attempt):
                if self.mode=='identity':raw['external_id']='wrong-product'
                elif self.mode=='cost':raw['skus'][0]['cost']='1.00'
                elif self.mode=='currency':raw['skus'][0]['currency']='RUB'
                elif self.mode=='stock':raw['skus'][0]['stock']=0
                elif self.mode=='policy':policy['fx_rate']='1'
                result=evaluate(raw,policy)
                if self.mode=='decision':
                    result['skus'][0].update(decision='filtered',reasons=['stock_below_minimum'],price=None)
                    result['accepted_skus']=0
                return result
        adapter=MutatingAdapter();engine=self.engine(adapter=adapter)
        for mode in ('identity','cost','currency','stock','policy','decision'):
            adapter.mode=mode
            task=self.submit(product(),key='mutated_'+mode)
            result=self.done(task)
            self.assertEqual(result['counts']['succeeded'],0)
            self.assertEqual(result['items'][0]['error'],'adapter_failed')
            self.assertTrue(engine.thread.is_alive())
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM products').fetchone()[0],0)
            raws=[json.loads(r[0]) for r in db.execute('SELECT raw FROM items')]
            self.assertTrue(all(r['external_id']=='p1' and r['skus'][0]['cost']=='100.00' for r in raws))


class DomainTests(unittest.TestCase):
    def sku(self):
        return {'sku':'ONE', 'cost':'100.00', 'currency':'CNY', 'original_price':'160.00'}

    def test_TS10_desktop_extension_separate_bases(self):
        desktop = quote(self.sku(), validate_policy(POLICY))
        extension = quote(self.sku(), validate_policy({**POLICY, 'surface':'extension'}))
        self.assertEqual(desktop['sale']['amount'], '1080.00')
        self.assertEqual(desktop['strike']['amount'], '1920.00')
        self.assertEqual(extension['sale']['amount'], '1080.00')
        self.assertEqual(extension['strike']['amount'], '1728.00')
        self.assertNotEqual(desktop['rule_version'], extension['rule_version'])

    def test_cost_plus_formula_and_rounding(self):
        policy = validate_policy({'fx_rate':'12', 'fixed_fee':'10.00', 'fee_bps':1000, 'margin_bps':2000})
        q = quote(self.sku(), policy)
        self.assertEqual(q['sale']['amount'], '1728.58')
        self.assertEqual(q['strike']['amount'], '2247.15')
        policy['surface'] = 'extension'
        self.assertEqual(quote(self.sku(), policy)['strike']['amount'], '2496.00')

    def test_currency_tags_and_missing_fx_fail_closed(self):
        p = product(); p['skus'][0]['currency'] = 'RUB'
        result = evaluate(p, validate_policy(POLICY))
        self.assertEqual(result['skus'][0]['reasons'], ['currency_pair_missing'])
        del p['skus'][0]['currency']
        self.assertRaises(ValidationError, validate_fixture, fixture(p))
        self.assertRaises(ValidationError, validate_policy, {'cost_currency':'RUB','settlement_currency':'RUB','fx_rate':'12'})
        for bad in ('YN', '?', 'EUR'):
            self.assertRaises(ValidationError, validate_policy, {'settlement_currency':bad})

    def test_money_rejects_float_nan_precision_and_invalid_ratios(self):
        for bad in (100.01, 'NaN', 'Infinity', '-1', '100.001'):
            p = product(); p['skus'][0]['cost'] = bad
            self.assertRaises(ValidationError, validate_fixture, fixture(p))
        self.assertRaises(ValidationError, validate_policy, {'fee_bps':5000,'margin_bps':5000})

    def test_normalization_rules_explain_rejection(self):
        p = product(); p['title']=' Fictional   forbidden ';p['skus'][0]['sku']=' ab-1 ';p['skus'][0]['stock']=0
        r=evaluate(p,validate_policy({'banned_terms':['FORBIDDEN']}))
        self.assertEqual(r['title'],'Fictional forbidden')
        self.assertEqual(r['skus'][0]['sku'],'AB-1')
        self.assertEqual(r['skus'][0]['reasons'],['banned_term','stock_below_minimum'])

    def test_duplicate_conflicts_and_sku_collision_rejected(self):
        p = product(); other = copy.deepcopy(p); other['title']='changed'
        self.assertRaises(ValidationError, validate_fixture, fixture(p,other))
        p['skus'].append({**p['skus'][0],'sku':' P1 '})
        self.assertRaises(ValidationError, validate_fixture, fixture(p))

    def test_CSV_formula_injection_neutralized(self):
        p = evaluate(product('=danger'),validate_policy(POLICY))
        body=csv_export({'task':{'items':[{'result':p}]}}).decode('utf-8-sig')
        self.assertIn("'=danger",body)


if __name__ == '__main__':
    unittest.main()
