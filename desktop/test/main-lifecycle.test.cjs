'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const {reloadWindows}=require('../shell.cjs');
function load(stop){
 const app=new EventEmitter(),quitEvents=[],exits=[];let stops=0;
 app.quit=()=>{const event={prevented:false,preventDefault(){this.prevented=true;}};app.emit('before-quit',event);quitEvents.push(event);};app.exit=code=>exits.push(code);
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8'),{require:name=>name==='electron'?{app}:{bootstrap:async()=>({stop:()=>{stops++;return stop(app);}})},console:{error:()=>{}}});
 return {app,quitEvents,exits,get stops(){return stops;}};
}
test('main keeps every reentrant quit blocked until owned backend stop is confirmed (Electron event double)',async()=>{
 let release;const pending=new Promise(resolve=>release=resolve);const x=load(app=>{app.emit('window-all-closed');return pending;});
 x.app.quit();await flush();x.app.quit();
 assert.equal(x.stops,1);assert.equal(x.quitEvents.length,3);assert.ok(x.quitEvents.every(event=>event.prevented));assert.deepEqual(x.exits,[]);
 release();await flush();assert.equal(x.quitEvents.length,4);assert.equal(x.quitEvents[3].prevented,false);assert.equal(x.stops,1);
});
test('unconfirmed owned stop remains an error and never silently permits native quit (Electron event double)',async()=>{
 const x=load(async()=>{throw Object.assign(Error('stop'),{code:'LOCAL_SERVICE_STOP_UNCONFIRMED'});});x.app.quit();await flush();x.app.quit();await flush();assert.ok(x.quitEvents.every(event=>event.prevented));assert.equal(x.stops,1);assert.deepEqual(x.exits,[]);
});
test('live window reload failure is reported, destroyed window failure is harmless (window doubles)',async()=>{
 const loaded=[];await reloadWindows(new Set([{isDestroyed:()=>false,loadURL:async url=>loaded.push(url)}]),'http://127.0.0.1:9000');assert.deepEqual(loaded,['http://127.0.0.1:9000/']);
 await assert.rejects(reloadWindows(new Set([{isDestroyed:()=>false,loadURL:async()=>{throw Error('synthetic');}}]),'http://127.0.0.1:9000'),error=>error.code==='LOCAL_UI_RELOAD_FAILED');
 let dead=false;await reloadWindows(new Set([{isDestroyed:()=>dead,loadURL:async()=>{dead=true;throw Error('closed during navigation');}}]),'http://127.0.0.1:9000');
});

for(const code of ['LOCAL_SERVICE_STOP_FORCED','LOCAL_SERVICE_STOP_NONZERO'])test(code+' propagates to the native quit gate without allowing clean exit (Electron event double)',async()=>{
 const x=load(async()=>{throw Object.assign(Error(code),{code});});x.app.quit();await flush();x.app.quit();await flush();assert.ok(x.quitEvents.every(event=>event.prevented));assert.equal(x.stops,1);assert.deepEqual(x.exits,[]);
});
