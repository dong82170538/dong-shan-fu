'use strict';
const ownOrigin = `chrome-extension://${chrome.runtime.id}/`;
function localBackend(value) {
  const url = new URL(value);
  const port = Number(url.port);
  if(url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password || url.search || url.hash || url.pathname !== '/' || !Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('LOCAL_FIXTURE_URL_REQUIRED');
  return url.origin;
}
chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(sender.id !== chrome.runtime.id || !sender.url?.startsWith(ownOrigin)) {sendResponse({ok:false,error:'REJECTED_SENDER'});return false;}
  if(!message || !['set_fixture_backend','preview_fixture'].includes(message.type)) {sendResponse({ok:false,error:'UNSUPPORTED_MESSAGE'});return false;}
  (async()=>{
    if(message.type==='set_fixture_backend') {
      const backend = localBackend(message.url);
      await chrome.storage.local.set({fixtureBackend:backend});
      return {ok:true,source:'test_fixture',real_connections_enabled:false};
    }
    if(message.body?.source!=='test_fixture' || message.body?.publish!==false) return {ok:false,error:'EXPLICIT_FIXTURE_REQUIRED'};
    const settings=await chrome.storage.local.get('fixtureBackend');
    const backend=localBackend(settings.fixtureBackend || 'http://127.0.0.1:8765');
    const response=await fetch(backend+'/api/extension/preview', {method:'POST',headers:{'Content-Type':'application/json','X-Local-Client':'dong-shan-fu'},credentials:'omit',redirect:'error',signal:AbortSignal.timeout(10000),body:JSON.stringify(message.body)});
    if(!response.ok)return {ok:false,error:'FIXTURE_BACKEND_REJECTED',status:response.status};
    return {ok:true,source:'test_fixture',result:await response.json(),publish_calls:0};
  })().then(sendResponse).catch(()=>sendResponse({ok:false,error:'LOCAL_FIXTURE_BACKEND_UNAVAILABLE'}));
  return true;
});
