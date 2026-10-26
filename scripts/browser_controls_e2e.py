"""Real Web compatibility UI/task controls/errors, with an owned Python process."""
import json,os,re,sqlite3,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from tests.support import offline_env
from tests.offline_guard import sitecustomize
from playwright.sync_api import sync_playwright
from process_tools import startup_line

def main():
 out=ROOT/os.environ.get('DSF_ARTIFACT_DIR','artifacts/stage3');out.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='dsf-web-controls-') as folder:
  db=str(Path(folder)/'core.sqlite3');process=None
  def start():
   p=subprocess.Popen([sys.executable,'-u','-X','utf8','-m','commerce_core','--db',db,'serve','--port','0'],cwd=ROOT,env=offline_env(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   line=startup_line(p);match=re.match(r'Fixture-only UI: (http://127\.0\.0\.1:\d+) \| DB:',line)
   if not match:p.terminate();p.wait(timeout=10);raise RuntimeError('OWNED_SERVICE_START_FAILED')
   return p,match.group(1)
  def stop(p):
   p.terminate();p.wait(timeout=10)
   assert p.returncode==0,'OWNED_SERVICE_STOP_FAILED'
   p.stdout.close();p.stderr.close()
  process,origin=start();first_pid=process.pid;origins={origin}
  try:
   with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('COMMERCE_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-default-apps'])
    context=browser.new_context(accept_downloads=True);denied=[]
    def route(request):
     if any(request.request.url.startswith(owned+'/') for owned in origins):request.continue_()
     else:denied.append(request.request.url);request.abort()
    context.route('**/*',route);page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(origin);page.wait_for_function("() => document.getElementById('fixture-json').value.includes('test_fixture')")
    def fixture(name,behavior):return {'schema_version':1,'source':'test_fixture','name':name,'products':[{'external_id':name,'platform':'OZON','title':'Fictional controls product','selection':{'price':'1000.00','currency':'RUB','sold_count':151},'skus':[{'sku':name,'stock':5,'cost':'100.00','currency':'CNY','original_price':'160.00'}],'test_behavior':behavior}]}
    def import_submit(raw,key):
     page.fill('#fixture-json',json.dumps(raw));page.click('#import-fixture');page.wait_for_function("() => document.getElementById('import-result').textContent.includes('已写盘')")
     page.fill('#idempotency-key',key);page.click('#submit-task')
    import_submit(fixture('controls-cancel',{'delay_ms':5000}),'controls-cancel')
    page.get_by_role('button',name='暂停',exact=True).click();page.wait_for_function("() => document.getElementById('selected-state').textContent==='已暂停'")
    page.get_by_role('button',name='显式恢复',exact=True).click();page.get_by_role('button',name='取消',exact=True).click();page.wait_for_function("() => document.getElementById('selected-state').textContent==='已取消'")
    page.wait_for_function("async () => (await (await fetch('/api/health')).json()).physical_workers===0",timeout=20000)
    import_submit(fixture('controls-retry',{'fail_attempts':1}),'controls-retry')
    page.wait_for_function("() => document.getElementById('selected-state').textContent==='完成 · 有失败'")
    page.get_by_role('button',name='仅重试失败项',exact=True).click();page.wait_for_function("() => document.getElementById('selected-state').textContent==='本地完成' && document.getElementById('saved-count').textContent==='1'")
    assert 'RUB 1080.00' in page.locator('#results').inner_text()
    with page.expect_download() as download:page.get_by_text('导出 JSON',exact=True).click()
    download.value.save_as(out/'controls-export.json');exported=json.loads((out/'controls-export.json').read_text());assert exported['task']['counts']['succeeded']==1 and exported['published']==0
    raw=fixture('invalid-input',{});raw['token']='fictional_sensitive_marker';page.fill('#fixture-json',json.dumps(raw));page.click('#import-fixture');page.wait_for_function("() => document.getElementById('feedback').textContent.includes('输入结构')")
    assert page.locator('#saved-count').inner_text()=='1'
    stop(process);page.wait_for_function("() => document.getElementById('connection-status').textContent.includes('上次读取')",timeout=10000)
    assert page.locator('#saved-count').inner_text()=='1'
    process,origin=start();origins.add(origin);page.goto(origin);page.wait_for_function("() => document.getElementById('saved-count').textContent==='1'")
    assert page.locator('#restart-service').is_hidden()
    data=page.evaluate("async () => await (await fetch('/api/tasks')).json()")
    canceled=next(t for t in data['tasks'] if t['state']=='canceled');assert canceled['counts']['succeeded']==0
    completed=next(t for t in data['tasks'] if t['state']=='completed');task=page.evaluate("async id => await (await fetch('/api/tasks/'+id)).json()",completed['id']);assert task['items'][0]['attempt']==2
    assert not errors,errors;assert not denied,denied
    # Hold a real A result, display B first, then deliver A late. No fabricated business data.
    pending=[];actual_a=page.request.get(origin+'/api/tasks/'+canceled['id']).json()
    def delayed_a(route):pending.append(route)
    page.route(origin+'/api/tasks/'+canceled['id'],delayed_a)
    page.locator('.task-card').filter(has_text=canceled['id'][:12]).click()
    for _ in range(80):
     if pending:break
     page.wait_for_timeout(25)
    assert pending,'A_READ_WAS_NOT_INTERCEPTED'
    page.locator('.task-card').filter(has_text=completed['id'][:12]).click()
    page.wait_for_function("id => document.getElementById('selected-state').dataset.taskId===id",arg=completed['id'])
    for route in pending:route.fulfill(status=200,content_type='application/json',body=json.dumps(actual_a))
    page.unroute(origin+'/api/tasks/'+canceled['id'],delayed_a)
    page.wait_for_timeout(200)
    assert page.locator('#selected-state').get_attribute('data-task-id')==completed['id']
    assert page.locator('#selected-state').inner_text()=='本地完成'
    source=sqlite3.connect(db);dest=sqlite3.connect(out/'controls-core.sqlite3')
    try:source.backup(dest)
    finally:source.close();dest.close()
    report={'status':'PASS','scope':'Real Chromium Web-compatible UI and Python/SQLite; NOT Electron/native IPC','first_pid':first_pid,'second_pid':process.pid,'same_database':db,'checks':['pause/resume/cancel','late canceled worker never saved','failed item retry to actual saved result','RUB1080.00 and JSON download','strict schema error without fake success','actual server outage retains and labels last data','new owned port+PID restart rereads data','late real A HTTP response cannot overwrite newly selected B'],'canceled_counts':canceled['counts'],'retry_attempt':task['items'][0]['attempt'],'saved':data['summary']['saved'],'published':0,'page_errors':errors,'external_page_requests':denied}
    (out/'browser-controls-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));browser.close()
  finally:
   if process and process.poll() is None:stop(process)
if __name__=='__main__':main()
