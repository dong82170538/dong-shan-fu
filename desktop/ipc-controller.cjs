'use strict';
// No Electron import: the same controller is used by real ipcMain and Node unit tests.
const CHANNELS=Object.freeze({runtime:'dsf:runtime',restart:'dsf:service:restart',watch:'dsf:task:watch',unwatch:'dsf:task:unwatch',get:'dsf:task:get',control:'dsf:task:control',export:'dsf:task:export'});
const UPDATE='dsf:watch:update';
class BridgeError extends Error { constructor(code){super(code);this.code=code;} }
function insist(condition,code){if(!condition)throw new BridgeError(code);}
function plain(value){return value!==null&&typeof value==='object'&&!Array.isArray(value)&&(Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null);}
function validate(channel,value){
  insist(plain(value),'INVALID_INPUT');
  const extras={runtime:[],restart:[],watch:['taskId','subscriptionId'],unwatch:['subscriptionId'],get:['taskId'],control:['taskId','action'],export:['taskId','format']};
  const method=Object.keys(CHANNELS).find(key=>CHANNELS[key]===channel);insist(method,'CHANNEL_UNAVAILABLE');
  const allowed=new Set(['protocol','requestId',...extras[method]]);
  insist(Object.keys(value).every(key=>allowed.has(key))&&value.protocol===1,'INVALID_INPUT');
  insist(typeof value.requestId==='string'&&/^[A-Za-z0-9_-]{1,80}$/.test(value.requestId),'INVALID_INPUT');
  if(extras[method].includes('taskId'))insist(typeof value.taskId==='string'&&/^[a-f0-9]{32}$/.test(value.taskId),'INVALID_INPUT');
  if(extras[method].includes('subscriptionId'))insist(typeof value.subscriptionId==='string'&&/^[A-Za-z0-9_-]{1,96}$/.test(value.subscriptionId),'INVALID_INPUT');
  if(method==='control')insist(['pause','resume','cancel','retry'].includes(value.action),'INVALID_INPUT');
  if(method==='export')insist(['json','csv'].includes(value.format),'INVALID_INPUT');
  return method;
}
function trustedURL(value,origin){try{const u=new URL(value);return u.origin===origin&&u.pathname==='/'&&!u.search&&!u.hash&&!u.username&&!u.password;}catch{return false;}}
class IpcController {
  constructor({origin,backend,send,writeExport,runtimeInfo=()=>({source:'test_fixture',protocol:1}),restartService=null,pollMs=500,timeoutMs=4000}){
    const url=new URL(origin);insist(url.protocol==='http:'&&url.hostname==='127.0.0.1'&&url.origin===origin,'INVALID_BACKEND');
    this.origin=origin;this.backend=backend;this.send=send;this.writeExport=writeExport;this.runtimeInfo=runtimeInfo;this.restartService=restartService;this.pollMs=pollMs;this.timeoutMs=timeoutMs;this.windows=new Map();
  }
  register(webContents){insist(!this.windows.has(webContents.id),'WINDOW_ALREADY_REGISTERED');this.windows.set(webContents.id,{webContents,generation:0,subscriptions:new Map(),pending:new Map(),recent:new Set()});}
  reset(webContents){const row=this.windows.get(webContents.id);if(!row)return;row.generation++;for(const pending of row.pending.values())pending.abort();row.pending.clear();for(const sub of row.subscriptions.values()){clearInterval(sub.timer);sub.controller?.abort();}row.subscriptions.clear();}
  remove(webContents){this.reset(webContents);this.windows.delete(webContents.id);}
  _sender(event){const row=this.windows.get(event.sender?.id);insist(row&&row.webContents===event.sender&&!event.sender.isDestroyed(),'REJECTED_SENDER');insist(event.senderFrame===event.sender.mainFrame&&trustedURL(event.senderFrame?.url,this.origin),'REJECTED_FRAME');return row;}
  _live(row,generation){insist(this.windows.get(row.webContents.id)===row&&row.generation===generation&&!row.webContents.isDestroyed()&&trustedURL(row.webContents.mainFrame?.url,this.origin),'STALE_CONTEXT');}
  _bound(row,taskId){insist([...row.subscriptions.values()].some(sub=>sub.taskId===taskId&&sub.active),'TASK_NOT_BOUND');}
  _check(row,generation,abort){insist(!abort.signal.aborted,'STALE_CONTEXT');this._live(row,generation);}
  _bounded(work,abort){
    return new Promise((resolve,reject)=>{
      let settled=false,timedOut=false;
      const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);abort.signal.removeEventListener('abort',stopped);if(error)reject(error);else resolve(value);};
      const stopped=()=>finish(new BridgeError(timedOut?'IPC_TIMEOUT':'STALE_CONTEXT'));
      const timer=setTimeout(()=>{timedOut=true;abort.abort();},this.timeoutMs);
      abort.signal.addEventListener('abort',stopped,{once:true});if(abort.signal.aborted)stopped();
      Promise.resolve(work).then(value=>finish(null,value),error=>finish(error));
    });
  }
  async invoke(event,channel,payload){
    let requestId=null,row,generation,abort,method,success=false;
    try{
      row=this._sender(event);method=validate(channel,payload);requestId=payload.requestId;generation=row.generation;
      insist(!row.pending.has(requestId)&&!row.recent.has(requestId),'REQUEST_REPLAY');insist(row.pending.size<8,'REQUEST_LIMIT');
      abort=new AbortController();row.pending.set(requestId,abort);
      const data=await this._bounded(this._perform(row,generation,method,payload,abort),abort);
      this._check(row,generation,abort);success=true;return {ok:true,requestId,data};
    }catch(error){const safe=new Set(['LOCAL_SERVICE_UNAVAILABLE','LOCAL_SERVICE_TIMEOUT','REQUEST_ABORTED','RESULT_TOO_LARGE','TASK_CONFLICT','AUTH_REQUIRED','LOCAL_BACKEND_REJECTED','INVALID_BACKEND_RESPONSE','SERVICE_IDENTITY_MISMATCH','LOCAL_SERVICE_NOT_READY','SERVICE_RESTART_IN_PROGRESS']);return {ok:false,requestId,error:{code:error instanceof BridgeError?error.code:(safe.has(error.code)?error.code:'LOCAL_OPERATION_FAILED')}};}
    finally{
      if(row&&abort&&!success&&method==='watch'){
        const sub=row.subscriptions.get(payload.subscriptionId);
        if(sub?.ownerRequest===requestId){clearInterval(sub.timer);sub.controller?.abort();row.subscriptions.delete(sub.id);}
      }
      if(row&&abort&&row.pending.get(requestId)===abort){row.pending.delete(requestId);row.recent.add(requestId);if(row.recent.size>256)row.recent.delete(row.recent.values().next().value);}
    }
  }
  async _perform(row,generation,method,payload,abort){
    if(method==='runtime')return this.runtimeInfo();
    if(method==='restart'){insist(typeof this.restartService==='function','SERVICE_RESTART_UNAVAILABLE');return this.restartService();}
    if(method==='watch'){
      insist(row.subscriptions.size<4,'SUBSCRIPTION_LIMIT');insist(!row.subscriptions.has(payload.subscriptionId),'SUBSCRIPTION_EXISTS');
      const sub={taskId:payload.taskId,id:payload.subscriptionId,ownerRequest:payload.requestId,active:false,timer:null,controller:null};row.subscriptions.set(sub.id,sub);
      const task=await this.backend.getTask(sub.taskId,abort.signal);this._check(row,generation,abort);insist(row.subscriptions.get(sub.id)===sub,'STALE_SUBSCRIPTION');
      sub.active=true;sub.timer=setInterval(()=>this._poll(row,generation,sub),this.pollMs);sub.timer.unref?.();return {subscriptionId:sub.id,task};
    }
    if(method==='unwatch'){
      const sub=row.subscriptions.get(payload.subscriptionId);insist(sub,'SUBSCRIPTION_NOT_FOUND');clearInterval(sub.timer);sub.controller?.abort();row.subscriptions.delete(sub.id);return {removed:true};
    }
    this._bound(row,payload.taskId);
    let data;
    if(method==='get')data=await this.backend.getTask(payload.taskId,abort.signal);
    else if(method==='control')data=await this.backend.controlTask(payload.taskId,payload.action,abort.signal);
    else{
      const bytes=await this.backend.exportTask(payload.taskId,payload.format,abort.signal);
      this._check(row,generation,abort);this._bound(row,payload.taskId);
      data=await this.writeExport(payload.taskId,payload.format,bytes,()=>{this._check(row,generation,abort);this._bound(row,payload.taskId);});
    }
    this._check(row,generation,abort);this._bound(row,payload.taskId);return data;
  }
  async _poll(row,generation,sub){
    if(sub.controller||row.subscriptions.get(sub.id)!==sub||!sub.active)return;
    const abort=new AbortController();sub.controller=abort;
    try{const task=await this._bounded(this.backend.getTask(sub.taskId,abort.signal),abort);this._check(row,generation,abort);if(row.subscriptions.get(sub.id)!==sub||abort.signal.aborted)return;this._emit(row.webContents,UPDATE,{protocol:1,subscriptionId:sub.id,taskId:sub.taskId,task});}
    catch{if(row.generation===generation&&row.subscriptions.get(sub.id)===sub&&!abort.signal.aborted)this._emit(row.webContents,UPDATE,{protocol:1,subscriptionId:sub.id,taskId:sub.taskId,error:{code:'LOCAL_SERVICE_UNAVAILABLE'}});}
    finally{if(sub.controller===abort)sub.controller=null;}
  }
  _emit(wc,channel,data){try{this.send(wc,channel,data);}catch{}}
  replaceBackend(origin,backend){const url=new URL(origin);insist(url.protocol==='http:'&&url.hostname==='127.0.0.1'&&url.origin===origin,'INVALID_BACKEND');for(const row of this.windows.values())this.reset(row.webContents);this.origin=origin;this.backend=backend;}
  diagnostics(){return {windows:this.windows.size,subscriptions:[...this.windows.values()].reduce((n,row)=>n+row.subscriptions.size,0),pending:[...this.windows.values()].reduce((n,row)=>n+row.pending.size,0)};}
  close(){for(const row of [...this.windows.values()])this.remove(row.webContents);}
}
function bindIpc(ipcMain,controller){for(const channel of Object.values(CHANNELS))ipcMain.handle(channel,(event,payload)=>controller.invoke(event,channel,payload));return ()=>{for(const channel of Object.values(CHANNELS))ipcMain.removeHandler(channel);controller.close();};}
module.exports={IpcController,BridgeError,CHANNELS,UPDATE,validate,trustedURL,bindIpc};
