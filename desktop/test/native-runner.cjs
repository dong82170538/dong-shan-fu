'use strict';
// Only run using the approved, pinned Electron binary. Never a Node mock substitute.
const electron=require('electron');const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');const http=require('node:http');
const {bootstrap}=require('../shell.cjs');const {windowOptions}=require('../security-policy.cjs');const {CHANNELS}=require('../ipc-controller.cjs');
const out=path.resolve(__dirname,'../..',process.env.DSF_ARTIFACT_DIR||'artifacts/stage3');fs.mkdirSync(out,{recursive:true});
const records=[];let shell,probe,badOriginServer;let request=0;
const evaluate=(window,code)=>window.webContents.executeJavaScript(code,true);
async function waitFor(check,timeout=5000){const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,25));}throw Error('NATIVE_ASSERTION_TIMEOUT');}
async function run(){
 shell=await bootstrap(electron,{show:false,createInitial:false,dataRelative:'data-desktop/native-acceptance-'+Date.now(),pollMs:100});
 const one=await shell.createWindow(),two=await shell.createWindow();
 const makeProduct=id=>({external_id:id,platform:'OZON',title:'Fictional native IPC '+id,skus:[{sku:id,stock:5,cost:'100.00',currency:'CNY',original_price:'160.00'}],test_behavior:{delay_ms:5000}});
 const fixture=await shell.backend.request('POST','/api/fixtures/import',{schema_version:1,source:'test_fixture',name:'Native IPC synthetic fixture',products:[makeProduct('p1')]});
 const first=await shell.backend.request('POST','/api/tasks',{fixture_id:fixture.id,idempotency_key:'native-first-'+Date.now(),timeout_ms:10000});
 const second=await shell.backend.request('POST','/api/tasks',{fixture_id:fixture.id,idempotency_key:'native-second-'+Date.now(),timeout_ms:10000});
 for(const window of [one,two]){
  const isolated=await evaluate(window,"({node:typeof require,process:typeof process,rawInvoke:typeof window.dongshanDesktop.invoke})");
  assert.deepEqual(isolated,{node:'undefined',process:'undefined',rawInvoke:'undefined'});
  const preferences=window.webContents.getLastWebPreferences();assert.equal(preferences.sandbox,true);assert.equal(preferences.contextIsolation,true);assert.equal(preferences.nodeIntegration,false);
 }
 const sub1=await evaluate(one,`(async()=>{window.__nativeUpdates=[];return await window.dongshanDesktop.watchTask(${JSON.stringify(first.id)},update=>window.__nativeUpdates.push(update));})()`);
 const sub2=await evaluate(two,`(async()=>{window.__nativeUpdates=[];return await window.dongshanDesktop.watchTask(${JSON.stringify(second.id)},update=>window.__nativeUpdates.push(update));})()`);
 assert.equal(sub1.ok,true);assert.equal(sub2.ok,true);
 await waitFor(async()=>await evaluate(one,'window.__nativeUpdates.length')>0&&await evaluate(two,'window.__nativeUpdates.length')>0);
 const cross=await evaluate(one,`window.dongshanDesktop.controlTask(${JSON.stringify(second.id)},'cancel')`);assert.equal(cross.error.code,'TASK_NOT_BOUND');
 const spoofUnwatch=await evaluate(one,`window.dongshanDesktop.unwatchTask(${JSON.stringify(sub2.data.subscriptionId)})`);assert.equal(spoofUnwatch.error.code,'SUBSCRIPTION_NOT_FOUND');
 const a=await evaluate(one,'window.__nativeUpdates'),b=await evaluate(two,'window.__nativeUpdates');
 assert.ok(a.every(event=>event.taskId===first.id&&event.subscriptionId===sub1.data.subscriptionId));assert.ok(b.every(event=>event.taskId===second.id&&event.subscriptionId===sub2.data.subscriptionId));
 records.push({case:'actual_window_and_task_association',status:'PASS',webContents:[one.webContents.id,two.webContents.id],taskIds:[first.id,second.id],deliveries:[a.length,b.length],spoofResult:cross.error.code});
 const fresh=await shell.backend.request('POST','/api/tasks',{fixture_id:fixture.id,idempotency_key:'native-cancel-'+Date.now(),timeout_ms:10000});
 const freshSub=await evaluate(one,`window.dongshanDesktop.watchTask(${JSON.stringify(fresh.id)},()=>{})`);assert.equal(freshSub.ok,true);
 const cancel=await evaluate(one,`window.dongshanDesktop.controlTask(${JSON.stringify(fresh.id)},'cancel')`);assert.equal(cancel.ok,true);
 assert.equal((await shell.backend.getTask(fresh.id)).state,'canceled');await evaluate(one,`window.dongshanDesktop.unwatchTask(${JSON.stringify(freshSub.data.subscriptionId)})`);
 await evaluate(one,`window.dongshanDesktop.unwatchTask(${JSON.stringify(sub1.data.subscriptionId)})`);
 await evaluate(two,`window.dongshanDesktop.unwatchTask(${JSON.stringify(sub2.data.subscriptionId)})`);
 for(let i=0;i<2;i++){const result=await evaluate(one,`window.dongshanDesktop.watchTask(${JSON.stringify(first.id)},()=>{})`);assert.equal(result.ok,true);await evaluate(one,`window.dongshanDesktop.unwatchTask(${JSON.stringify(result.data.subscriptionId)})`);}
 const listeners=await evaluate(one,'window.dongshanDesktop.bridgeState()');assert.equal(listeners.activeSubscriptions,0);assert.equal(listeners.updateListeners,0);assert.equal(shell.controller.diagnostics().subscriptions,0);
 records.push({case:'actual_listener_cleanup_two_cycles',status:'PASS',renderer:listeners,main:shell.controller.diagnostics()});
 // This dedicated test preload is never loaded by the production shell.
 probe=new electron.BrowserWindow(windowOptions({show:false,preload:path.join(__dirname,'native-probe-preload.cjs')}));
 await probe.loadURL(shell.origin+'/');
 const unknown=await evaluate(probe,`window.nativeProbe.invoke('store-delete','fictional_token_marker')`);assert.equal(unknown.ok,false);
 const sender=await evaluate(probe,`window.nativeProbe.invoke(${JSON.stringify(CHANNELS.runtime)},${JSON.stringify({protocol:1,requestId:'native-probe-'+(++request)})})`);assert.equal(sender.error.code,'REJECTED_SENDER');
 badOriginServer=http.createServer((_request,response)=>{response.end('<!doctype html><title>Owned synthetic wrong origin</title>');});await new Promise(resolve=>badOriginServer.listen(0,'127.0.0.1',resolve));
 shell.controller.register(probe.webContents);await probe.loadURL('http://127.0.0.1:'+badOriginServer.address().port+'/');
 const frame=await evaluate(probe,`window.nativeProbe.invoke(${JSON.stringify(CHANNELS.runtime)},${JSON.stringify({protocol:1,requestId:'native-probe-'+(++request)})})`);assert.equal(frame.error.code,'REJECTED_FRAME');
 shell.controller.remove(probe.webContents);probe.destroy();probe=null;
 records.push({case:'actual_unauthorized_channel_sender_and_origin',status:'PASS',unknown:unknown.error.code,sender:sender.error.code,origin:frame.error.code});
 // Hold an already-read response so destroying its real window tests late delivery fencing.
 const original=shell.backend.getTask.bind(shell.backend);let release;let hold=false;
 shell.backend.getTask=(id,signal)=>hold?new Promise(resolve=>{release=()=>original(id,signal).then(resolve,()=>resolve({id}));}):original(id,signal);
 const late=await evaluate(two,`window.dongshanDesktop.watchTask(${JSON.stringify(second.id)},()=>{})`);assert.equal(late.ok,true);hold=true;
 await waitFor(()=>typeof release==='function');const destroyedId=two.webContents.id;two.destroy();release();hold=false;
 await new Promise(resolve=>setTimeout(resolve,150));assert.equal(shell.controller.windows.has(destroyedId),false);
 shell.backend.getTask=original;
 if(['queued','running','paused','interrupted'].includes((await shell.backend.getTask(second.id)).state))await shell.backend.controlTask(second.id,'cancel');
 records.push({case:'actual_destroyed_window_late_response_cleanup',status:'PASS',main:shell.controller.diagnostics()});
 const report={status:'PASS',runId:process.env.DSF_NATIVE_RUN_ID,scope:'Actual Electron native IPC, not Node mocks',electron:process.versions.electron,platform:process.platform,pid:process.pid,backendPid:shell.service.child.pid,database:path.join(shell.dataDir,'commerce.sqlite3'),contextIsolation:true,sandbox:true,nodeIntegration:false,realConnectionsEnabled:false,published:0,records};
 fs.writeFileSync(path.join(out,'desktop-native-result.json'),JSON.stringify(report,null,2));
}
run().then(async()=>{if(badOriginServer)badOriginServer.close();await shell.stop();electron.app.exit(0);}).catch(async()=>{fs.writeFileSync(path.join(out,'desktop-native-result.json'),JSON.stringify({status:'FAIL',runId:process.env.DSF_NATIVE_RUN_ID,nativePassed:false,electron:process.versions.electron,platform:process.platform,records,error:'NATIVE_ACCEPTANCE_FAILED'},null,2));if(probe&&!probe.isDestroyed())probe.destroy();if(badOriginServer)badOriginServer.close();await shell?.stop();electron.app.exit(1);});
