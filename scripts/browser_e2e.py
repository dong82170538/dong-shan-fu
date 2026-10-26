"""Real Chromium UI flow, loopback only, using a fresh persistent database."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.support import offline_env
from tests.offline_guard import sitecustomize
from playwright.sync_api import sync_playwright
from process_tools import startup_line


def main():
    out = ROOT / os.environ.get('DSF_ARTIFACT_DIR','artifacts')
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='commerce-ui-') as folder:
        db = str(Path(folder) / 'ui.sqlite3')
        with socket.socket() as socket_probe:
            socket_probe.bind(('127.0.0.1', 0))
            port = socket_probe.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        def start():
            p = subprocess.Popen([sys.executable, '-m', 'commerce_core', '--db', db, 'serve', '--port', str(port)], cwd=ROOT, env=offline_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            line = startup_line(p)
            if not line.startswith('Fixture-only UI:'):
                p.terminate(); p.wait(timeout=10)
                raise RuntimeError('Server did not start: ' + p.stderr.read())
            return p
        def stop(p):
            p.terminate()
            p.wait(timeout=10)
            assert p.returncode == 0, p.stderr.read()
            p.stdout.close(); p.stderr.close()
        p = start()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.environ.get('COMMERCE_CHROMIUM','/usr/bin/chromium'), headless=True, args=['--no-sandbox', '--disable-background-networking', '--disable-component-update', '--disable-default-apps'])
                context = browser.new_context(accept_downloads=True, viewport={'width':1440,'height':1100}, service_workers='block')
                denied = []
                def network_guard(route):
                    if route.request.url.startswith(url + '/'):
                        route.continue_()
                    else:
                        denied.append(route.request.url)
                        route.abort()
                context.route('**/*', network_guard)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                page.wait_for_function("() => document.getElementById('fixture-json').value.includes('test_fixture')")
                page.click('#import-fixture')
                page.wait_for_function("() => document.getElementById('import-result').textContent.includes('去重后 2 条')")
                page.click('#submit-task')
                page.wait_for_function("() => document.getElementById('saved-count').textContent==='1' && document.getElementById('selected-state').textContent==='本地完成'")
                assert page.locator('#filtered-count').inner_text() == '1'
                assert '1080.00' in page.locator('#results').inner_text()
                assert '1920.00' in page.locator('#results').inner_text()
                task_count_before = page.locator('#task-count').inner_text()
                page.click('#submit-task')
                page.wait_for_function("() => document.getElementById('feedback').textContent.includes('返回原任务')")
                assert page.locator('#task-count').inner_text() == task_count_before == '1'
                with page.expect_download() as download:
                    page.get_by_text('导出 JSON', exact=True).click()
                exported_path = out / 'ui-export.json'
                download.value.save_as(exported_path)
                exported = json.loads(exported_path.read_text())
                assert exported['published'] == 0
                assert exported['task']['counts']['succeeded'] == 1
                task_id = exported['task']['id']
                page.click('#create-schedule')
                page.wait_for_function("() => document.getElementById('schedule-list').textContent.includes('Asia/Shanghai') && document.getElementById('schedule-list').textContent.includes('active')")
                page.get_by_text('取消计划',exact=True).click()
                page.wait_for_function("() => document.getElementById('schedule-list').textContent.includes('canceled')")
                page.screenshot(path=str(out / 'ui-desktop.png'), full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(out / 'ui-mobile.png'), full_page=True)
                assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')
                first_pid = p.pid
                stop(p)
                p = start()
                page.goto(url)
                page.wait_for_function("() => document.getElementById('saved-count').textContent==='1'")
                page.locator('.task-card').click()
                page.wait_for_function("() => document.getElementById('results').textContent.includes('1080.00')")
                assert 'canceled' in page.locator('#schedule-list').inner_text()
                assert p.pid != first_pid
                assert not errors, errors
                assert not denied, denied
                report = {'status':'PASS','source':'test_fixture','first_server_pid':first_pid,'second_server_pid':p.pid,
                          'same_database_path':db, 'database_evidence':str(out/'ui-core.sqlite3'),
                          'task_id':task_id,'persisted_products':1,'sale':{'amount':'1080.00','currency':'RUB'},
                          'strike':{'amount':'1920.00','currency':'RUB'},'published':0,'duplicate_task_count':1,
                          'mobile_width':390,'page_errors':errors,'external_page_requests':denied,
                          'checks':['actual button import/submit','idempotent resubmit','JSON download','daily schedule create/cancel and persisted readback','desktop/mobile render','real server restart and readback'],
                          'network_policy':'Python test audit hook covers external connect/sendto/sendmsg/listed DNS, not an OS firewall; Chromium page requests limited to loopback, background networking disabled'}
                (out / 'browser-result.json').write_text(json.dumps(report,indent=2))
                source = sqlite3.connect(db)
                dest = sqlite3.connect(out / 'ui-core.sqlite3')
                try: source.backup(dest)
                finally: source.close(); dest.close()
                print(json.dumps(report,ensure_ascii=False))
                browser.close()
        finally:
            if p.poll() is None: stop(p)


if __name__ == '__main__':
    main()
