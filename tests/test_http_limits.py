"""Small loopback synthetic resource checks; never target third-party services."""
from tests.support import temporary_directory
import http.client
import json
import socket
import tempfile
import threading
import unittest
from commerce_core.store import Store
from commerce_core.engine import Engine
from commerce_core.server import make_server
from tests.support import wait_until,fixture,POLICY
from tests.offline_guard import sitecustomize


class HttpResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=temporary_directory();self.store=Store(self.temp.name+'/core.sqlite3');self.engine=Engine(self.store).start()
        self.server=make_server(self.store,self.engine,0,max_requests=2,read_deadline=.4)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.clients=[]
    def tearDown(self):
        for client in self.clients:client.close()
        self.server.shutdown();self.server.server_close();self.thread.join();self.engine.stop();self.temp.cleanup()
    def partial(self,headers=True):
        client=socket.create_connection(('127.0.0.1',self.server.server_port),timeout=3);self.clients.append(client)
        if headers:
            client.sendall(('POST /api/tasks HTTP/1.1\r\nHost: 127.0.0.1:'+str(self.server.server_port)+'\r\nContent-Type: application/json\r\nX-Local-Client: dong-shan-fu\r\nContent-Length: 100\r\n\r\n{').encode())
        else:client.sendall(b'G')
        return client
    def response(self,client):
        response=http.client.HTTPResponse(client);response.begin();return response.status,json.loads(response.read())
    def test_partial_body_has_absolute_deadline_and_no_mutation(self):
        client=self.partial();status,data=self.response(client)
        self.assertEqual((status,data['error']),(408,'REQUEST_READ_TIMEOUT'))
        wait_until(lambda:self.server.active_requests==0)
        self.assertEqual(self.store.summary()['tasks'],0)
    def test_partial_headers_have_absolute_deadline(self):
        self.assertEqual(self.response(self.partial(False))[0],408)
    def test_saturation_is_503_without_extra_handler_and_recovers(self):
        first=self.partial();second=self.partial()
        wait_until(lambda:self.server.active_requests==2)
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        client.request('GET','/api/health');response=client.getresponse();body=json.loads(response.read());client.close()
        self.assertEqual((response.status,body['error']),(503,'LOCAL_SERVER_BUSY'))
        self.assertLessEqual(self.server.peak_active,2)
        self.assertEqual(self.response(first)[0],408);self.assertEqual(self.response(second)[0],408)
        wait_until(lambda:self.server.active_requests==0)
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        client.request('GET','/api/health');response=client.getresponse();response.read();client.close()
        self.assertEqual(response.status,200)
        self.assertEqual(self.store.summary()['tasks'],0)


class TopLevelSchemaTests(unittest.TestCase):
    def test_unexpected_sensitive_fields_are_400_without_state_change(self):
        with temporary_directory() as folder:
            store=Store(folder+'/core.sqlite3');engine=Engine(store) # Intentionally not running: no fixture races.
            imported=store.import_fixture(fixture());task=store.submit(imported['id'],POLICY,'schema')['id']
            scheduled=store.schedule(imported['id'],POLICY,store.clock()+3600)
            server=make_server(store,engine,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def snapshot():
                with store.connect() as db:return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in ('tasks','items','schedules','daily_schedules','sessions','events')}
            baseline=snapshot();marker='fictional_sensitive_marker'
            cases=[('/api/tasks',{'fixture_id':imported['id'],'policy':POLICY,'idempotency_key':'extra'}),('/api/schedules',{'fixture_id':imported['id'],'policy':POLICY,'due':store.clock()+3600}),('/api/schedules/daily',{'fixture_id':imported['id'],'policy':POLICY,'timezone':'Asia/Shanghai','local_time':'09:00'}),('/api/session/fake-login',{'source':'test_fixture'}),('/api/session/response',{'source':'test_fixture','epoch':0,'code':401}),('/api/session/logout',{'source':'test_fixture'}),('/api/tasks/'+task+'/cancel',{}),('/api/schedules/daily/'+scheduled['id']+'/cancel',{})]
            try:
                for path,body in cases:
                    for key in ('password','token','unknown'):
                        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                        c.request('POST',path,json.dumps({**body,key:marker}),{'Content-Type':'application/json','X-Local-Client':'dong-shan-fu'})
                        response=c.getresponse();text=response.read().decode();c.close()
                        self.assertEqual(response.status,400,(path,key,text));self.assertNotIn(marker,text)
                        self.assertEqual(snapshot(),baseline,(path,key))
            finally:server.shutdown();server.server_close();thread.join()
