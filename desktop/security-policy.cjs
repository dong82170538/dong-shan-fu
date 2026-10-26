'use strict';
const path=require('node:path');
const {trustedURL}=require('./ipc-controller.cjs');
function windowOptions({preload=path.join(__dirname,'preload.cjs'),partition,show=true}={}){
 return {width:1440,height:1050,minWidth:760,minHeight:620,show,autoHideMenuBar:true,title:'东山 · 本地任务助手',webPreferences:{preload,partition,contextIsolation:true,nodeIntegration:false,nodeIntegrationInWorker:false,nodeIntegrationInSubFrames:false,sandbox:true,webSecurity:true,allowRunningInsecureContent:false,webviewTag:false,navigateOnDragDrop:false,spellcheck:false}};
}
function protectSession(session,origin){
 session.setPermissionRequestHandler((_webContents,_permission,callback)=>callback(false));
 session.setPermissionCheckHandler(()=>false);
 session.webRequest.onBeforeRequest((details,callback)=>{
  let allowed=false;
  try{const u=new URL(details.url);const currentOrigin=typeof origin==='function'?origin():origin;allowed=u.origin===currentOrigin&&u.protocol==='http:'&&!u.username&&!u.password&&details.resourceType!=='subFrame'&&(details.resourceType!=='mainFrame'||trustedURL(details.url,currentOrigin));}catch{}
  callback({cancel:!allowed});
 });
 session.on('will-download',(event)=>event.preventDefault()); // Managed exports only, never renderer-supplied filesystem paths.
}
function protectWindow(window,origin,controller){
 const wc=window.webContents;const currentOrigin=()=>typeof origin==='function'?origin():origin;
 wc.setWindowOpenHandler(()=>({action:'deny'}));
 wc.on('will-attach-webview',event=>event.preventDefault());
 const navigation=(details,url,isSameDocument,isMainFrame)=>({url:typeof details.url==='string'?details.url:(typeof url==='string'?url:url?.url),isMainFrame:typeof details.isMainFrame==='boolean'?details.isMainFrame:(typeof url?.isMainFrame==='boolean'?url.isMainFrame:isMainFrame),isSameDocument:typeof details.isSameDocument==='boolean'?details.isSameDocument:isSameDocument});
 wc.on('will-navigate',(event,url)=>{if(!trustedURL(navigation(event,url).url,currentOrigin()))event.preventDefault();});
 wc.on('will-frame-navigate',(event,url,same,main)=>{const details=navigation(event,url,same,main);if(!details.isMainFrame||!trustedURL(details.url,currentOrigin()))event.preventDefault();});
 wc.on('will-redirect',(event,url)=>{if(!trustedURL(navigation(event,url).url,currentOrigin()))event.preventDefault();});
 wc.on('did-start-navigation',(event,url,same,main)=>{const details=navigation(event,url,same,main);if(details.isMainFrame&&!details.isSameDocument)controller.reset(wc);});
 wc.on('render-process-gone',()=>controller.reset(wc));
 wc.once('destroyed',()=>controller.remove(wc));
}
module.exports={windowOptions,protectSession,protectWindow};
