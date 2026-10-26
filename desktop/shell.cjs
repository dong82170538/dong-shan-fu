'use strict';
const fs=require('node:fs');const path=require('node:path');const {randomUUID}=require('node:crypto');
const {IpcController,bindIpc}=require('./ipc-controller.cjs');const {LocalBackend}=require('./backend-client.cjs');
const {windowOptions,protectSession,protectWindow}=require('./security-policy.cjs');const {exportWriter,inside}=require('./managed-export.cjs');
const {ServiceManager}=require('./service-process.cjs');const packageInfo=require('./package.json');
function prepareData(repoRoot,relative='data-desktop'){
 const directory=path.resolve(repoRoot,relative);if(!inside(repoRoot,directory))throw Error('DATA_DIRECTORY_OUTSIDE_PROJECT');fs.mkdirSync(directory,{recursive:true});if(!inside(fs.realpathSync(repoRoot),fs.realpathSync(directory)))throw Error('DATA_DIRECTORY_SYMLINK_OUTSIDE_PROJECT');return directory;
}
function configurePaths(app,dataDir){
 const root=fs.realpathSync(dataDir);
 for(const [name,child] of [['userData','electron-profile'],['sessionData','electron-session']]){const directory=path.join(dataDir,child);fs.mkdirSync(directory,{recursive:true});if(!inside(root,fs.realpathSync(directory)))throw Error('PROFILE_SYMLINK_OUTSIDE_DATA');app.setPath(name,directory);}
}
async function reloadWindows(windows,origin){
 await Promise.all([...windows].filter(window=>!window.isDestroyed()).map(async window=>{
  try{await window.loadURL(origin+'/');}
  catch{if(!window.isDestroyed())throw Object.assign(Error('LOCAL_UI_RELOAD_FAILED'),{code:'LOCAL_UI_RELOAD_FAILED'});}
 }));
}
async function bootstrap(electron,{show=true,createInitial=true,dataRelative='data-desktop',pollMs=500}={}){
 const {app,BrowserWindow,ipcMain,session}=electron;
 if(process.versions.electron!==packageInfo.devDependencies.electron)throw Error('ELECTRON_VERSION_MISMATCH');
 if(app.commandLine.hasSwitch('no-sandbox')||app.commandLine.hasSwitch('disable-web-security'))throw Error('UNSAFE_RUNTIME_FLAGS_REJECTED');
 const repoRoot=path.resolve(__dirname,'..'),dataDir=prepareData(repoRoot,dataRelative);app.enableSandbox();configurePaths(app,dataDir);await app.whenReady();
 const windows=new Set(),service=new ServiceManager(repoRoot,dataDir);let stopped=false,stopPromise=null,unbind,controller,client,restarting=false;
 const safeSend=(wc,channel,value)=>{try{if(!wc.isDestroyed())wc.send(channel,value);}catch{}};
 const publish=state=>{for(const window of windows)if(!window.isDestroyed())safeSend(window.webContents,'dsf:service:state',state);};
 service.on('state',state=>{if(state.state!=='ready'&&controller)for(const row of controller.windows.values())controller.reset(row.webContents);publish(state);});
 const stop=()=>{if(stopPromise)return stopPromise;stopped=true;stopPromise=Promise.resolve().then(async()=>{unbind?.();for(const window of [...windows])if(!window.isDestroyed())window.destroy();await service.stop();});return stopPromise;};
 const backend={};for(const method of ['request','getTask','controlTask','exportTask'])backend[method]=(...args)=>{if(service.state!=='ready'||!client)return Promise.reject(Object.assign(Error('LOCAL_SERVICE_NOT_READY'),{code:'LOCAL_SERVICE_NOT_READY'}));return client[method](...args);};
 async function restart(){
  await service.stop();if(stopped)return;
  await service.start();if(stopped){await service.stop();return;}
  client=new LocalBackend(service.origin,{instanceId:service.instanceId});controller.replaceBackend(service.origin,backend);
  await reloadWindows(windows,service.origin);
 }
 function acceptRestart(){
  if(stopped||restarting||['starting','stopping'].includes(service.state))throw Object.assign(Error('SERVICE_RESTART_IN_PROGRESS'),{code:'SERVICE_RESTART_IN_PROGRESS'});
  restarting=true;setImmediate(()=>restart().catch(error=>publish({...service.status(),state:'failed',error:['LOCAL_UI_RELOAD_FAILED','LOCAL_SERVICE_STOP_FORCED','LOCAL_SERVICE_STOP_NONZERO','LOCAL_SERVICE_STOP_FAILED','LOCAL_SERVICE_STOP_UNCONFIRMED'].includes(error.code)?error.code:'LOCAL_SERVICE_RESTART_FAILED'})).finally(()=>{restarting=false;}));
  return {accepted:true,operation:'owned_local_service_restart'};
 }
 try{
  await service.start();client=new LocalBackend(service.origin,{instanceId:service.instanceId});
  controller=new IpcController({origin:service.origin,backend,pollMs,send:safeSend,writeExport:exportWriter(dataDir),restartService:acceptRestart,runtimeInfo:()=>({protocol:1,source:'test_fixture',shellVersion:packageInfo.version,electron:process.versions.electron,localServiceAvailable:service.state==='ready',serviceState:service.status(),realConnectionsEnabled:false,published:0,nativeAcceptance:'requires recorded native test'})});
  unbind=bindIpc(ipcMain,controller);const partition='dsf-fixture-'+randomUUID();protectSession(session.fromPartition(partition),()=>service.state==='ready'?service.origin:null);
  async function createWindow(){if(stopped)throw Error('SHELL_STOPPED');const window=new BrowserWindow(windowOptions({partition,show}));windows.add(window);window.once('closed',()=>windows.delete(window));controller.register(window.webContents);protectWindow(window,()=>controller.origin,controller);await window.loadURL(service.origin+'/');return window;}
  if(createInitial)await createWindow();
  return {app,controller,backend,service,dataDir,createWindow,stop,restart,windows,get origin(){return service.origin;}};
 }catch(error){await stop();throw error;}
}
module.exports={bootstrap,prepareData,configurePaths,reloadWindows};
