'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const http=require('node:http');
const {ServiceManager}=require('../service-process.cjs');const {LocalBackend}=require('../backend-client.cjs');
const root=path.resolve(__dirname,'../..');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function wait(check,ms=4000){const end=Date.now()+ms;while(Date.now()<end){if(await check())return;await delay(10);}throw Error('WAIT_TIMEOUT');}
async function closeServer(server){await new Promise(resolve=>server.close(resolve));}
function fixture(){return {schema_version:1,source:'test_fixture',name:'Lifecycle real local data',products:[{external_id:'life-p1',platform:'OZON',title:'Fictional lifecycle product',skus:[{sku:'one',stock:1,cost:'100.00',currency:'CNY',original_price:'160.00'}]}]};}
test('actual Python ready/duplicate start/restart/stop preserves one real database',{timeout:15000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-生命周期-'));const manager=new ServiceManager(root,folder);const events=[];manager.on('state',s=>events.push({state:s.state,pid:s.pid,origin:s.origin}));
 try{
  const start=manager.start();assert.equal(manager.start(),start);await start;
  const first=manager.status();assert.equal(first.state,'ready');assert.equal(new URL(first.origin).hostname,'127.0.0.1');
  const backend=new LocalBackend(first.origin,{instanceId:first.instanceId});const health=await backend.request('GET','/api/health');assert.equal(health.service_instance,first.instanceId);
  const imported=await backend.request('POST','/api/fixtures/import',fixture());const accepted=await backend.request('POST','/api/tasks',{fixture_id:imported.id,idempotency_key:'life-task'});
  await wait(async()=>(await backend.getTask(accepted.id)).state==='completed');assert.equal((await backend.getTask(accepted.id)).counts.succeeded,1);
  await manager.start();assert.equal(manager.status().pid,first.pid);
  await manager.restart();const second=manager.status();assert.notEqual(second.pid,first.pid);assert.notEqual(second.instanceId,first.instanceId);
  const reconnected=new LocalBackend(second.origin,{instanceId:second.instanceId});assert.equal((await reconnected.getTask(accepted.id)).counts.succeeded,1);
  await Promise.all([manager.stop(),manager.stop()]);assert.equal(manager.state,'stopped');assert.equal(manager.current,null);
  const target=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,'lifecycle-real-result.json'),JSON.stringify({status:'PASS',scope:'Actual Node-owned Python subprocess and SQLite, not Electron',database:path.join(folder,'commerce.sqlite3'),taskId:accepted.id,first,second,events,published:0},null,2));
 }finally{await manager.stop();fs.rmSync(folder,{recursive:true,force:true});}
});
test('port conflict never stops the unrelated local owner; ephemeral port works',{timeout:10000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-port-'));const server=http.createServer((_q,r)=>r.end('owned-fixture'));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const manager=new ServiceManager(root,folder,{port:server.address().port});
 try{await assert.rejects(manager.start());assert.equal(manager.state,'failed');const body=await fetch('http://127.0.0.1:'+server.address().port).then(r=>r.text());assert.equal(body,'owned-fixture');await manager.stop();}
 finally{await manager.stop();await closeServer(server);fs.rmSync(folder,{recursive:true,force:true});}
});
test('duplicate managers same data directory fail without changing active owner',{timeout:12000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-duplicate-'));const first=new ServiceManager(root,folder),second=new ServiceManager(root,folder);
 try{await first.start();const pid=first.child.pid;await assert.rejects(second.start());assert.equal(first.child.pid,pid);assert.equal(first.state,'ready');assert.equal((await new LocalBackend(first.origin,{instanceId:first.instanceId}).request('GET','/api/health')).health,'ready');}
 finally{await second.stop();await first.stop();fs.rmSync(folder,{recursive:true,force:true});}
});
test('abnormal child exit is failed, explicit restart is needed and port reuse is rejected',{timeout:12000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-exit-'));const manager=new ServiceManager(root,folder);let server;
 try{await manager.start();const old=manager.status();const client=new LocalBackend(old.origin,{instanceId:old.instanceId});manager.child.kill('SIGKILL');await wait(()=>manager.state==='failed');assert.equal(manager.origin,null);
  server=http.createServer((_q,r)=>{r.setHeader('Content-Type','application/json');r.end('{"health":"ready"}');});await new Promise(resolve=>server.listen(Number(new URL(old.origin).port),'127.0.0.1',resolve));await assert.rejects(client.request('GET','/api/health'),error=>error.code==='SERVICE_IDENTITY_MISMATCH');
  await manager.restart();assert.equal(manager.state,'ready');assert.notEqual(manager.child.pid,old.pid);
 }finally{await manager.stop();if(server)await closeServer(server);fs.rmSync(folder,{recursive:true,force:true});}
});
test('missing Python, bad data path and stop during startup settle with fixed errors',{timeout:15000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-bad-start-'));const missing=new ServiceManager(root,folder,{python:path.join(folder,'no-python')});
 try{await assert.rejects(missing.start(),error=>['PYTHON_UNAVAILABLE','LOCAL_SERVICE_EXITED'].includes(error.code));await missing.stop();const bad=path.join(folder,'is-file');fs.writeFileSync(bad,'x');await assert.rejects(new ServiceManager(root,bad).start(),error=>error.code==='DATA_DIRECTORY_INVALID');
  const early=new ServiceManager(root,path.join(folder,'early'));const pending=early.start();const rejected=assert.rejects(pending);await early.stop();await rejected;assert.equal(early.state,'stopped');assert.equal(early.current,null);
 }finally{await missing.stop();fs.rmSync(folder,{recursive:true,force:true});}
});

test('synchronous starting/stopping observers cannot orphan or recursively stop a real child',{timeout:10000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-reentrant-'));const manager=new ServiceManager(root,folder);let firstStop,reentrantStop,owned;
 manager.on('state',state=>{if(state.state==='starting'){owned=manager.child;firstStop=manager.stop();}else if(state.state==='stopping'){reentrantStop=manager.stop();}});
 try{const started=manager.start();const rejection=assert.rejects(started,error=>error.code==='LOCAL_SERVICE_STOPPED');await firstStop;await rejection;assert.equal(firstStop,reentrantStop);assert.equal(manager.state,'stopped');assert.equal(manager.current,null);assert.ok(owned);assert.ok(owned.exitCode!==null||owned.signalCode!==null);await delay(80);assert.equal(manager.state,'stopped');assert.equal(manager.current,null);
  const target=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,'lifecycle-reentrancy-result.json'),JSON.stringify({status:'PASS',scope:'Actual owned Python child, synchronous state observer reentrancy',owned_pid:owned.pid,exitCode:owned.exitCode,signalCode:owned.signalCode,state:manager.state,current:null,same_stop_promise:firstStop===reentrantStop},null,2));
 }finally{await manager.stop();fs.rmSync(folder,{recursive:true,force:true});}
});
