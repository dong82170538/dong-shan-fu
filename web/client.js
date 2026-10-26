(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.DsfTaskClient=factory();})(globalThis,function(){
 'use strict';
 function problem(code){return Object.assign(new Error(code),{code});}
 function unwrap(response){if(!response?.ok)throw problem(response?.error?.code||'IPC_UNAVAILABLE');return response.data;}
 class TaskClient {
  constructor({request,bridge=null}){this.request=request;this.bridge=bridge;this.current=null;this.generation=0;this.serviceReady=!bridge;}
  get mode(){return this.bridge?'desktop':'web';}
  setServiceState(state){this.serviceReady=state==='ready';if(!this.serviceReady)void this.unbind();}
  importFixture(value){return this.request('/api/fixtures/import',value);}
  submit(value){return this.request('/api/tasks',value);}
  async bind(taskId,onUpdate=()=>{}){
   if(!this.bridge)return {taskId,mode:'web'};
   if(!this.serviceReady)throw problem('LOCAL_SERVICE_UNAVAILABLE');
   if(this.current?.taskId===taskId)return this.current.promise;
   const previous=this.current,entry={taskId,generation:++this.generation,subscriptionId:null,promise:null};this.current=entry;
   entry.promise=(async()=>{
    if(previous?.subscriptionId)await this.bridge.unwatchTask(previous.subscriptionId);
    if(this.current!==entry)throw problem('TASK_SELECTION_CHANGED');
    const data=unwrap(await this.bridge.watchTask(taskId,message=>{if(this.current===entry&&this.serviceReady&&message.taskId===taskId&&(!message.task||message.task.id===taskId))onUpdate(message);}));
    entry.subscriptionId=data.subscriptionId;
    if(this.current!==entry||!this.serviceReady){await this.bridge.unwatchTask(data.subscriptionId);throw problem('TASK_SELECTION_CHANGED');}
    return data;
   })().catch(error=>{if(this.current===entry)this.current=null;throw error;});return entry.promise;
  }
  async unbind(){const old=this.current;this.current=null;this.generation++;if(old?.subscriptionId&&this.bridge)try{await this.bridge.unwatchTask(old.subscriptionId);}catch{}}
  async control(taskId,action){
   if(!['pause','resume','cancel','retry'].includes(action))throw problem('INVALID_ACTION');
   if(!this.bridge)return this.request('/api/tasks/'+taskId+'/'+action,{});
   await this.bind(taskId);if(this.current?.taskId!==taskId||!this.serviceReady)throw problem('TASK_SELECTION_CHANGED');
   return unwrap(await this.bridge.controlTask(taskId,action));
  }
  async exportTask(taskId,format){
   if(!['json','csv'].includes(format))throw problem('INVALID_FORMAT');
   if(!this.bridge)return {kind:'download',href:'/api/tasks/'+taskId+'/export.'+format};
   await this.bind(taskId);if(this.current?.taskId!==taskId||!this.serviceReady)throw problem('TASK_SELECTION_CHANGED');
   return {kind:'managed',...unwrap(await this.bridge.exportTask(taskId,format))};
  }
  async restartService(){if(!this.bridge?.restartService)throw problem('SERVICE_RESTART_UNAVAILABLE');return unwrap(await this.bridge.restartService());}
 }
 return TaskClient;
});
