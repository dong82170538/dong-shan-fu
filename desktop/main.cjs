'use strict';
const electron=require('electron');const {bootstrap}=require('./shell.cjs');
let shutdownPromise=null,shutdownComplete=false;const shellPromise=bootstrap(electron);
electron.app.on('before-quit',event=>{
 if(shutdownComplete)return;
 event.preventDefault();
 if(shutdownPromise)return;
 // Every reentrant quit remains blocked until our owned child has stopped.
 // Destroying the final window can synchronously request another app.quit().
 shutdownPromise=shellPromise.then(shell=>shell.stop()).then(()=>{
  shutdownComplete=true;electron.app.quit();
 }).catch(()=>{console.error('DESKTOP_SERVICE_STOP_UNCONFIRMED');});
});
electron.app.on('window-all-closed',()=>electron.app.quit());
shellPromise.catch(error=>{
 console.error('DESKTOP_START_FAILED');
 // bootstrap awaits owned-child cleanup before rejecting. If cleanup itself
 // failed, keep the quit gate closed rather than claim a clean shutdown.
 if(error.code!=='LOCAL_SERVICE_STOP_UNCONFIRMED')electron.app.exit(1);
});
