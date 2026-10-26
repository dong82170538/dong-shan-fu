'use strict';
const {spawn}=require('node:child_process');
const {EventEmitter}=require('node:events');
const {randomUUID}=require('node:crypto');
const path=require('node:path');const fs=require('node:fs');
const {LocalBackend}=require('./backend-client.cjs');
function error(code){return Object.assign(Error(code),{code});}
function closed(child){return child.exitCode!==null||child.signalCode!==null;}
const STOP_ERRORS=new Set(['LOCAL_SERVICE_STOP_FORCED','LOCAL_SERVICE_STOP_NONZERO','LOCAL_SERVICE_STOP_FAILED','LOCAL_SERVICE_STOP_UNCONFIRMED']);
function stopError(problem){return STOP_ERRORS.has(problem?.code)?problem:error('LOCAL_SERVICE_STOP_UNCONFIRMED');}
function stopOwned(record,{graceMs=1500,killMs=1500}={}){
 if(!record)return Promise.resolve();
 // A confirmed close does not erase a previous forced/failed stop operation.
 if(record.stopPromise)return record.stopPromise;
 if(record.closed||closed(record.child)){
  record.closed=true;record.stopResult={status:'ALREADY_EXITED',forced:false,cleanupConfirmed:true,exitCode:record.child.exitCode,signalCode:record.child.signalCode};return Promise.resolve();
 }
 record.stopPromise=new Promise((resolve,reject)=>{
  let settled=false,force,deadline,forced=false,requestedSignal=null;
  const finish=problem=>{
   if(settled)return;settled=true;clearTimeout(force);clearTimeout(deadline);record.child.removeListener('close',onClose);
   record.stopResult={status:problem?'FAIL':'PASS',reason:problem?.code||'owned_shutdown_complete',forced,cleanupConfirmed:record.closed,exitCode:record.child.exitCode,signalCode:record.child.signalCode};
   if(problem){record.stopError=problem;reject(problem);}else resolve();
  };
  const onClose=()=>{
   record.closed=true;
   if(forced){finish(error('LOCAL_SERVICE_STOP_FORCED'));return;}
   // An owned SIGTERM before the Unix service installs handlers remains the
   // intentional startup-cancel path. SIGKILL and nonzero never pass.
   if(record.child.exitCode!==0&&!(process.platform!=='win32'&&requestedSignal==='SIGTERM'&&record.child.signalCode==='SIGTERM')){finish(error('LOCAL_SERVICE_STOP_NONZERO'));return;}
   finish();
  };
  record.child.once('close',onClose);
  try{if(process.platform==='win32'&&record.child.stdin?.writable){record.child.stdin.end('DSF_SHUTDOWN\n');}else{requestedSignal='SIGTERM';record.child.kill(requestedSignal);}}
  catch{finish(error('LOCAL_SERVICE_STOP_FAILED'));return;}
  force=setTimeout(()=>{
   if(!record.closed&&!closed(record.child)){
    forced=true; // Set before kill: a concurrent close can never hide fallback.
    try{record.child.kill('SIGKILL');}catch{finish(error('LOCAL_SERVICE_STOP_UNCONFIRMED'));}
   }
  },graceMs);
  deadline=setTimeout(()=>finish(error('LOCAL_SERVICE_STOP_UNCONFIRMED')),graceMs+killMs);
 });return record.stopPromise;
}
class ServiceManager extends EventEmitter {
 constructor(repoRoot,dataDir,{python=process.env.DSF_PYTHON||(process.platform==='win32'?'python':'python3'),port=0,readyTimeoutMs=10000,onExit=()=>{}}={}){
  super();if(!Number.isInteger(port)||port<0||port>65535)throw error('INVALID_LOCAL_PORT');
  this.repoRoot=path.resolve(repoRoot);this.dataDir=path.resolve(dataDir);this.python=python;this.port=port;this.readyTimeoutMs=readyTimeoutMs;this.onExit=onExit;
  this.state='stopped';this.origin=null;this.instanceId=null;this.current=null;this.startPromise=null;this.stopPromise=null;this.generation=0;this.lastError=null;this.lastStopResult=null;
 }
 status(){return {state:this.state,origin:this.origin,pid:this.current?.child.pid||null,instanceId:this.instanceId,error:this.lastError,realConnectionsEnabled:false,stopResult:this.lastStopResult};}
 _state(state,code=null){this.state=state;this.lastError=code;this.emit('state',this.status());}
 start(){
  if(this.current?.stopError)return Promise.reject(this.current.stopError);
  if(this.state==='ready')return Promise.resolve(this);if(this.startPromise)return this.startPromise;
  if(this.state==='stopping')return Promise.reject(error('LOCAL_SERVICE_STOPPING'));
  if(this.current&&!this.current.closed)return Promise.reject(error('LOCAL_SERVICE_STOP_UNCONFIRMED'));
  const generation=++this.generation;this.origin=null;this.instanceId=randomUUID().replaceAll('-','');
  try{fs.mkdirSync(this.dataDir,{recursive:true});if(!fs.statSync(this.dataDir).isDirectory())throw error('DATA_DIRECTORY_INVALID');const db=path.join(this.dataDir,'commerce.sqlite3');if(fs.existsSync(db)&&(!fs.lstatSync(db).isFile()||fs.lstatSync(db).isSymbolicLink()))throw error('DATA_FILE_INVALID');}
  catch{this._state('failed','DATA_DIRECTORY_INVALID');return Promise.reject(error('DATA_DIRECTORY_INVALID'));}
  const child=spawn(this.python,['-u','-X','utf8','-m','commerce_core','--db',path.join(this.dataDir,'commerce.sqlite3'),'serve','--port',String(this.port),'--concurrency','2',...(process.platform==='win32'?['--stdio-control']:[])],{cwd:this.repoRoot,shell:false,windowsHide:true,stdio:[process.platform==='win32'?'pipe':'ignore','pipe','pipe'],env:{...process.env,PYTHONPATH:this.repoRoot,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',DSF_SERVICE_INSTANCE:this.instanceId}});
  const record={child,closed:false,stopPromise:null,generation};this.current=record;let buffer='',settled=false,checking=false,resolveStart,rejectStart;
  const pending=new Promise((resolve,reject)=>{resolveStart=resolve;rejectStart=reject;});this.startPromise=pending;
  const timer=setTimeout(()=>void fail('LOCAL_SERVICE_START_TIMEOUT'),this.readyTimeoutMs);
  const finish=(problem)=>{if(settled)return;settled=true;clearTimeout(timer);record.failStart=null;if(this.startPromise===pending)this.startPromise=null;if(problem)rejectStart(problem);else resolveStart(this);};
  const fail=async code=>{if(settled)return;this._state(this.state==='stopping'?'stopping':'failed',code);try{await stopOwned(record);}catch(problem){code=stopError(problem).code;}finish(error(code));};record.failStart=fail;
  if(child.stdin)child.stdin.on('error',()=>{});child.once('error',()=>void fail('PYTHON_UNAVAILABLE'));child.stderr.on('data',()=>{});
  child.once('close',()=>{record.closed=true;if(this.current!==record||generation!==this.generation)return;this.origin=null;if(!settled){const code=this.state==='stopping'?'LOCAL_SERVICE_STOPPED':(this.lastError||'LOCAL_SERVICE_EXITED');if(this.state!=='stopping')this._state('failed',code);finish(error(code));}else if(this.state==='ready'){this._state('failed','LOCAL_SERVICE_EXITED');this.onExit();}});
  child.stdout.on('data',chunk=>{
   if(settled||checking||this.current!==record)return;buffer+=chunk.toString('utf8');if(buffer.length>8192){void fail('INVALID_SERVICE_START');return;}
   const match=buffer.match(/^Fixture-only UI: (http:\/\/127\.0\.0\.1:(\d+)) \| DB:/m);if(!match)return;const reportedPort=Number(match[2]);if(reportedPort<1||reportedPort>65535||(this.port!==0&&reportedPort!==this.port)){void fail('INVALID_SERVICE_START');return;}
   checking=true;const origin=match[1];
   new LocalBackend(origin,{instanceId:this.instanceId,timeoutMs:1500}).request('GET','/api/health').then(health=>{
    if(settled||this.current!==record||this.state!=='starting'||record.closed)return;
    if(health.service_instance!==this.instanceId||health.health!=='ready'){void fail('SERVICE_IDENTITY_MISMATCH');return;}
    this.origin=origin;finish();this._state('ready');
   }).catch(()=>void fail('LOCAL_SERVICE_NOT_READY'));
  });
  // Publish only after the owned record, handlers and operation promise exist.
  // A synchronous state observer may stop/reenter the manager.
  this._state('starting');return pending;
 }
 stop(){
  if(this.stopPromise)return this.stopPromise;if(this.state==='stopped'&&!this.current)return Promise.resolve();
  const record=this.current;
  const pending=Promise.resolve().then(async()=>{try{await stopOwned(record);this.lastStopResult=record?.stopResult||null;if(record?.failStart)record.failStart('LOCAL_SERVICE_STOPPED');this.current=null;this.origin=null;this._state('stopped');}catch(problem){const failure=stopError(problem);this.lastStopResult=record?.stopResult||null;this.origin=null;this._state('failed',failure.code);throw failure;}finally{if(this.stopPromise===pending)this.stopPromise=null;}});
  this.stopPromise=pending;this._state('stopping');return pending;
 }
 async restart(){await this.stop();return this.start();}
 get child(){return this.current?.child;}
 get exited(){return this.state!=='ready';}
}
async function startService(repoRoot,dataDir,options){const manager=new ServiceManager(repoRoot,dataDir,options);await manager.start();return manager;}
function stopService(state){return state?.stop?state.stop():Promise.resolve();}
module.exports={ServiceManager,startService,stopService};
