"""Real timeout/abnormal-exit regression; no network and no name-based killing."""
from tests.support import temporary_directory
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'scripts'))
from owned_process import run_owned, OwnershipUnsupported


@unittest.skipUnless(sys.platform == 'linux', 'Linux subreaper/pidfd regression; Windows Job regressions are separate')
class ProcessSupervisionTests(unittest.TestCase):
    def test_unsupported_platform_does_not_start_a_command(self):
        from unittest.mock import patch
        with patch('owned_process.subprocess.Popen') as launch, patch('owned_process.sys.platform', 'unsupported-test-platform'):
            with self.assertRaises(OwnershipUnsupported):
                run_owned([sys.executable, '-c', 'print("must not start")'], cwd=ROOT, timeout=1)
            launch.assert_not_called()
    def test_preexisting_child_is_rejected_without_signaling_it(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
        try:
            with self.assertRaisesRegex(RuntimeError, 'SUPERVISOR_HAS_PREEXISTING_CHILDREN'):
                run_owned([sys.executable, '-c', 'raise RuntimeError("must not start")'], cwd=ROOT, timeout=1)
            self.assertIsNone(child.poll())
        finally:
            child.terminate(); child.wait(timeout=2)
    def evidence(self, name, value):
        out = Path(os.environ.get('COMMERCE_EVIDENCE_DIR', ROOT/'artifacts/stage4/test-evidence'))
        out.mkdir(parents=True, exist_ok=True)
        (out/(name+'.json')).write_text(json.dumps(value, indent=2)+'\n')

    def tree_command(self, folder, nonce, mode='wait'):
        return [sys.executable, str(ROOT/'tests/process_fixtures/heartbeat_tree.py'), '--folder', str(folder), '--nonce', nonce, '--mode', mode]

    def assert_stopped_unlocked(self, folder, nonce):
        import fcntl
        records = [json.loads((folder/(role+'.json')).read_text()) for role in ['root', 'child', 'leaf']]
        for row in records:
            self.assertEqual(row['nonce'], nonce)
            self.assertFalse(Path(f'/proc/{row["pid"]}').exists(), row)
            with (folder/(row['role']+'.lock')).open('r+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = {role: (folder/(role+'.heartbeat')).stat().st_size for role in ['root', 'child', 'leaf']}
        time.sleep(0.08)
        self.assertEqual(before, {role: (folder/(role+'.heartbeat')).stat().st_size for role in before})
        return records

    def check_reusable(self):
        result = run_owned([sys.executable, '-c', 'print("normal_after_cleanup")'], cwd=ROOT, timeout=2)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['stdout'].strip(), 'normal_after_cleanup')

    def test_outer_timeout_reaps_heartbeat_grandchildren_and_releases_locks(self):
        with temporary_directory(prefix='dsf-owned-outer-') as temp:
            folder = Path(temp); nonce = uuid.uuid4().hex
            result = run_owned(self.tree_command(folder, nonce), cwd=ROOT, timeout=0.8)
            self.assertEqual(result['reason'], 'timeout'); self.assertEqual(result['status'], 'FAIL')
            self.assertTrue(result['cleanup']['confirmed'])
            records = self.assert_stopped_unlocked(folder, nonce)
            self.check_reusable(); self.evidence('outer-timeout', {'processes': records, 'directory': temp, 'supervision': result})

    def test_abnormal_runner_exit_cleans_adopted_descendants(self):
        with temporary_directory(prefix='dsf-owned-exit-') as temp:
            folder = Path(temp); nonce = uuid.uuid4().hex
            result = run_owned(self.tree_command(folder, nonce, 'abnormal'), cwd=ROOT, timeout=2)
            self.assertEqual(result['reason'], 'runner_exit_nonzero'); self.assertEqual(result['exit_code'], 23)
            self.assertTrue(result['cleanup']['confirmed']); records = self.assert_stopped_unlocked(folder, nonce)
            self.check_reusable(); self.evidence('runner-abnormal-exit', {'processes': records, 'directory': temp, 'supervision': result})

    def test_zero_runner_exit_with_orphans_is_not_pass(self):
        with temporary_directory(prefix='dsf-owned-zero-') as temp:
            folder = Path(temp); nonce = uuid.uuid4().hex
            result = run_owned(self.tree_command(folder, nonce, 'zero_orphan'), cwd=ROOT, timeout=2)
            self.assertEqual(result['reason'], 'runner_exit_zero'); self.assertEqual(result['status'], 'FAIL')
            self.assertTrue(result['leftovers_at_runner_exit']); self.assert_stopped_unlocked(folder, nonce); self.check_reusable()

    def node_command(self, folder, timeout_ms):
        code = "const {runTests}=require('./desktop/scripts/test.cjs');const r=runTests({files:[require('node:path').resolve('tests/process_fixtures/hanging-node.cjs')],timeoutMs:"+str(timeout_ms)+",out:"+json.dumps(str(folder/'node-reports'))+"});process.exitCode=r.status==='PASS'?0:1;"
        return ['node', '-e', code]

    def test_inner_node_timeout_uses_same_cleanup_and_outer_does_not_mask_it(self):
        with temporary_directory(prefix='dsf-owned-inner-') as temp:
            folder=Path(temp); nonce=uuid.uuid4().hex
            env=dict(os.environ, DSF_PYTHON=sys.executable, DSF_HEARTBEAT_DIR=temp, DSF_HEARTBEAT_NONCE=nonce)
            result=run_owned(self.node_command(folder,1000),cwd=ROOT,env=env,timeout=6)
            inner=json.loads((folder/'node-reports/desktop-process-ownership.json').read_text())
            self.assertEqual(result['reason'],'runner_exit_nonzero'); self.assertTrue(result['cleanup']['confirmed'])
            self.assertEqual(inner['reason'],'timeout'); self.assertTrue(inner['cleanup']['confirmed'])
            records=self.assert_stopped_unlocked(folder,nonce);self.check_reusable()
            self.evidence('inner-node-timeout',{'processes':records,'directory':temp,'inner':inner,'outer':result})

    def test_outer_timeout_during_inner_supervision_cleans_whole_owned_tree(self):
        with temporary_directory(prefix='dsf-owned-nested-') as temp:
            folder=Path(temp);nonce=uuid.uuid4().hex
            env=dict(os.environ,DSF_PYTHON=sys.executable,DSF_HEARTBEAT_DIR=temp,DSF_HEARTBEAT_NONCE=nonce)
            result=run_owned(self.node_command(folder,10000),cwd=ROOT,env=env,timeout=1)
            self.assertEqual(result['reason'],'timeout');self.assertTrue(result['cleanup']['confirmed'])
            records=self.assert_stopped_unlocked(folder,nonce);self.check_reusable()
            self.evidence('outer-during-inner',{'processes':records,'directory':temp,'outer':result})

    def test_node_wrapper_death_requests_inner_supervisor_cleanup(self):
        with temporary_directory(prefix='dsf-owned-parent-death-') as temp:
            folder=Path(temp);nonce=uuid.uuid4().hex
            env=dict(os.environ,DSF_PYTHON=sys.executable,DSF_HEARTBEAT_DIR=temp,DSF_HEARTBEAT_NONCE=nonce)
            # Only this explicitly created child is killed. The surrounding
            # supervisor remains alive to collect evidence/reap adopted exits.
            script='''import subprocess,time,pathlib,sys
child=subprocess.Popen(COMMAND)
deadline=time.monotonic()+4
while not pathlib.Path(READY).exists():
 if time.monotonic()>deadline:raise TimeoutError('tree not ready')
 time.sleep(.02)
child.kill();child.wait(timeout=2)
while not pathlib.Path(REPORT).exists():
 if time.monotonic()>deadline+4:raise TimeoutError('supervisor cleanup not reported')
 time.sleep(.02)
'''.replace('COMMAND',repr(self.node_command(folder,10000))).replace('READY',repr(str(folder/'ready'))).replace('REPORT',repr(str(folder/'node-reports/desktop-process-ownership.json')))
            result=run_owned([sys.executable,'-c',script],cwd=ROOT,env=env,timeout=10)
            inner=json.loads((folder/'node-reports/desktop-process-ownership.json').read_text())
            self.assertEqual(inner['reason'],'supervisor_signal');self.assertTrue(inner['cleanup']['confirmed'])
            self.assertTrue(result['cleanup']['confirmed']);records=self.assert_stopped_unlocked(folder,nonce);self.check_reusable()
            self.evidence('node-wrapper-death',{'processes':records,'directory':temp,'inner':inner,'outer':result})
