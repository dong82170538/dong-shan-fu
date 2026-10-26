"""Load only our self-written MV3 extension in real Chromium, offline/loopback."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from tests.offline_guard import sitecustomize
from commerce_core.store import Store
from commerce_core.engine import Engine
from commerce_core.server import make_server
from playwright.sync_api import sync_playwright


def main():
    out=ROOT/'artifacts';out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='commerce-mv3-') as folder:
        store=Store(Path(folder)/'core.sqlite3');engine=Engine(store).start()
        server=None;thread=None;context=None
        try:
            with sync_playwright() as pw:
                extension=ROOT/'extension'
                def launch():
                    return pw.chromium.launch_persistent_context(str(Path(folder)/'profile'),executable_path=os.environ.get('COMMERCE_CHROMIUM','/usr/bin/chromium'),headless=True,ignore_default_args=['--disable-extensions'],args=['--no-sandbox','--disable-background-networking','--disable-component-update','--disable-default-apps',f'--disable-extensions-except={extension}',f'--load-extension={extension}'])
                context=launch()
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker',timeout=20000)
                extension_id=urlsplit(worker.url).netloc
                assert len(extension_id)==32
                server=make_server(store,engine,0,[extension_id])
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                backend=f'http://127.0.0.1:{server.server_port}'
                page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'chrome-extension://{extension_id}/popup.html')
                page.fill('#backend',backend);page.click('#set-backend')
                page.wait_for_function("() => document.getElementById('result').textContent.includes('real_connections_enabled')")
                assert json.loads(page.locator('#result').inner_text())['ok']
                base={'source':'test_fixture','candidate_origin':'https://fixture.invalid','publish':False,'selected_skus':[{'goods_id':'s1','offer_id':'fixture-1','price':'100.00','old_price':'160.00','min_price':'50.00','currency':'RUB'}]}
                def preview(body):
                    page.fill('#fixture-input',json.dumps(body));page.click('#preview')
                    page.wait_for_function("() => !document.getElementById('preview').disabled && document.getElementById('result').textContent.includes('publish_calls')")
                    result=json.loads(page.locator('#result').inner_text())
                    assert result['ok'],result
                    return result['result']
                valid=preview(base)
                assert valid['state']=='dry_run_preview' and valid['publish_calls']==0
                duplicate={**base,'selected_skus':[base['selected_skus'][0],{**base['selected_skus'][0],'goods_id':'s2'}]}
                duplicate_result=preview(duplicate)
                assert 'duplicate_offer_id' in [e['code'] for e in duplicate_result['errors']]
                minimum={**base,'selected_skus':[{**base['selected_skus'][0],'min_price':'49.99'}]}
                minimum_result=preview(minimum)
                assert 'below_min_price' in [e['code'] for e in minimum_result['errors']]
                denied=page.evaluate("async () => await chrome.runtime.sendMessage({type:'publish',token:'fictional_sensitive_marker'})")
                assert denied=={'ok':False,'error':'UNSUPPORTED_MESSAGE'}
                invalid_backend=page.evaluate("async () => await chrome.runtime.sendMessage({type:'set_fixture_backend',url:'https://fixture.invalid'})")
                assert not invalid_backend['ok']
                page.screenshot(path=str(out/'extension-popup.png'),full_page=True)
                first_worker=worker.url
                context.close();context=None
                context=launch()
                worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker',timeout=20000)
                assert urlsplit(worker.url).netloc==extension_id
                page=context.new_page();page.goto(f'chrome-extension://{extension_id}/popup.html')
                page.click('#preview')
                page.wait_for_function("() => !document.getElementById('preview').disabled && document.getElementById('result').textContent.includes('dry_run_preview')")
                restarted=json.loads(page.locator('#result').inner_text())
                assert restarted['ok'] and restarted['publish_calls']==0
                assert store.summary()['saved']==0
                assert not errors,errors
                report={'status':'PASS','source':'test_fixture','actual_mv3_host':True,'chromium_version':context.browser.version,'manifest_version':3,
                        'extension_id':extension_id,'first_service_worker':first_worker,'second_service_worker':worker.url,
                        'contexts_opened':2,'local_backend':backend,'valid':valid,'duplicate':duplicate_result,'minimum':minimum_result,
                        'unsupported_channel':denied,'remote_backend_rejected':not invalid_backend['ok'],'page_errors':errors,'published':0,
                        'scope':'Actual self-written MV3 popup -> runtime message -> service worker -> loopback Python core. No original extension or merchant integration.'}
                (out/'extension-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print(json.dumps(report,ensure_ascii=False))
        finally:
            if context:
                try:context.close()
                except Exception:pass
            if server:server.shutdown();server.server_close()
            if thread:thread.join()
            engine.stop()


if __name__=='__main__':
    try:main()
    except Exception as error:
        report={'status':'BLOCKED' if 'serviceworker' in str(error) else 'FAIL','source':'test_fixture','actual_mv3_host':False,'published':0,'error_type':type(error).__name__,'reason':'MV3 host did not complete. Check permitted browser/runtime and extension diagnostics; no integration success claimed.'}
        (ROOT/'artifacts').mkdir(exist_ok=True)
        (ROOT/'artifacts/extension-result.json').write_text(json.dumps(report,indent=2))
        raise
