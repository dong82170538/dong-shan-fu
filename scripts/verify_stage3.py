"""Run real regressions and bind their reports to an unchanged code manifest."""
import datetime
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid

from snapshot_contract import ROOT, code_manifest, collect_sources, digest, manifest_sha
from owned_process import run_owned, OwnershipUnsupported


def main():
    out = ROOT / os.environ.get('DSF_ARTIFACT_DIR', 'artifacts/stage4')
    out.mkdir(parents=True, exist_ok=True)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    history = out/'run-history'/run_id
    before = code_manifest(collect_sources())
    env = dict(os.environ, DSF_ARTIFACT_DIR=str(out.relative_to(ROOT)), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    commands = [
        ([sys.executable, 'scripts/run_tests.py'], 180, 'test-results.json'),
        (['node', 'desktop/scripts/test.cjs'], 75, 'desktop-unit-result.json'),
        ([sys.executable, 'scripts/browser_e2e.py'], 100, 'browser-result.json'),
        ([sys.executable, 'scripts/browser_controls_e2e.py'], 100, 'browser-controls-result.json'),
    ]
    runs = []
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    for index, (argv, timeout, report_name) in enumerate(commands):
        report_file = out/report_name
        if report_file.exists():
            history.mkdir(parents=True, exist_ok=True)
            shutil.move(report_file, history/('previous-'+report_name))
        try:
            result = run_owned(argv, cwd=ROOT, env=env, timeout=timeout, stop_event=stop)
            record = {'argv': argv, 'exit_code': result['exit_code'], 'status': result['status'],
                      'reason': result['reason'], 'cleanup': result['cleanup']}
            ownership = {k: v for k, v in result.items() if k not in {'stdout', 'stderr'}}
            (out/f'verify-ownership-{index+1}.json').write_text(json.dumps(ownership, indent=2)+'\n', encoding='utf-8')
            output = result['stdout'] + result['stderr']
        except OwnershipUnsupported as error:
            record = {'argv': argv, 'status': 'BLOCKED', 'error': str(error), 'command_started': False}
            output = 'No command started: supported ownership cleanup is unavailable.\n'
        except Exception as error:
            record = {'argv': argv, 'status': 'FAIL', 'error': type(error).__name__, 'cleanup_unconfirmed': True}
            output = 'Owned-command supervision failed; no pass or confirmed cleanup claimed.\n'
        (out / f'verify-command-{index+1}.txt').write_text(output, encoding='utf-8')
        if record['status'] != 'PASS' or not report_file.is_file():
            report_file.write_text(json.dumps({'status': record['status'] if record['status'] != 'PASS' else 'FAIL', 'run_id': run_id, 'command': argv,
                                              'reason': record.get('error', 'command failed or no fresh report'),
                                              'log': f'verify-command-{index+1}.txt'}, indent=2), encoding='utf-8')
        runs.append(record)
        print(json.dumps(record), flush=True)
        if record['status'] != 'PASS':
            break
    after = code_manifest(collect_sources())
    unchanged = before == after
    reports = {}
    for name in ('test-results.json', 'desktop-unit-result.json', 'browser-result.json', 'browser-controls-result.json'):
        file = out / name
        if file.is_file():
            reports[name] = {'bytes': file.stat().st_size, 'sha256': digest(file.read_bytes()),
                             'status': json.loads(file.read_text(encoding='utf-8'))['status']}
    passed = unchanged and len(runs) == len(commands) and all(run['status'] == 'PASS' for run in runs) and len(reports) == 4 and all(r['status'] == 'PASS' for r in reports.values())
    report = {'status': 'PASS' if passed else 'BLOCKED' if any(run['status']=='BLOCKED' for run in runs) else 'FAIL', 'run_id': run_id, 'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'Linux owned descendant cleanup + core/Python lifecycle + Node IPC doubles + Chromium Web UI; native Electron NOT RUN',
              'code_unchanged_during_verification': unchanged, 'tested_code_manifest_sha256': manifest_sha(before),
              'tested_code_manifest': before, 'commands': runs, 'reports': reports,
              'native_ipc': 'BLOCKED: approved project Electron 44.9.3 runtime absent', 'windows': 'NOT_RUN',
              'mv3_host': 'BLOCKED: existing Chromium did not load service worker; official browser download previously403; no retry',
              'archive_not_executed': True}
    (out / 'verification-binding.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'tested_code_manifest_sha256', 'code_unchanged_during_verification')}))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
