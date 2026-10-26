"""Repeatable stdlib acceptance runner with real disk evidence and TS accounting."""
import datetime
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tests.offline_guard import sitecustomize
from snapshot_contract import code_manifest,collect_sources,manifest_sha
INPUT_BEFORE=code_manifest(collect_sources())

out = ROOT / os.environ.get('DSF_ARTIFACT_DIR','artifacts')
out.mkdir(parents=True,exist_ok=True)
os.environ['COMMERCE_EVIDENCE_DIR'] = str(out / 'test-evidence')

class RecordingResult(unittest.TextTestResult):
    records = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({'test':test.id(),'status':'PASS'})
    def addFailure(self, test, err):
        super().addFailure(test,err)
        self.records.append({'test':test.id(),'status':'FAIL','kind':'assertion'})
    def addError(self,test,err):
        super().addError(test,err)
        self.records.append({'test':test.id(),'status':'FAIL','kind':'error'})
    def addSkip(self,test,reason):
        super().addSkip(test,reason)
        self.records.append({'test':test.id(),'status':'NOT_RUN','reason':reason})

suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'),top_level_dir=str(ROOT))
with (out/'test-output.txt').open('w') as log:
    result = unittest.TextTestRunner(stream=log,verbosity=2,resultclass=RecordingResult).run(suite)
records = result.records
scenarios = json.loads((ROOT/'docs/acceptance-scenarios.json').read_text())['scenarios']
for scenario in scenarios:
    matching = [r for r in records if scenario.get('test') and scenario['test'] in r['test']]
    scenario['status'] = ('PASS' if all(r['status']=='PASS' for r in matching) else 'FAIL') if matching else ('BLOCKED' if scenario.get('blocker') else 'NOT_RUN')
    scenario['executed_tests'] = [r['test'] for r in matching]
INPUT_AFTER=code_manifest(collect_sources())
INPUT_UNCHANGED=INPUT_BEFORE==INPUT_AFTER
report = {
    'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'status':'PASS' if result.wasSuccessful() and INPUT_UNCHANGED else 'FAIL',
    'runtime_code_manifest_before':INPUT_BEFORE,'runtime_code_manifest_after':INPUT_AFTER,'runtime_code_unchanged':INPUT_UNCHANGED,'runtime_code_manifest_sha256':manifest_sha(INPUT_BEFORE),
    'scope':'first local fixture milestone, not full product reconstruction', 'test_count':result.testsRun,
    'failure_count':len(result.failures),'error_count':len(result.errors),'skip_count':len(result.skipped),
    'network':'Python audit guard covers connect/sendto/sendmsg and listed DNS events; not OS-level isolation. Browser requests have a separate loopback route policy.',
    'records':records,'scenarios':scenarios,
    'scenario_counts':{state:sum(s['status']==state for s in scenarios) for state in ('PASS','FAIL','BLOCKED','NOT_RUN')},
    'executed_test_status':'PASS' if result.wasSuccessful() else 'FAIL',
    'evidence_directory':str(out/'test-evidence'), 'log':str(out/'test-output.txt'),
    'unverified':['Windows packaging/runtime','native desktop IPC','packaged MV3 extension','real merchant integration','original ERP modules','original upstream source parity']}
(out/'test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'status':report['status'],'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'report':str(out/'test-results.json')},ensure_ascii=False))
if not result.wasSuccessful() or not INPUT_UNCHANGED:
    print((out/'test-output.txt').read_text())
    sys.exit(1)
