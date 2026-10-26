'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');
const {IpcController,CHANNELS,UPDATE,bindIpc}=require('../ipc-controller.cjs');
const task='a'.repeat(32),other='b'.repeat(32),origin='http://127.0.0.1:12345';let sequence=0;
const message=extra=>({protocol:1,requestId:'test-'+(++sequence),...extra});
function setup(options={}){const sent=[],calls=[];const backend={getTask:async id=>({id,source:'test_fixture',counts:{published:0}}),controlTask:async(id,action)=>{calls.push([id,action]);return {id,state:'canceled'};},exportTask:async()=>Buffer.from('{}'),...options.backend};const controller=new IpcController({origin,backend,send:(wc,channel,data)=>sent.push({window:wc.id,channel,data}),writeExport:async(_id,_format,_bytes,current)=>{current();return {fileName:'fixed.json'};},pollMs:100000,timeoutMs:100,...options,backend});const wc=id=>({id,mainFrame:{url:origin+'/'},isDestroyed:()=>false});const one=wc(11),two=wc(22);controller.register(one);controller.register(two);return {controller,one,two,sent,calls,event:sender=>({sender,senderFrame:sender.mainFrame})};}
async function watch(x,wc,id=task,subscriptionId='sub-'+wc.id){return x.controller.invoke(x.event(wc),CHANNELS.watch,message({taskId:id,subscriptionId}));}
test('sender identity, frame, channel and schema reject unauthorized input',async()=>{const x=setup();try{
 assert.equal((await x.controller.invoke(x.event({...x.one}),CHANNELS.runtime,message())).error.code,'REJECTED_SENDER');
 assert.equal((await x.controller.invoke({sender:x.one,senderFrame:{url:origin+'/'}},CHANNELS.runtime,message())).error.code,'REJECTED_FRAME');
 assert.equal((await x.controller.invoke(x.event(x.one),'store-delete',message({token:'fictional'}))).error.code,'CHANNEL_UNAVAILABLE');
 assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.control,message({taskId:task,action:'cancel',windowId:22}))).error.code,'INVALID_INPUT');
 assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.get,message({taskId:'../../secret'}))).error.code,'INVALID_INPUT');assert.equal(x.calls.length,0);
}finally{x.controller.close();}});
test('window/task association routes updates only to the registered window',async()=>{const x=setup();try{
 assert.equal((await watch(x,x.one,task)).ok,true);assert.equal((await watch(x,x.two,other)).ok,true);
 assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.control,message({taskId:other,action:'cancel'}))).error.code,'TASK_NOT_BOUND');
 assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.unwatch,message({subscriptionId:'sub-22'}))).error.code,'SUBSCRIPTION_NOT_FOUND');
 const row=x.controller.windows.get(11),sub=row.subscriptions.get('sub-11');await x.controller._poll(row,row.generation,sub);
 assert.equal(x.sent.length,1);assert.equal(x.sent[0].window,11);assert.equal(x.sent[0].data.taskId,task);assert.equal(x.sent[0].channel,UPDATE);
 const result=await x.controller.invoke(x.event(x.one),CHANNELS.control,message({taskId:task,action:'cancel'}));assert.equal(result.data.state,'canceled');assert.deepEqual(x.calls,[[task,'cancel']]);
}finally{x.controller.close();}});
test('late result after unwatch cannot emit; subscription cycles clean up',async()=>{let release;const x=setup();try{
 for(let i=0;i<2;i++){await watch(x,x.one,task,'cycle-'+i);await x.controller.invoke(x.event(x.one),CHANNELS.unwatch,message({subscriptionId:'cycle-'+i}));}
 assert.equal(x.controller.diagnostics().subscriptions,0);await watch(x,x.one);x.controller.backend.getTask=()=>new Promise(resolve=>{release=resolve;});
 const row=x.controller.windows.get(11),sub=row.subscriptions.get('sub-11'),pending=x.controller._poll(row,row.generation,sub);
 await x.controller.invoke(x.event(x.one),CHANNELS.unwatch,message({subscriptionId:'sub-11'}));release({id:task});await pending;assert.equal(x.sent.length,0);assert.equal(x.controller.diagnostics().subscriptions,0);
}finally{x.controller.close();}});
test('hard IPC deadline settles an uncooperative backend and frees slots',async()=>{const x=setup({timeoutMs:20,backend:{getTask:()=>new Promise(()=>{})}});try{const result=await watch(x,x.one);assert.equal(result.error.code,'IPC_TIMEOUT');assert.equal(x.controller.diagnostics().subscriptions,0);assert.equal(x.controller.diagnostics().pending,0);}finally{x.controller.close();}});
test('reset fences pending work, watch cap is atomic, request IDs cannot replay',async()=>{const releases=[];const x=setup({backend:{getTask:()=>new Promise(resolve=>releases.push(resolve))}});try{
 const pending=[];for(let i=0;i<5;i++)pending.push(watch(x,x.one,task,'limit-'+i));assert.equal((await pending[4]).error.code,'SUBSCRIPTION_LIMIT');
 x.controller.reset(x.one);for(const resolve of releases)resolve({id:task});assert.ok((await Promise.all(pending.slice(0,4))).every(r=>!r.ok));assert.equal(x.controller.diagnostics().subscriptions,0);
 const payload=message();assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.runtime,payload)).ok,true);assert.equal((await x.controller.invoke(x.event(x.one),CHANNELS.runtime,payload)).error.code,'REQUEST_REPLAY');
}finally{x.controller.close();}});
test('stale export cannot reach the managed file commit',async()=>{let release,writes=0;const x=setup({backend:{exportTask:()=>new Promise(resolve=>{release=resolve;})},writeExport:async(_id,_format,_bytes,current)=>{current();writes++;return {};}});try{await watch(x,x.one);const pending=x.controller.invoke(x.event(x.one),CHANNELS.export,message({taskId:task,format:'json'}));x.controller.reset(x.one);release(Buffer.from('{}'));assert.equal((await pending).ok,false);assert.equal(writes,0);}finally{x.controller.close();}});
test('handler cleanup removes the exact registration set',()=>{const x=setup(),handlers=new Map();const stop=bindIpc({handle:(name,fn)=>handlers.set(name,fn),removeHandler:name=>handlers.delete(name)},x.controller);assert.equal(handlers.size,7);stop();assert.equal(handlers.size,0);assert.equal(x.controller.diagnostics().windows,0);});
