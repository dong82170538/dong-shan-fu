import copy
import json
import os
from pathlib import Path
import threading
import time
import tempfile

from commerce_core.domain import evaluate

ROOT = Path(__file__).resolve().parent.parent
POLICY = json.loads((ROOT / 'fixtures/pipeline-policy.json').read_text())


def product(ident='p1', **behavior):
    p = {'external_id': ident, 'platform': 'OZON', 'title': 'Fictional ' + ident,
         'selection': {'price': '1000.00', 'currency': 'RUB', 'sold_count': 151},
         'skus': [{'sku': ident, 'stock': 5, 'cost': '100.00', 'currency': 'CNY', 'original_price': '160.00'}]}
    if behavior:
        p['test_behavior'] = behavior
    return p


def fixture(*products):
    return {'schema_version': 1, 'source': 'test_fixture', 'name': 'Synthetic acceptance fixture', 'products': list(products or [product()])}


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    gate = threading.Event()
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        gate.wait(.005)
    raise AssertionError('Predicate did not settle before deadline')


def offline_env():
    return {**os.environ, 'PYTHONPATH': str(ROOT / 'tests/offline_guard') + os.pathsep + str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}


def temporary_directory(*args,**kwargs):
    root=Path(os.environ.get('DSF_TEST_TEMP_ROOT',ROOT/'artifacts'/'test-temp')).resolve()
    root.relative_to(ROOT.resolve())
    root.mkdir(parents=True,exist_ok=True)
    if 'dir' in kwargs:Path(kwargs['dir']).resolve().relative_to(root)
    kwargs.setdefault('dir',str(root))
    return tempfile.TemporaryDirectory(*args,**kwargs)


class ManualClock:
    def __init__(self):
        self.now = 0
        self.calls = 0
    def __call__(self):
        self.calls += 1
        return self.now
    def advance(self, seconds):
        self.now += seconds


class DeferredAdapter:
    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.calls = 0
    def run(self, raw, policy, attempt):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        self.started.set()
        try:
            if not self.release.wait(10):
                raise AssertionError('Test forgot to release deferred adapter')
            return evaluate(raw, policy)
        finally:
            with self.lock:
                self.active -= 1
