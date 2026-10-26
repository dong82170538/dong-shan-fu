const {temporaryDir,removeOwnedDirectory}=require('./test-paths.cjs');
'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');const http=require('node:http');
const {ServiceManager}=require('../service-process.cjs');const {LocalBackend}=require('../backend-client.cjs');
const root=path.resolve(__dirname,'../..');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function wait(check,ms=4000){const end=Date.now()+ms;while(Date.now()<end){if(await check())return;await delay(10);}throw Error('WAIT_TIMEOUT');}
async function closeServer(server){await new Promise(resolve=>server.close(resolve));}
function fixture(){return {schema_version:1,source:'test_fixture',name:'Lifecycle real local data',products:[{external_id:'life-p1',platform:'OZON',title:'Fictional lifecycle product',skus:[{sku:'one',stock:1,cost:'100.00',currency:'CNY',original_price:'160.00'}]}]};}
test('actual Python ready/duplicate start/restart/stop preserves one real database',{timeout:15000},async()=>{
 const folder=temporaryDir('dsf-生命周期-');const manager=new ServiceManager(root,folder);const events=[];manager.on('state',s=>events.push({state:s.state,pid:s.pid,origin:s.origin}));
 try{
  const start=manager.start();assert.equal(manager.start(),start);await start;
  const first=manager.status();assert.equal(first.state,'ready');assert.equal(new URL(first.origin).hostname,'127.0.0.1');
  const backend=new LocalBackend(first.origin,{instanceId:first.instanceId});const health=await backend.request('GET','/api/health');assert.equal(health.service_instance,first.instanceId);
  const imported=await backend.request('POST','/api/fixtures/import',fixture());const accepted=await backend.request('POST','/api/tasks',{fixture_id:imported.id,idempotency_key:'life-task'});
  await wait(async()=>(await backend.getTask(accepted.id)).state==='completed');assert.equal((await backend.getTask(accepted.id)).counts.succeeded,1);
  await manager.start();assert.equal(manager.status().pid,first.pid);
  const firstOwnedChild=manager.child;await manager.restart();assert.equal(firstOwnedChild.exitCode,0,'restart must gracefully stop owned Python');const second=manager.status();assert.notEqual(second.pid,first.pid);assert.notEqual(second.instanceId,first.instanceId);
  const reconnected=new LocalBackend(second.origin,{instanceId:second.instanceId});assert.equal((await reconnected.getTask(accepted.id)).counts.succeeded,1);
  const secondOwnedChild=manager.child;await Promise.all([manager.stop(),manager.stop()]);assert.equal(secondOwnedChild.exitCode,0,'stop must be graceful');assert.equal(manager.state,'stopped');assert.equal(manager.current,null);
  const target=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,'lifecycle-real-result.json'),JSON.stringify({status:'PASS',scope:'Actual Node-owned Python subprocess and SQLite, not Electron',database:path.join(folder,'commerce.sqlite3'),taskId:accepted.id,first,second,events,firstExitCode:firstOwnedChild.exitCode,secondExitCode:secondOwnedChild.exitCode,published:0},null,2));
 }finally{await manager.stop();removeOwnedDirectory(folder);}
});
test('port conflict never stops the unrelated local owner; ephemeral port works',{timeout:10000},async()=>{
 const folder=temporaryDir('dsf-port-');const server=http.createServer((_q,r)=>r.end('owned-fixture'));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const manager=new ServiceManager(root,folder,{port:server.address().port});
 try{await assert.rejects(manager.start());assert.equal(manager.state,'failed');const body=await fetch('http://127.0.0.1:'+server.address().port).then(r=>r.text());assert.equal(body,'owned-fixture');await manager.stop();}
 finally{await manager.stop();await closeServer(server);removeOwnedDirectory(folder);}
});
test('duplicate managers same data directory fail without changing active owner',{timeout:12000},async()=>{
 const folder=temporaryDir('dsf-duplicate-');const first=new ServiceManager(root,folder),second=new ServiceManager(root,folder);
 try{await first.start();const pid=first.child.pid;await assert.rejects(second.start());assert.equal(first.child.pid,pid);assert.equal(first.state,'ready');assert.equal((await new LocalBackend(first.origin,{instanceId:first.instanceId}).request('GET','/api/health')).health,'ready');}
 finally{await second.stop();await first.stop();removeOwnedDirectory(folder);}
});
test('abnormal child exit is failed, explicit restart is needed and port reuse is rejected',{timeout:12000},async()=>{
 const folder=temporaryDir('dsf-exit-');const manager=new ServiceManager(root,folder);let server;
 try{await manager.start();const old=manager.status();const client=new LocalBackend(old.origin,{instanceId:old.instanceId});manager.child.kill('SIGKILL');await wait(()=>manager.state==='failed');assert.equal(manager.origin,null);
  server=http.createServer((_q,r)=>{r.setHeader('Content-Type','application/json');r.end('{"health":"ready"}');});await new Promise(resolve=>server.listen(Number(new URL(old.origin).port),'127.0.0.1',resolve));await assert.rejects(client.request('GET','/api/health'),error=>error.code==='SERVICE_IDENTITY_MISMATCH');
  await manager.restart();assert.equal(manager.state,'ready');assert.notEqual(manager.child.pid,old.pid);
 }finally{await manager.stop();if(server)await closeServer(server);removeOwnedDirectory(folder);}
});
test('missing Python, bad data path and stop during startup settle with fixed errors',{timeout:15000},async()=>{
 const folder=temporaryDir('dsf-bad-start-');const missing=new ServiceManager(root,folder,{python:path.join(folder,'no-python')});
 try{await assert.rejects(missing.start(),error=>['PYTHON_UNAVAILABLE','LOCAL_SERVICE_EXITED'].includes(error.code));await missing.stop();const bad=path.join(folder,'is-file');fs.writeFileSync(bad,'x');await assert.rejects(new ServiceManager(root,bad).start(),error=>error.code==='DATA_DIRECTORY_INVALID');
  const early=new ServiceManager(root,path.join(folder,'early'));const pending=early.start();const rejected=assert.rejects(pending);await early.stop();await rejected;assert.equal(early.state,'stopped');assert.equal(early.current,null);
 }finally{await missing.stop();removeOwnedDirectory(folder);}
});

test('synchronous starting/stopping observers cannot orphan or recursively stop a real child',{timeout:10000},async()=>{
 const folder=temporaryDir('dsf-reentrant-');const manager=new ServiceManager(root,folder);let firstStop,reentrantStop,owned;
 manager.on('state',state=>{if(state.state==='starting'){owned=manager.child;firstStop=manager.stop();}else if(state.state==='stopping'){reentrantStop=manager.stop();}});
 try{const started=manager.start();const rejection=assert.rejects(started,error=>error.code==='LOCAL_SERVICE_STOPPED');await firstStop;await rejection;assert.equal(firstStop,reentrantStop);assert.equal(manager.state,'stopped');assert.equal(manager.current,null);assert.ok(owned);assert.ok(owned.exitCode!==null||owned.signalCode!==null);await delay(80);assert.equal(manager.state,'stopped');assert.equal(manager.current,null);
  const target=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,'lifecycle-reentrancy-result.json'),JSON.stringify({status:'PASS',scope:'Actual owned Python child, synchronous state observer reentrancy',owned_pid:owned.pid,exitCode:owned.exitCode,signalCode:owned.signalCode,state:manager.state,current:null,same_stop_promise:firstStop===reentrantStop},null,2));
 }finally{await manager.stop();removeOwnedDirectory(folder);}
});

// These children are created by this test and remain in the test runner's Job.
// No existing/user process, name or port is used as a termination target.
async function stopFixture(folder,mode){
 const {spawn}=require('node:child_process'),{randomUUID}=require('node:crypto');const nonce=randomUUID();
 const python=process.env.DSF_PYTHON||(process.platform==='win32'?'python':'python3');
 const script=path.join(root,'tests/process_fixtures/stop_service.py');
 const child=spawn(python,['-u','-X','utf8',script,'--folder',folder,'--nonce',nonce,'--mode',mode],{cwd:root,stdio:['pipe','pipe','pipe'],shell:false,windowsHide:true});
 child.stdin.on('error',()=>{});let stdout='',stderr='';child.stdout.on('data',b=>stdout+=b);child.stderr.on('data',b=>stderr+=b);
 try{await wait(()=>stdout.includes('OWNED_STOP_READY'),4000);}catch(problem){if(!closedFixture(child)){child.kill('SIGKILL');await new Promise(resolve=>child.once('close',resolve));}throw problem;}
 const row=JSON.parse(fs.readFileSync(path.join(folder,'stop-fixture.json'),'utf8'));assert.equal(row.pid,child.pid);assert.equal(row.nonce,nonce);
 return {child,nonce,python,script,stderr:()=>stderr};
}
function closedFixture(child){return child.exitCode!==null||child.signalCode!==null;}
async function stopProof(fixture,folder){
 assert.ok(closedFixture(fixture.child));const file=path.join(folder,'stop-fixture.heartbeat');const before=fs.readFileSync(file);assert.ok(before.length>0);await delay(120);assert.deepEqual(fs.readFileSync(file),before);
 const {spawnSync}=require('node:child_process');const checked=spawnSync(fixture.python,[fixture.script,'--folder',folder,'--check-lock'],{cwd:root,encoding:'utf8',shell:false,timeout:3000,windowsHide:true});assert.equal(checked.status,0,checked.stderr);assert.equal(JSON.parse(checked.stdout).lock_reacquired,true);
 return {pid:fixture.child.pid,nonce:fixture.nonce,exitCode:fixture.child.exitCode,signalCode:fixture.child.signalCode,heartbeatBytes:before.length,heartbeatSha256:require('node:crypto').createHash('sha256').update(before).digest('hex'),heartbeatSample:before.toString('ascii'),fixtureRecord:JSON.parse(fs.readFileSync(path.join(folder,'stop-fixture.json'),'utf8')),heartbeatStable:true,lockReacquired:true,ownership:'held ChildProcess created by this test; containing verification Job records creation identities'};
}
function saveStopEvidence(name,record){const target=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,name),JSON.stringify(record,null,2));}

test('real forced stop rejects and stays failed across repeat stop/start/restart; fresh owner still runs',{timeout:20000},async()=>{
 const folder=temporaryDir('dsf-forced-stop-');let fixture,recovery;const manager=new ServiceManager(root,folder);const events=[];manager.on('state',s=>events.push(s));
 try{
  fixture=await stopFixture(folder,'ignore');const record={child:fixture.child,closed:false,stopPromise:null};manager.current=record;manager.state='ready';
  await assert.rejects(manager.stop(),e=>e.code==='LOCAL_SERVICE_STOP_FORCED');
  assert.equal(manager.state,'failed');assert.equal(manager.status().error,'LOCAL_SERVICE_STOP_FORCED');assert.equal(manager.current,record);assert.equal(record.closed,true);assert.equal(manager.status().stopResult.forced,true);assert.equal(manager.status().stopResult.cleanupConfirmed,true);
  for(const action of ['stop','start','restart'])await assert.rejects(manager[action](),e=>e.code==='LOCAL_SERVICE_STOP_FORCED');
  assert.equal(manager.child.pid,fixture.child.pid);assert.equal(events.some(e=>e.state==='stopped'),false);const proof=await stopProof(fixture,folder);
  recovery=new ServiceManager(root,path.join(folder,'recovery'));await recovery.start();const healthy=recovery.child;await recovery.stop();assert.equal(healthy.exitCode,0);
  saveStopEvidence('lifecycle-forced-stop-result.json',{status:'PASS',scope:'real owned offline Python fixture ignores stdin; not Electron',operationResult:'FAIL',error:manager.status().error,state:manager.state,stopResult:manager.status().stopResult,proof,events,freshOwnerExitCode:healthy.exitCode,stderr:fixture.stderr()});
 }finally{
  if(fixture&&!closedFixture(fixture.child)){fixture.child.kill('SIGKILL');await new Promise(resolve=>fixture.child.once('close',resolve));}
  if(recovery)await recovery.stop();removeOwnedDirectory(folder);
 }
});

test('real nonzero exit during stop rejects distinctly without pretending graceful success',{timeout:10000},async()=>{
 const folder=temporaryDir('dsf-nonzero-stop-');let fixture;const manager=new ServiceManager(root,folder);
 try{
  fixture=await stopFixture(folder,'nonzero');manager.current={child:fixture.child,closed:false,stopPromise:null};manager.state='ready';
  await assert.rejects(manager.stop(),e=>e.code==='LOCAL_SERVICE_STOP_NONZERO');assert.equal(manager.state,'failed');assert.equal(manager.status().stopResult.forced,false);assert.equal(fixture.child.exitCode,23);await assert.rejects(manager.stop(),e=>e.code==='LOCAL_SERVICE_STOP_NONZERO');const proof=await stopProof(fixture,folder);
  saveStopEvidence('lifecycle-nonzero-stop-result.json',{status:'PASS',operationResult:'FAIL',error:manager.status().error,stopResult:manager.status().stopResult,proof,stderr:fixture.stderr()});
 }finally{
  if(fixture&&!closedFixture(fixture.child)){fixture.child.kill('SIGKILL');await new Promise(resolve=>fixture.child.once('close',resolve));}removeOwnedDirectory(folder);
 }
});
