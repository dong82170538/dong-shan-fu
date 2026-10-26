import http.client
from contextlib import redirect_stderr
import io
import json
import tempfile
import threading
import unittest

from commerce_core.engine import Engine
from commerce_core.server import make_server
from commerce_core.store import Store
from tests.support import POLICY, fixture, product, wait_until, ManualClock
from tests.offline_guard import sitecustomize


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/core.sqlite3')
        self.engine = Engine(self.store).start()
        self.server = make_server(self.store, self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.engine.stop()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        opts = {'Content-Type': 'application/json', 'X-Local-Client': 'dong-shan-fu'}
        opts.update(headers or {})
        c.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), opts)
        response = c.getresponse()
        data = response.read()
        status, mime = response.status, response.getheader('Content-Type')
        c.close()
        return status, json.loads(data) if 'application/json' in mime else data

    def test_HTTP_real_import_submit_export_and_capability_gate(self):
        status, imported = self.request('/api/fixtures/import', fixture())
        self.assertEqual(status, 201)
        body = {'fixture_id': imported['id'], 'policy': POLICY, 'idempotency_key': 'http'}
        status, accepted = self.request('/api/tasks', body)
        self.assertEqual(status, 202)  # Acceptance is never reported as completion.
        self.assertTrue(accepted['accepted'])
        task = accepted['id']
        wait_until(lambda: self.store.task(task)['state'] == 'completed')
        status, result = self.request('/api/tasks/' + task + '/export.json')
        self.assertEqual(status, 200)
        self.assertEqual(result['task']['counts']['succeeded'], 1)
        self.assertEqual(result['published'], 0)
        status, csv = self.request('/api/tasks/' + task + '/export.csv')
        self.assertEqual(status, 200)
        self.assertIn(b'1080.00', csv)
        status, duplicate = self.request('/api/tasks', body)
        self.assertTrue(duplicate['deduplicated'])
        self.assertEqual(duplicate['id'], task)
        status, _ = self.request('/api/tasks', {**body,'policy': {**POLICY,'fx_rate':'13'}})
        self.assertEqual(status, 409)
        for path in ('/api/publish', '/api/shops/connect'):
            status, unavailable = self.request(path, {})
            self.assertEqual(status, 403)
            self.assertEqual(unavailable['published'], 0)
        status, caps = self.request('/api/capabilities')
        self.assertFalse(caps['real_connections_enabled'])
        self.assertEqual(caps['original_erp_routes_implemented'], 0)
        self.assertEqual(caps['surfaces']['desktop_assistant'], 'ui_wired_native_unverified')

    def test_local_host_origin_schema_and_secret_rejection(self):
        self.assertEqual(self.request('/api/tasks', {}, {'Origin':'https://fixture.invalid'})[0], 403)
        self.assertEqual(self.request('/api/tasks', {}, {'X-Local-Client':''})[0], 403)
        self.assertEqual(self.request('/api/tasks', {}, {'Host':'fixture.invalid'})[0], 403)
        self.assertEqual(self.request('/../../.git/config')[0], 404)
        bad = fixture(); bad['products'][0]['password']='fictional_sensitive_marker'
        status, error = self.request('/api/fixtures/import', bad)
        self.assertEqual(status,400)
        self.assertNotIn('fictional_sensitive_marker',json.dumps(error))
        self.assertEqual(len(self.store.list_tasks()),0)
        self.assertEqual(self.request('/api/tasks', {'policy': []})[0],400)

    def test_malformed_absolute_target_does_not_leak_credentials(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status, response = self.request('http://[password=fictional_sensitive_marker]/', headers={'Host':f'127.0.0.1:{self.server.server_port}'})
        self.assertEqual(status,400)
        self.assertNotIn('fictional_sensitive_marker',stderr.getvalue())
        self.assertNotIn('fictional_sensitive_marker',json.dumps(response))

    def test_TS13_unauthenticated_restricted_routes_block_writes(self):
        status,session=self.request('/api/session')
        self.assertEqual(session['state'],'unauthenticated')
        for path in ('/restricted/selection','/restricted/erp-edit'):
            status,error=self.request(path)
            self.assertEqual((status,error['error']),(401,'AUTH_REQUIRED'))
        for path in ('/api/restricted/selection','/api/restricted/erp-edit'):
            status,error=self.request(path,{})
            self.assertEqual((status,error['error']),(401,'AUTH_REQUIRED'))
        self.assertEqual(self.store.list_tasks(),[])
        status,session=self.request('/api/session/fake-login',{'source':'test_fixture'})
        self.assertEqual(session['state'],'authenticated_fixture')
        self.assertFalse(session['real_authentication'])
        self.assertFalse(session['real_credentials_stored'])
        self.assertEqual(self.request('/api/restricted/erp-edit',{})[0],403)

    def test_new_fixture_api_operations_are_executable(self):
        _,f=self.request('/api/fixtures/import',fixture())
        status,schedule=self.request('/api/schedules/daily',{'fixture_id':f['id'],'policy':POLICY,'timezone':'Asia/Shanghai','local_time':'09:00'})
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/schedules/daily/'+schedule['id']+'/cancel',{})[1]['state'],'canceled')
        self.assertEqual(self.request('/api/schedules/daily')[1]['schedules'][0]['state'],'canceled')
        status,m=self.request('/api/money-records',{'source':'test_fixture','context':'inspection','resMoney':{'amount':'1000.00','currency':'RUB','context':'inspection'}})
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/money-records')[1]['records'][0]['currency'],'RUB')
        body={'source':'test_fixture','candidate_origin':'https://fixture.invalid','publish':False,'selected_skus':[{'goods_id':'s1','offer_id':'fixture-1','price':'100.00','old_price':'160.00','min_price':'50.00','currency':'RUB'}]}
        self.assertEqual(self.request('/api/extension/preview',body)[1]['state'],'dry_run_preview')

    def test_registered_extension_origin_only_grants_fixture_preview(self):
        origin='chrome-extension://'+'a'*32
        body={'source':'test_fixture','candidate_origin':'https://fixture.invalid','publish':False,'selected_skus':[{'goods_id':'s1','offer_id':'fixture-1','price':'100.00','old_price':'160.00','min_price':'50.00','currency':'RUB'}]}
        self.assertEqual(self.request('/api/extension/preview',body,{'Origin':origin})[0],403)
        server=make_server(self.store,self.engine,0,['a'*32])
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def post(path,selected_origin):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            c.request('POST',path,json.dumps(body),{'Content-Type':'application/json','X-Local-Client':'dong-shan-fu','Origin':selected_origin})
            r=c.getresponse();r.read();status=r.status;c.close();return status
        try:
            self.assertEqual(post('/api/extension/preview',origin),200)
            self.assertEqual(post('/api/extension/preview','chrome-extension://'+'b'*32),403)
            self.assertEqual(post('/api/tasks',origin),403)
        finally:server.shutdown();server.server_close();thread.join()

    def test_TS18_inventory_and_own_implemented_capabilities_are_separate(self):
        status,inventory=self.request('/api/reference/erp-routes')
        self.assertEqual(status,200)
        self.assertEqual(inventory['source'],'parent static evidence')
        self.assertFalse(inventory['materialized_original_source'])
        self.assertEqual(len(inventory['routes']),68)
        self.assertTrue(inventory['expressions_are_inert_text'])
        self.assertTrue(all(isinstance(r['path'],str) and r['evidence']['end']>r['evidence']['start'] for r in inventory['routes']))
        self.assertEqual(sum(r['name']=='Home' for r in inventory['routes']),2)
        _,caps=self.request('/api/capabilities')
        implemented=[r for r in caps['independent_routes'] if r['status']=='implemented_fixture']
        self.assertGreater(len(implemented),0)
        self.assertEqual(len(implemented),caps['independent_implemented_route_count'])
        self.assertEqual(caps['reference_erp_route_records'],68)
        self.assertEqual(caps['original_erp_routes_implemented'],0)
        self.assertEqual(caps['surfaces']['web_erp'],'local_fixture_ui')
        self.assertEqual(caps['surfaces']['mv3_extension'],'source_written_host_unverified')
        status,missing=self.request('/api/erp/CreateGoods')
        self.assertEqual((status,missing['error']),(501,'NOT_IMPLEMENTED'))

    def test_independent_manifest_handlers_are_reachable_in_real_server(self):
        from commerce_core.server import INDEPENDENT_ROUTES
        _,f=self.request('/api/fixtures/import',fixture())
        _,accepted=self.request('/api/tasks',{'fixture_id':f['id'],'policy':POLICY,'idempotency_key':'manifest-base'})
        task=accepted['id'];wait_until(lambda:self.store.task(task)['state']=='completed')
        _,daily=self.request('/api/schedules/daily',{'fixture_id':f['id'],'policy':POLICY,'timezone':'Asia/Shanghai','local_time':'09:00'})
        bodies={
            '/api/fixtures/import':fixture(),
            '/api/tasks':{'fixture_id':f['id'],'policy':POLICY,'idempotency_key':'manifest-probe'},
            '/api/schedules':{'fixture_id':f['id'],'policy':POLICY,'due':self.store.clock()+3600},
            '/api/schedules/daily':{'fixture_id':f['id'],'policy':POLICY,'timezone':'Asia/Shanghai','local_time':'09:00'},
            '/api/schedules/daily/{id}/cancel':{},
            '/api/money-records':{'source':'test_fixture','context':'inspection','resMoney':{'amount':'1000.00','currency':'RUB','context':'inspection'}},
            '/api/extension/preview':{'source':'test_fixture','candidate_origin':'https://fixture.invalid','publish':False,'selected_skus':[{'goods_id':'s1','offer_id':'fixture-1','price':'100.00','old_price':'160.00','min_price':'50.00','currency':'RUB'}]},
            '/api/session/fake-login':{'source':'test_fixture'},
            '/api/session/response':{'source':'test_fixture','epoch':1,'code':0},
            '/api/session/logout':{'source':'test_fixture'},
        }
        reachable=[]
        for route in INDEPENDENT_ROUTES:
            if route['status']!='implemented_fixture':continue
            path=route['path'].replace('{id}',daily['id'] if route['path'].startswith('/api/schedules/daily/') else task)
            body=None if route['method']=='GET' else bodies.get(route['path'],{})
            status,_=self.request(path,body)
            self.assertIn(status,{200,201,202,400,401,403,409},(route,status))
            reachable.append((route['method'],route['path']))
        self.assertEqual(len(reachable),len({tuple(pair) for pair in reachable}))


class ScheduleTests(unittest.TestCase):
    def test_manual_keys_cannot_collide_with_internal_schedule_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            clock=ManualClock();clock.now=1792889400
            store=Store(folder+'/core.sqlite3',clock=clock)
            f=store.import_fixture(fixture())
            scheduled=store.schedule(f['id'],POLICY,clock.now+10)
            manual=store.submit(f['id'],POLICY,'schedule_'+scheduled['id'])
            store.submit(f['id'],POLICY,'schedule:'+scheduled['id'])
            clock.advance(10)
            store.tick_schedules();store.tick_schedules()
            self.assertEqual(len(store.list_tasks()),3)
            self.assertEqual(store.list_schedules()[0]['state'],'submitted')
            self.assertIsNotNone(store.claim())

    def test_missed_one_shot_skips_once_and_due_submission_is_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = ManualClock(); clock.now = 1792889400
            store = Store(folder + '/core.sqlite3', clock=clock)
            f = store.import_fixture(fixture())
            a = store.schedule(f['id'], POLICY, clock.now + 10)
            b = store.schedule(f['id'], POLICY, clock.now + 20)
            clock.advance(30)
            store.recover()
            store.recover()
            self.assertEqual([x['state'] for x in store.list_schedules()], ['missed','missed'])
            self.assertEqual(store.list_tasks(), [])
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE code='schedule_missed_skipped'").fetchone()[0], 2)
            c = store.schedule(f['id'], POLICY, clock.now + 5)
            clock.advance(5)
            store.tick_schedules(); store.tick_schedules()
            self.assertEqual(len(store.list_tasks()),1)
            self.assertEqual(next(s for s in store.list_schedules() if s['id']==c['id'])['state'],'submitted')
