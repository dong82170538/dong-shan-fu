'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const TaskClient=require('../../web/client.js');const {IpcController,CHANNELS}=require('../ipc-controller.cjs');const {ServiceManager}=require('../service-process.cjs');const {LocalBackend}=require('../backend-client.cjs');const {exportWriter}=require('../managed-export.cjs');
const root=path.resolve(__dirname,'../..'),delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function wait(check){const end=Date.now()+5000;while(Date.now()<end){if(await check())return;await delay(10);}throw Error('WAIT_TIMEOUT');}
function createBridge(controller,wc){let sequence=0;const callbacks=new Map();const invoke=(channel,body)=>controller.invoke({sender:wc,senderFrame:wc.mainFrame},channel,{protocol:1,requestId:'protocol-'+(++sequence),...body});return {callbacks,runtime:()=>invoke(CHANNELS.runtime,{}),watchTask:async(taskId,callback)=>{const subscriptionId='subscription-'+(++sequence);callbacks.set(subscriptionId,callback);const result=await invoke(CHANNELS.watch,{taskId,subscriptionId});if(!result.ok)callbacks.delete(subscriptionId);return result;},unwatchTask:async subscriptionId=>{callbacks.delete(subscriptionId);return invoke(CHANNELS.unwatch,{subscriptionId});},controlTask:(taskId,action)=>invoke(CHANNELS.control,{taskId,action}),exportTask:(taskId,format)=>invoke(CHANNELS.export,{taskId,format})};}
test('renderer protocol uses real Python task/retry/currency/managed export; IPC transport is a stated double',{timeout:15000},async()=>{
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dsf-ui-protocol-')),manager=new ServiceManager(root,folder);let controller;
 try{
  await manager.start();const backend=new LocalBackend(manager.origin,{instanceId:manager.instanceId});const wc={id:11,mainFrame:{url:manager.origin+'/'},isDestroyed:()=>false};let bridge;
  controller=new IpcController({origin:manager.origin,backend,pollMs:100000,send:(_wc,_channel,data)=>bridge.callbacks.get(data.subscriptionId)?.(data),writeExport:exportWriter(folder)});controller.register(wc);bridge=createBridge(controller,wc);
  const requests=[];const request=(url,body)=>{requests.push(url);return backend.request(body===undefined?'GET':'POST',url,body);};const client=new TaskClient({request,bridge});client.setServiceState('ready');
  const raw={schema_version:1,source:'test_fixture',name:'Renderer protocol actual data',products:[{external_id:'ui-p1',platform:'OZON',title:'Fictional renderer SKU',skus:[{sku:'one',stock:1,cost:'100.00',currency:'CNY',original_price:'160.00'}],test_behavior:{fail_attempts:1}}]};
  const fixture=await client.importFixture(raw);const task=await client.submit({fixture_id:fixture.id,idempotency_key:'protocol-task',policy:{pricing_profile:'mock_fee_v1',fx_rate:'12'}});
  await wait(async()=>(await backend.getTask(task.id)).state==='completed_with_errors');await client.bind(task.id);
  const retry=await client.control(task.id,'retry');assert.equal(retry.state,'queued');await wait(async()=>(await backend.getTask(task.id)).state==='completed');
  const actual=await backend.getTask(task.id);assert.equal(actual.counts.succeeded,1);assert.equal(actual.counts.published,0);assert.deepEqual(actual.items[0].result.skus[0].price.sale,{amount:'1080.00',currency:'RUB'});
  const exported=await client.exportTask(task.id,'json');assert.equal(exported.kind,'managed');const bytes=fs.readFileSync(path.join(folder,'exports',exported.fileName));const data=JSON.parse(bytes);assert.equal(data.task.id,task.id);assert.equal(data.task.counts.succeeded,1);assert.equal(data.published,0);assert.equal(bytes.length,exported.bytes);
  const before=requests.length;client.setServiceState('failed');await assert.rejects(client.control(task.id,'cancel'),e=>e.code==='LOCAL_SERVICE_UNAVAILABLE');assert.equal(requests.length,before);await client.unbind();assert.equal(controller.diagnostics().subscriptions,0);
  const out=path.join(root,process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'renderer-protocol-real-core-result.json'),JSON.stringify({status:'PASS',scope:'Renderer client and IPC controller with explicit transport double, actual Python core/database/export filesystem; NOT native IPC',backendPid:manager.child.pid,database:path.join(folder,'commerce.sqlite3'),taskId:task.id,price:actual.items[0].result.skus[0].price.sale,saved:actual.counts.succeeded,published:0,exportBytes:bytes.length,requestPaths:requests,subscriptions:controller.diagnostics().subscriptions},null,2));
 }finally{controller?.close();await manager.stop();fs.rmSync(folder,{recursive:true,force:true});}
});
test('rapid selection and disposal discard late updates and clean the old subscription',async()=>{
 let resolveA;const removed=[],callbacks=new Map();let next=0;
 const bridge={watchTask:(id,callback)=>{callbacks.set(id,callback);if(id==='A')return new Promise(resolve=>{resolveA=resolve;});return Promise.resolve({ok:true,data:{subscriptionId:'sub-'+(++next),task:{id}}});},unwatchTask:async id=>{removed.push(id);return {ok:true};}};
 const client=new TaskClient({request:()=>{throw Error('MUST_NOT_HTTP_FALLBACK');},bridge});client.setServiceState('ready');const received=[];
 const a=client.bind('A',value=>received.push(value));const aRejected=assert.rejects(a,e=>e.code==='TASK_SELECTION_CHANGED');const b=await client.bind('B',value=>received.push(value));resolveA({ok:true,data:{subscriptionId:'late-A',task:{id:'A'}}});await aRejected;
 callbacks.get('A')({taskId:'A',task:{id:'A'}});callbacks.get('B')({taskId:'B',task:{id:'B'}});assert.equal(received.length,1);assert.equal(received[0].taskId,'B');assert.ok(removed.includes('late-A'));
 await client.unbind();callbacks.get('B')({taskId:'B',task:{id:'B'}});assert.equal(received.length,1);assert.ok(removed.includes(b.subscriptionId));
});
test('web compatibility calls real request operations and never fabricates save/export completion',async()=>{
 const calls=[];const client=new TaskClient({request:async(path,body)=>{calls.push({path,body});return {from:'actual request adapter'};}});
 assert.deepEqual(await client.importFixture({source:'test_fixture'}),{from:'actual request adapter'});await client.submit({fixture_id:'x'});await client.control('task','cancel');const download=await client.exportTask('task','csv');assert.equal(download.kind,'download');assert.equal(download.href,'/api/tasks/task/export.csv');assert.equal(calls.length,3);
});
test('desktop errors do not fall back to HTTP mutation',async()=>{
 let http=0;const bridge={watchTask:async()=>({ok:false,error:{code:'REJECTED_FRAME'}})};const client=new TaskClient({request:()=>http++,bridge});client.setServiceState('ready');await assert.rejects(client.control('x','cancel'),e=>e.code==='REJECTED_FRAME');assert.equal(http,0);
});
