import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from .engine import Engine
from .server import make_server, csv_export, capability_manifest
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Local-only synthetic commerce core")
    parser.add_argument("--db", default="data/commerce.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--concurrency", type=int, default=2)
    serve.add_argument("--fixture-extension-id", action="append", default=[], help="Explicitly trust this self-built MV3 ID for fixture preview only")
    imp = sub.add_parser("import-fixture")
    imp.add_argument("file")
    submit = sub.add_parser("submit")
    submit.add_argument("fixture_id")
    submit.add_argument("--policy")
    submit.add_argument("--key", required=True)
    submit.add_argument("--timeout-ms", type=int, default=2000)
    sub.add_parser("tasks")
    task = sub.add_parser("task")
    task.add_argument("task_id")
    control = sub.add_parser("control")
    control.add_argument("task_id")
    control.add_argument("action", choices=["pause", "resume", "cancel", "retry"])
    export = sub.add_parser("export")
    export.add_argument("task_id")
    export.add_argument("--format", choices=["json", "csv"], default="json")
    export.add_argument("--output", required=True)
    work = sub.add_parser("work")
    work.add_argument("--concurrency", type=int, default=2)
    work.add_argument("--max-seconds", type=float, default=60)
    sub.add_parser("capabilities")
    args = parser.parse_args()
    store = Store(args.db)
    result = None
    if args.command == "import-fixture":
        result = store.import_fixture(json.loads(Path(args.file).read_text(encoding="utf-8")))
    elif args.command == "submit":
        policy = json.loads(Path(args.policy).read_text(encoding="utf-8")) if args.policy else None
        result = store.submit(args.fixture_id, policy, args.key, args.timeout_ms)
    elif args.command == "tasks":
        result = store.list_tasks()
    elif args.command == "task":
        result = store.task(args.task_id)
    elif args.command == "control":
        result = store.control(args.task_id, args.action)
    elif args.command == "capabilities":
        result = capability_manifest()
    elif args.command == "export":
        data = store.export(args.task_id)
        body = csv_export(data) if args.format == "csv" else json.dumps(data, ensure_ascii=False, indent=2).encode()
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        result = {"output": str(target.resolve()), "published": 0, "source": "test_fixture"}
    elif args.command == "work":
        engine = Engine(store, args.concurrency).start()
        try:
            deadline = time.monotonic() + args.max_seconds
            while time.monotonic() < deadline:
                if not store.has_runnable() and not engine.active:
                    result = {"process_id": os.getpid(), "database_path": store.path, "tasks": store.list_tasks(), "engine": engine.status()}
                    break
                time.sleep(0.01)
            if result is None:
                raise TimeoutError("Worker deadline reached; durable tasks preserved")
        finally:
            engine.stop()
    elif args.command == "serve":
        engine = Engine(store, args.concurrency).start()
        server = None
        try:
            server = make_server(store, engine, args.port, args.fixture_extension_id, service_instance=os.environ.get("DSF_SERVICE_INSTANCE"))
            def stop(_signal, _frame):
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            print(f"Fixture-only UI: http://127.0.0.1:{server.server_port} | DB: {store.path}", flush=True)
            server.serve_forever(poll_interval=0.1)
        finally:
            if server:
                server.server_close()
            engine.stop()
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Command failed; check schema/state/storage. No success claimed.", file=sys.stderr)
        sys.exit(1)
