"""Create/replay the explicit synthetic demo in a real local database."""
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from commerce_core.store import Store
from commerce_core.engine import Engine

store=Store(ROOT/'data/commerce.sqlite3')
f=store.import_fixture(json.loads((ROOT/'fixtures/pipeline.json').read_text()))
p=json.loads((ROOT/'fixtures/pipeline-policy.json').read_text())
t=store.submit(f['id'],p,'demo:pipeline:v1')
e=Engine(store).start()
try:
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=store.task(t['id'])
        if result['state'] in {'completed','completed_with_errors','canceled','interrupted'}:
            break
        e.stop_event.wait(.01)
    else: raise TimeoutError('Demo did not settle')
    print(json.dumps({'database':store.path,'fixture':f,'task':result,'published':0},ensure_ascii=False,indent=2))
finally:
    e.stop()
