'use strict';
const http=require('node:http');
class LocalBackend {
  constructor(origin,{timeoutMs=3000,maxBytes=12_000_000,instanceId=null}={}){const u=new URL(origin);if(u.protocol!=='http:'||u.hostname!=='127.0.0.1'||u.origin!==origin)throw Error('INVALID_BACKEND');this.origin=origin;this.timeoutMs=timeoutMs;this.maxBytes=maxBytes;this.instanceId=instanceId;}
  request(method,path,body,signal,bytes=false){
    if(!path.startsWith('/api/')||path.includes('..')||path.includes('://'))return Promise.reject(Object.assign(Error('INVALID_PATH'),{code:'INVALID_PATH'}));
    return new Promise((resolve,reject)=>{
      let settled=false,size=0;const chunks=[];const timer=setTimeout(()=>finish(Object.assign(Error('LOCAL_SERVICE_TIMEOUT'),{code:'LOCAL_SERVICE_TIMEOUT'})),this.timeoutMs);
      const finish=(error,data)=>{if(settled)return;settled=true;clearTimeout(timer);signal?.removeEventListener('abort',aborted);if(error){req.destroy();reject(error);}else resolve(data);};
      const aborted=()=>finish(Object.assign(Error('REQUEST_ABORTED'),{code:'REQUEST_ABORTED'}));
      const data=body===undefined?null:Buffer.from(JSON.stringify(body));
      const req=http.request(this.origin+path,{method,headers:{...(data?{'Content-Type':'application/json','X-Local-Client':'dong-shan-fu','Content-Length':data.length}:{}),...(this.instanceId?{'X-DSF-Service-Instance':this.instanceId}:{})},agent:false},response=>{
        if(this.instanceId&&response.headers['x-dsf-service-instance']!==this.instanceId){response.resume();return finish(Object.assign(Error('SERVICE_IDENTITY_MISMATCH'),{code:'SERVICE_IDENTITY_MISMATCH'}));}
        response.on('data',chunk=>{size+=chunk.length;if(size>this.maxBytes)return finish(Object.assign(Error('RESULT_TOO_LARGE'),{code:'RESULT_TOO_LARGE'}));chunks.push(chunk);});
        response.on('error',()=>finish(Object.assign(Error('LOCAL_SERVICE_UNAVAILABLE'),{code:'LOCAL_SERVICE_UNAVAILABLE'})));
        response.on('end',()=>{if(settled)return;if(response.statusCode<200||response.statusCode>=300)return finish(Object.assign(Error('LOCAL_BACKEND_REJECTED'),{code:response.statusCode===409?'TASK_CONFLICT':response.statusCode===401?'AUTH_REQUIRED':'LOCAL_BACKEND_REJECTED'}));try{const value=Buffer.concat(chunks);finish(null,bytes?value:JSON.parse(value.toString('utf8')));}catch{finish(Object.assign(Error('INVALID_BACKEND_RESPONSE'),{code:'INVALID_BACKEND_RESPONSE'}));}});
      });
      req.on('error',()=>finish(Object.assign(Error('LOCAL_SERVICE_UNAVAILABLE'),{code:'LOCAL_SERVICE_UNAVAILABLE'})));
      if(signal?.aborted){aborted();return;}signal?.addEventListener('abort',aborted,{once:true});if(data)req.write(data);req.end();
    });
  }
  getTask(id,signal){return this.request('GET','/api/tasks/'+id,undefined,signal);}
  controlTask(id,action,signal){return this.request('POST','/api/tasks/'+id+'/'+action,{},signal);}
  exportTask(id,format,signal){return this.request('GET','/api/tasks/'+id+'/export.'+format,undefined,signal,true);}
}
module.exports={LocalBackend};
