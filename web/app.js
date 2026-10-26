'use strict';
const $=id=>document.getElementById(id);
let fixtureId=null,selectedId=null,polling=false,serviceSubscription=null,selectionGeneration=0,serviceGeneration=0,lastServiceInstance=null;
const bridge=window.dongshanDesktop||null;
const labels={queued:'排队中',running:'运行中',paused:'已暂停',interrupted:'中断 · 需恢复',completed:'本地完成',completed_with_errors:'完成 · 有失败',canceled:'已取消',pending:'待处理',retry_wait:'等待重试',failed:'失败',timed_out:'超时'};
const errors={INVALID_INPUT:'输入结构、字段或币种不符合要求，请检查 JSON。',CONFLICT:'幂等键或任务状态冲突，请检查当前任务。',TASK_CONFLICT:'任务状态已变化，请刷新查看。',AUTH_REQUIRED:'本地 fixture 会话未就绪；真实店铺仍不可用。',LOCAL_SERVER_BUSY:'本地服务繁忙，请稍后刷新。',LOCAL_SERVICE_UNAVAILABLE:'本地服务不可用，显示上次读取数据。',LOCAL_SERVICE_NOT_READY:'本地服务尚未就绪，任务操作未确认。',LOCAL_REQUEST_TIMEOUT:'请求超时，操作结果未确认，请刷新查看。',IPC_TIMEOUT:'桌面请求超时，操作结果未确认，请刷新查看。',IPC_UNAVAILABLE:'桌面通信不可用，请重新连接。',TASK_SELECTION_CHANGED:'选中的任务已变化，请重新操作。',STALE_CONTEXT:'窗口或连接已变化，请刷新任务。',TASK_NOT_BOUND:'任务尚未绑定当前窗口。',SERVICE_RESTART_IN_PROGRESS:'本地服务正在重启，请等待。',SERVICE_RESTART_UNAVAILABLE:'当前运行方式不支持桌面重启。'};
function element(tag,text,className){const value=document.createElement(tag);if(text!==undefined)value.textContent=text;if(className)value.className=className;return value;}
function feedback(text){$('feedback').textContent=text;}
function describe(error){return errors[error.code]||(error.name==='SyntaxError'?'JSON 格式不正确，请检查后重试。':'操作未完成，请检查本地服务与输入。');}
function connection(state,text){$('connection-status').textContent=text;$('connection-status').dataset.state=state;}
async function api(path,body){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);
 try{
  const response=await fetch(path,body===undefined?{signal:controller.signal}:{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json','X-Local-Client':'dong-shan-fu'},body:JSON.stringify(body)});
  let data;try{data=await response.json();}catch{throw Object.assign(Error('INVALID_RESPONSE'),{code:'LOCAL_SERVICE_UNAVAILABLE'});}
  if(!response.ok)throw Object.assign(Error(data.error||'REQUEST_FAILED'),{code:data.error||'LOCAL_SERVICE_UNAVAILABLE'});return data;
 }catch(error){if(error.name==='AbortError')throw Object.assign(Error('LOCAL_REQUEST_TIMEOUT'),{code:'LOCAL_REQUEST_TIMEOUT'});if(!error.code)error.code='LOCAL_SERVICE_UNAVAILABLE';throw error;}
 finally{clearTimeout(timer);}
}
const client=new DsfTaskClient({request:api,bridge});
function guarded(fn){return async event=>{const button=event?.currentTarget;if(button?.tagName==='BUTTON')button.disabled=true;try{await fn(event);}catch(error){feedback(describe(error));}finally{if(button?.tagName==='BUTTON')button.disabled=false;}};}
async function demo(){const [fixture,policy]=await Promise.all([api('/api/fixtures/demo'),api('/api/fixtures/policy')]);$('fixture-json').value=JSON.stringify(fixture,null,2);$('policy-json').value=JSON.stringify(policy,null,2);fixtureId=null;feedback('已载入虚构样例，尚未导入。');}
$('load-demo').addEventListener('click',guarded(demo));
$('fixture-file').addEventListener('change',guarded(async event=>{const file=event.target.files[0];if(!file)return;if(file.size>2000000)throw Object.assign(Error('FILE_LIMIT'),{code:'INVALID_INPUT'});$('fixture-json').value=await file.text();fixtureId=null;feedback('文件已打开，尚未导入。');}));
$('fixture-json').addEventListener('input',()=>{fixtureId=null;$('import-result').textContent='输入已更改，尚未导入。';});
$('import-fixture').addEventListener('click',guarded(async()=>{const result=await client.importFixture(JSON.parse($('fixture-json').value));fixtureId=result.id;$('import-result').textContent=`已写盘：输入 ${result.raw_count} 条，去重后 ${result.unique_count} 条，移除 ${result.duplicates_removed} 条重复。`;feedback('Fixture 已导入本地数据库，可以提交任务。');}));
$('submit-task').addEventListener('click',guarded(async()=>{if(!fixtureId)throw Object.assign(Error('IMPORT_REQUIRED'),{code:'INVALID_INPUT'});const result=await client.submit({fixture_id:fixtureId,policy:JSON.parse($('policy-json').value),idempotency_key:$('idempotency-key').value,retry_policy:$('auto-retry').checked?{mode:'automatic_fixture',max_attempts:2,backoff_ms:500}:undefined});selectedId=result.id;feedback(result.deduplicated?'同键任务已存在，返回原任务。':'任务已受理，等待处理与保存结果。');await refresh();}));
$('create-schedule').addEventListener('click',guarded(async()=>{if(!fixtureId)throw Object.assign(Error('IMPORT_REQUIRED'),{code:'INVALID_INPUT'});const result=await api('/api/schedules/daily',{fixture_id:fixtureId,policy:JSON.parse($('policy-json').value),timezone:$('schedule-zone').value,local_time:$('schedule-time').value});feedback(`每日合成计划已写盘，下次 ${new Date(result.next_due*1000).toISOString()} · ${result.timezone}`);await refresh();}));
$('restart-service').addEventListener('click',guarded(async()=>{const result=await client.restartService();if(result.accepted)connection('starting','已受理本地服务重启，等待重新连接。');}));
function renderSchedules(schedules){const list=$('schedule-list');list.replaceChildren();if(!schedules.length)list.append(element('p','暂无计划','muted'));for(const schedule of schedules){const row=element('div',undefined,'schedule-card');row.append(element('strong',`${schedule.local_time} · ${schedule.timezone}`),element('small',`${schedule.state} · 下次 ${new Date(schedule.next_due*1000).toISOString()} · 错过 ${schedule.occurrences.filter(o=>o.state==='missed').length} 次`));if(schedule.state==='active'){const button=element('button','取消计划','secondary');button.addEventListener('click',guarded(async()=>{await api(`/api/schedules/daily/${schedule.id}/cancel`,{});await refresh();}));row.append(button);}list.append(row);}}
function watchSelected(taskId){
 if(!bridge)return;const selection=selectionGeneration,service=serviceGeneration;
 client.bind(taskId,update=>{if(selectedId!==taskId||selection!==selectionGeneration||service!==serviceGeneration)return;if(update.task)renderTask(update.task);else connection('failed','桌面任务监听连接中断，显示上次读取数据。');}).catch(error=>{if(selectedId===taskId&&error.code!=='TASK_SELECTION_CHANGED')feedback(describe(error));});
}
function renderTask(task){
 if(task.id!==selectedId)return;
 $('selected-state').dataset.taskId=task.id;
 $('selected-state').textContent=labels[task.state]||task.state;watchSelected(task.id);
 const controls=$('task-controls');controls.replaceChildren();
 const actions={queued:['pause','cancel'],running:['pause','cancel'],paused:['resume','cancel'],interrupted:['resume','cancel'],completed_with_errors:['retry']},names={pause:'暂停',resume:'显式恢复',cancel:'取消',retry:'仅重试失败项'};
 for(const action of actions[task.state]||[]){const button=element('button',names[action],action==='cancel'?'danger':'secondary');if(action==='retry'&&!task.items.some(item=>['failed','timed_out'].includes(item.state)&&item.attempt<task.max_attempts)){button.disabled=true;button.title='已用完本任务的重试次数';}button.addEventListener('click',guarded(async()=>{if(selectedId!==task.id)throw Object.assign(Error('SELECTION'),{code:'TASK_SELECTION_CHANGED'});await client.control(task.id,action);feedback(`任务${names[action]}已由本地核心确认。`);await refresh();}));controls.append(button);}
 for(const format of ['json','csv']){
  if(bridge){const button=element('button',`导出 ${format.toUpperCase()}`,'export');button.addEventListener('click',guarded(async()=>{if(selectedId!==task.id)throw Object.assign(Error('SELECTION'),{code:'TASK_SELECTION_CHANGED'});const result=await client.exportTask(task.id,format);feedback(`已导出 ${result.fileName} · 桌面管理的 exports 目录`);}));controls.append(button);}
  else{const link=element('a',`导出 ${format.toUpperCase()}`,'export');link.href=`/api/tasks/${task.id}/export.${format}`;link.download=`fixture-result.${format}`;controls.append(link);}
 }
 const count=task.counts,progress=$('task-progress');progress.replaceChildren(element('div',`处理 ${count.processed}/${count.total} · 保存 ${count.succeeded} · 筛除 ${count.filtered} · 失败/超时 ${count.failed+count.timed_out} · 等待重试 ${count.retry_wait||0} · 已发布 ${count.published}`,'progress-label'));
 const bar=element('div',undefined,'progress'),fill=element('div');fill.style.width=`${count.total?count.processed/count.total*100:0}%`;bar.append(fill);progress.append(bar);$('results').replaceChildren();
 for(const item of task.items){
  if(!item.result){const row=element('tr'),cell=element('td',`商品 #${item.item_no+1} · ${labels[item.state]||item.state}${item.error?' / '+item.error:''}`);cell.colSpan=5;row.append(cell);$('results').append(row);continue;}
  const product=item.result;for(const sku of product.skus){const row=element('tr'),name=element('td',product.title);name.append(element('small',`${product.platform} / ${product.external_id} / ${sku.sku}`));const decision=element('td',sku.decision,sku.decision),sale=element('td',sku.price?`${sku.price.sale.currency} ${sku.price.sale.amount}`:'—','money'),strike=element('td',sku.price?`${sku.price.strike.currency} ${sku.price.strike.amount}`:'—','money'),basis=element('td',sku.reasons.join(', '));if(sku.price){basis.append(element('small',`${sku.price.rule_version} · ${sku.price.strike_basis}`),element('small',`FX ${sku.price.fx.from}/${sku.price.fx.to} ${sku.price.fx.rate} · mock/test fixture`));}row.append(name,decision,sale,strike,basis);$('results').append(row);}
 }
 $('events').replaceChildren();for(const event of task.events){const row=element('li');row.append(element('time',new Date(event.at*1000).toLocaleTimeString()),document.createTextNode(`${event.code} ${JSON.stringify(event.details)}`));$('events').append(row);}
}
async function readSelected(id,selection,service){
 try{const task=await api('/api/tasks/'+id);if(selectedId===id&&selectionGeneration===selection&&serviceGeneration===service)renderTask(task);}
 catch(error){if(selectedId===id&&selectionGeneration===selection&&serviceGeneration===service)throw error;}
}
async function selectTask(id){selectedId=id;const selection=++selectionGeneration,service=serviceGeneration;void client.unbind();$('selected-state').textContent='读取任务中';$('selected-state').dataset.taskId='';$('task-controls').replaceChildren();await readSelected(id,selection,service);}
async function refresh(){
 if(polling)return;polling=true;const chosen=selectedId,selection=selectionGeneration,service=serviceGeneration;
 try{
  const [data,health,schedules]=await Promise.all([api('/api/tasks'),api('/api/health'),api('/api/schedules/daily')]);
  if(lastServiceInstance&&lastServiceInstance!==health.service_instance){serviceGeneration++;void client.unbind();}lastServiceInstance=health.service_instance;
  renderSchedules(schedules.schedules);$('task-count').textContent=data.summary.tasks;$('saved-count').textContent=data.summary.saved;$('filtered-count').textContent=data.summary.filtered;
  $('engine-status').textContent=`${health.health} · 并发 ${health.physical_workers}/${health.concurrency}`;connection(health.health==='ready'&&(!bridge||client.serviceReady)?'ready':'failed',health.health==='ready'&&bridge&&!client.serviceReady?'本地数据可读，桌面通信未就绪；任务控制暂不可用。':health.health==='ready'?`本地核心已连接 · ${bridge?'桌面 IPC + 本地 Web 数据接口':'Web 兼容模式'}`:'本地核心状态异常，操作结果请以持久记录为准。');
  const list=$('task-list');list.replaceChildren();if(!data.tasks.length)list.append(element('p','暂无任务，先导入 fixture。','muted'));
  for(const task of data.tasks){const button=element('button',undefined,'task-card'+(task.id===selectedId?' active':'')),name=element('div');name.append(element('strong',task.id.slice(0,12)),element('small',`TEST FIXTURE · ${new Date(task.created*1000).toLocaleString()} · 保存 ${task.counts.succeeded}/${task.counts.total}`));button.append(name,element('span',labels[task.state]||task.state,'state'));button.addEventListener('click',guarded(async()=>{await selectTask(task.id);}));list.append(button);}
  if(chosen&&chosen===selectedId&&selection===selectionGeneration&&service===serviceGeneration)await readSelected(chosen,selection,service);
 }catch(error){if(service===serviceGeneration){serviceGeneration++;void client.unbind();connection('failed','本地服务连接中断，当前显示上次读取的数据。');feedback(describe(error));}}finally{polling=false;}
}
(async()=>{
 if(bridge){
  $('restart-service').hidden=!bridge.restartService;
  if(bridge.onServiceState)serviceSubscription=bridge.onServiceState(state=>{serviceGeneration++;client.setServiceState(state.state);connection(state.state,state.state==='ready'?'本地服务已就绪，正在刷新窗口。':'本地服务未就绪，当前显示上次读取的数据。');});
  try{const runtime=await bridge.runtime();if(runtime.ok)client.setServiceState(runtime.data.serviceState?.state||(runtime.data.localServiceAvailable?'ready':'failed'));else feedback('桌面通信尚未就绪。');}catch{feedback('桌面通信不可用。');}
 }
 try{await demo();}catch(error){feedback(describe(error));}
 await refresh();setInterval(refresh,700);
})();
window.addEventListener('beforeunload',()=>{void client.unbind();if(serviceSubscription)bridge?.offServiceState?.(serviceSubscription);});
