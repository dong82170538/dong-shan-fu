'use strict';
// Sandboxed preload: only Electron's permitted module; no filesystem, process spawning or generic invoke.
const {contextBridge,ipcRenderer}=require('electron');
const UPDATE='dsf:watch:update';
const SERVICE='dsf:service:state';
const serviceCallbacks=new Map();let serviceAttached=false;
function serviceDispatch(_event,state){for(const callback of serviceCallbacks.values()){try{callback(state);}catch{}}}
const callbacks=new Map();let sequence=0,attached=false;
function requestId(){return 'req-'+Date.now()+'-'+(++sequence);}
function dispatch(_event,message){const entry=callbacks.get(message?.subscriptionId);if(entry&&message.protocol===1&&entry.taskId===message.taskId){try{entry.callback(message);}catch{}}}
function detach(){if(attached&&callbacks.size===0){ipcRenderer.removeListener(UPDATE,dispatch);attached=false;}}
async function invoke(channel,body={}){return ipcRenderer.invoke(channel,{protocol:1,requestId:requestId(),...body});}
const api=Object.freeze({
  runtime:()=>invoke('dsf:runtime'),
  restartService:()=>invoke('dsf:service:restart'),
  onServiceState:callback=>{if(typeof callback!=='function')return null;const id='service-'+requestId();serviceCallbacks.set(id,callback);if(!serviceAttached){ipcRenderer.on(SERVICE,serviceDispatch);serviceAttached=true;}return id;},
  offServiceState:id=>{serviceCallbacks.delete(id);if(serviceAttached&&serviceCallbacks.size===0){ipcRenderer.removeListener(SERVICE,serviceDispatch);serviceAttached=false;}return {removed:true};},
  getTask:taskId=>invoke('dsf:task:get',{taskId}),
  controlTask:(taskId,action)=>invoke('dsf:task:control',{taskId,action}),
  exportTask:(taskId,format)=>invoke('dsf:task:export',{taskId,format}),
  watchTask:async(taskId,callback)=>{
    if(typeof callback!=='function')return {ok:false,error:{code:'INVALID_CALLBACK'}};
    const id='sub-'+requestId();callbacks.set(id,{taskId,callback});if(!attached){ipcRenderer.on(UPDATE,dispatch);attached=true;}
    try{const result=await invoke('dsf:task:watch',{taskId,subscriptionId:id});if(!result.ok){callbacks.delete(id);detach();}return result;}
    catch{callbacks.delete(id);detach();return {ok:false,error:{code:'IPC_UNAVAILABLE'}};}
  },
  unwatchTask:async subscriptionId=>{callbacks.delete(subscriptionId);detach();try{return await invoke('dsf:task:unwatch',{subscriptionId});}catch{return {ok:false,error:{code:'IPC_UNAVAILABLE'}};}},
  bridgeState:()=>({protocol:1,activeSubscriptions:callbacks.size,updateListeners:ipcRenderer.listenerCount(UPDATE),serviceListeners:ipcRenderer.listenerCount(SERVICE)})
});
contextBridge.exposeInMainWorld('dongshanDesktop',api);
window.addEventListener('beforeunload',()=>{for(const id of callbacks.keys())void api.unwatchTask(id);callbacks.clear();detach();for(const id of serviceCallbacks.keys())api.offServiceState(id);});
