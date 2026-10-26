'use strict';
const {spawn}=require('node:child_process');
const {EventEmitter}=require('node:events');
const {randomUUID}=require('node:crypto');
const path=require('node:path');const fs=require('node:fs');
const {LocalBackend}=require('./backend-client.cjs');
function error(code){return Object.assign(Error(code),{code});}
function closed(child){return child.exitCode!==null||child.signalCode!==null;}
function stopOwned(record,{graceMs=1500,killMs=1500}={}){
 if(!record||record.closed)return Promise.resolve();if(record.stopPromise)return record.stopPromise;
 record.stopPromise=new Promise((resolve,reject)=>{
  let settled=false,force,deadline;
  const finish=problem=>{if(settled)return;settled=true;clearTimeout(force);clearTimeout(deadline);record.child.removeListener('close',onClose);if(problem)reject(problem);else resolve();};
  const onClose=()=>{record.closed=true;finish();};record.child.once('close',onClose);
  if(record.closed||closed(record.child)){finish();return;}
  try{record.child.kill('SIGTERM');}catch{finish(error('LOCAL_SERVICE_STOP_FAILED'));return;}
  force=setTimeout(()=>{if(!record.closed&&!closed(record.child)){try{record.child.kill('SIGKILL');}catch{}}},graceMs);
  deadline=setTimeout(()=>record.closed||closed(record.child)?finish():finish(error('LOCAL_SERVICE_STOP_UNCONFIRMED')),graceMs+killMs);
 });return record.stopPromise;
}
class ServiceManager extends EventEmitter {
 constructor(repoRoot,dataDir,{python=process.env.DSF_PYTHON||(process.platform==='win32'?'python':'python3'),port=0,readyTimeoutMs=10000,onExit=()=>{}}={}){
  super();if(!Number.isInteger(port)||port<0||port>65535)throw error('INVALID_LOCAL_PORT');
  this.repoRoot=path.resolve(repoRoot);this.dataDir=path.resolve(dataDir);this.python=python;this.port=port;this.readyTimeoutMs=readyTimeoutMs;this.onExit=onExit;
  this.state='stopped';this.origin=null;this.instanceId=null;this.current=null;this.startPromise=null;this.stopPromise=null;this.generation=0;this.lastError=null;
 }
 status(){return {state:this.state,origin:this.origin,pid:this.current?.child.pid||null,instanceId:this.instanceId,error:this.lastError,realConnectionsEnabled:false};}
 _state(state,code=null){this.state=state;this.lastError=code;this.emit('state',this.status());}
 start(){
  if(this.state==='ready')return Promise.resolve(this);if(this.startPromise)return this.startPromise;
  if(this.state==='stopping')return Promise.reject(error('LOCAL_SERVICE_STOPPING'));
  if(this.current&&!this.current.closed)return Promise.reject(error('LOCAL_SERVICE_STOP_UNCONFIRMED'));
  const generation=++this.generation;this.origin=null;this.instanceId=randomUUID().replaceAll('-','');
  try{fs.mkdirSync(this.dataDir,{recursive:true});if(!fs.statSync(this.dataDir).isDirectory())throw error('DATA_DIRECTORY_INVALID');const db=path.join(this.dataDir,'commerce.sqlite3');if(fs.existsSync(db)&&(!fs.lstatSync(db).isFile()||fs.lstatSync(db).isSymbolicLink()))throw error('DATA_FILE_INVALID');}
  catch{this._state('failed','DATA_DIRECTORY_INVALID');return Promise.reject(error('DATA_DIRECTORY_INVALID'));}
  const child=spawn(this.python,['-u','-X','utf8','-m','commerce_core','--db',path.join(this.dataDir,'commerce.sqlite3'),'serve','--port',String(this.port),'--concurrency','2'],{cwd:this.repoRoot,shell:false,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONPATH:this.repoRoot,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',DSF_SERVICE_INSTANCE:this.instanceId}});
  const record={child,closed:false,stopPromise:null,generation};this.current=record;let buffer='',settled=false,checking=false,resolveStart,rejectStart;
  const pending=new Promise((resolve,reject)=>{resolveStart=resolve;rejectStart=reject;});this.startPromise=pending;
  const timer=setTimeout(()=>void fail('LOCAL_SERVICE_START_TIMEOUT'),this.readyTimeoutMs);
  const finish=(problem)=>{if(settled)return;settled=true;clearTimeout(timer);record.failStart=null;if(this.startPromise===pending)this.startPromise=null;if(problem)rejectStart(problem);else resolveStart(this);};
  const fail=async code=>{if(settled)return;this._state(this.state==='stopping'?'stopping':'failed',code);try{await stopOwned(record);}catch{code='LOCAL_SERVICE_STOP_UNCONFIRMED';}finish(error(code));};record.failStart=fail;
  child.once('error',()=>void fail('PYTHON_UNAVAILABLE'));child.stderr.on('data',()=>{});
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
  const pending=Promise.resolve().then(async()=>{try{await stopOwned(record);if(record?.failStart)record.failStart('LOCAL_SERVICE_STOPPED');this.current=null;this.origin=null;this._state('stopped');}catch{this._state('failed','LOCAL_SERVICE_STOP_UNCONFIRMED');throw error('LOCAL_SERVICE_STOP_UNCONFIRMED');}finally{if(this.stopPromise===pending)this.stopPromise=null;}});
  this.stopPromise=pending;this._state('stopping');return pending;
 }
 async restart(){await this.stop();return this.start();}
 get child(){return this.current?.child;}
 get exited(){return this.state!=='ready';}
}
async function startService(repoRoot,dataDir,options){const manager=new ServiceManager(repoRoot,dataDir,options);await manager.start();return manager;}
function stopService(state){return state?.stop?state.stop():Promise.resolve();}
module.exports={ServiceManager,startService,stopService};
