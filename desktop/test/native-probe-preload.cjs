'use strict';
// Native acceptance fixture only. Production BrowserWindows always use ../preload.cjs.
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('nativeProbe',{invoke:async(channel,payload)=>{try{return await ipcRenderer.invoke(channel,payload);}catch{return {ok:false,error:{code:'NO_REGISTERED_HANDLER'}};}}});
