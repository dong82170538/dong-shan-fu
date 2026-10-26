"""SQLite transactions are the source of truth. No in-memory success counts."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .domain import ValidationError, canonical, validate_fixture, validate_policy, integer

TERMINAL = {"completed", "completed_with_errors", "canceled"}
ITEM_TERMINAL = {"succeeded", "filtered", "failed", "timed_out", "canceled"}


class ConflictError(ValueError):
    pass


class AuthRequired(ValueError):
    pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('schema_version', '1');
CREATE TABLE IF NOT EXISTS fixtures (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, raw_count INTEGER NOT NULL,
 unique_count INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
 fixture_id TEXT NOT NULL REFERENCES fixtures(id), policy TEXT NOT NULL,
 state TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
 timeout_ms INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
 task_id TEXT NOT NULL REFERENCES tasks(id), item_no INTEGER NOT NULL, raw TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
 generation INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT,
 PRIMARY KEY(task_id,item_no)
);
CREATE TABLE IF NOT EXISTS products (
 task_id TEXT NOT NULL, item_no INTEGER NOT NULL, product_id TEXT NOT NULL,
 platform TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL CHECK(source='test_fixture'),
 PRIMARY KEY(task_id,item_no), FOREIGN KEY(task_id,item_no) REFERENCES items(task_id,item_no)
);
CREATE TABLE IF NOT EXISTS skus (
 task_id TEXT NOT NULL, item_no INTEGER NOT NULL, sku TEXT NOT NULL,
 decision TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(task_id,item_no,sku), FOREIGN KEY(task_id,item_no) REFERENCES items(task_id,item_no)
);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(id), item_no INTEGER,
 at REAL NOT NULL, code TEXT NOT NULL, details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
 id TEXT PRIMARY KEY, due REAL NOT NULL, fixture_id TEXT NOT NULL REFERENCES fixtures(id),
 policy TEXT NOT NULL, state TEXT NOT NULL, task_id TEXT REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS money_records (
 id TEXT PRIMARY KEY, context TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source='test_fixture'), created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 account_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL, state TEXT NOT NULL,
 updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_schedules (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL, timezone TEXT NOT NULL, local_time TEXT NOT NULL,
 next_due REAL NOT NULL, fixture_id TEXT NOT NULL REFERENCES fixtures(id), policy TEXT NOT NULL,
 state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS occurrences (
 schedule_id TEXT NOT NULL REFERENCES daily_schedules(id), due REAL NOT NULL,
 state TEXT NOT NULL, task_id TEXT REFERENCES tasks(id), recorded REAL NOT NULL,
 PRIMARY KEY(schedule_id,due)
);
CREATE INDEX IF NOT EXISTS item_queue ON items(state,task_id,item_no);
CREATE INDEX IF NOT EXISTS event_task ON events(task_id,seq);
"""


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, path, clock=time.time):
        self.path = str(Path(path).resolve())
        self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            try:
                version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                if version not in {"1", "2"}:
                    raise RuntimeError("Unsupported database schema")
                additions = {
                    "tasks": {"account_id":"TEXT NOT NULL DEFAULT 'fixture-default'", "kind":"TEXT NOT NULL DEFAULT 'selection'", "retry_mode":"TEXT NOT NULL DEFAULT 'manual'", "max_attempts":"INTEGER NOT NULL DEFAULT 3", "backoff_ms":"INTEGER NOT NULL DEFAULT 500"},
                    "items": {"next_run":"REAL NOT NULL DEFAULT 0"},
                    "schedules": {"account_id":"TEXT NOT NULL DEFAULT 'fixture-default'"},
                }
                for table, columns in additions.items():
                    existing = {r["name"] for r in db.execute("PRAGMA table_info(" + table + ")")}
                    for name, ddl in columns.items():
                        if name not in existing:
                            db.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + ddl)
                if version == "1":
                    db.execute("UPDATE tasks SET dedupe_key='manual/fixture-default/' || substr(dedupe_key,8) WHERE dedupe_key LIKE 'manual/%' AND dedupe_key NOT LIKE 'manual/fixture-default/%'")
                    for task in db.execute("SELECT * FROM tasks").fetchall():
                        retry = {"mode":task["retry_mode"], "max_attempts":task["max_attempts"], "backoff_ms":task["backoff_ms"]}
                        digest = hashlib.sha256(canonical([task["fixture_id"], json.loads(task["policy"]), task["timeout_ms"], task["account_id"], task["kind"], retry]).encode()).hexdigest()
                        db.execute("UPDATE tasks SET request_hash=? WHERE id=?", (digest,task["id"]))
                db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, task_id, code, item_no=None, **details):
        # Event messages are fixed codes, not raw input, exceptions or credentials.
        db.execute("INSERT INTO events(task_id,item_no,at,code,details) VALUES (?,?,?,?,?)",
                   (task_id, item_no, self.clock(), code, canonical(details)))

    def import_fixture(self, fixture):
        validate_fixture(fixture)
        unique = {}
        for product in fixture["products"]:
            unique.setdefault((product["platform"], product["external_id"].strip()), product)
        payload = {**fixture, "products": list(unique.values())}
        fixture_id = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with self.transaction() as db:
            db.execute("INSERT OR IGNORE INTO fixtures VALUES (?,?,?,?,?,?)", (
                fixture_id, fixture["name"], canonical(payload), len(fixture["products"]), len(unique), self.clock()))
        return {"id": fixture_id, "source": "test_fixture", "raw_count": len(fixture["products"]),
                "unique_count": len(unique), "duplicates_removed": len(fixture["products"]) - len(unique)}

    def submit(self, fixture_id, policy=None, dedupe_key=None, timeout_ms=2000, account_id="fixture-default", kind="selection", retry_policy=None):
        policy = validate_policy(policy)
        integer(timeout_ms, "timeout_ms", 20, 600000)
        self.validate_account(account_id)
        if kind not in {"selection", "inspection_fixture"}:
            raise ValidationError("unknown fixture task kind")
        retry = self.validate_retry(retry_policy)
        import re
        if not isinstance(dedupe_key, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", dedupe_key):
            raise ValidationError("idempotency_key: use 1..80 letters, numbers, dot, colon, underscore or hyphen")
        digest = hashlib.sha256(canonical([fixture_id, policy, timeout_ms, account_id, kind, retry]).encode()).hexdigest()
        storage_key = "manual/" + account_id + "/" + dedupe_key
        with self.transaction() as db:
            self.check_session(db, account_id)
            existing = db.execute("SELECT id,request_hash FROM tasks WHERE dedupe_key=?", (storage_key,)).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise ConflictError("idempotency key already used for different parameters")
                return {"id": existing["id"], "deduplicated": True, "accepted": True}
            return self._submit(db, fixture_id, policy, storage_key, digest, timeout_ms, account_id, kind, retry)

    @staticmethod
    def validate_account(account_id):
        import re
        if not isinstance(account_id, str) or not re.fullmatch(r"fixture-[A-Za-z0-9_-]{1,60}", account_id):
            raise ValidationError("Only explicitly fictional fixture accounts are supported")

    @staticmethod
    def validate_retry(raw=None):
        from .domain import only_keys
        raw = {} if raw is None else raw
        only_keys(raw, {"mode", "max_attempts", "backoff_ms"}, "retry_policy")
        retry = {"mode":"manual", "max_attempts":3, "backoff_ms":500, **raw}
        if retry["mode"] not in {"manual", "automatic_fixture"}:
            raise ValidationError("unknown retry mode")
        integer(retry["max_attempts"], "max_attempts", 1, 3)
        integer(retry["backoff_ms"], "backoff_ms", 1, 10000)
        return retry

    @staticmethod
    def check_session(db, account_id, restricted=False):
        row = db.execute("SELECT state FROM sessions WHERE account_id=?", (account_id,)).fetchone()
        if (row and row[0] != "authenticated_fixture") or (restricted and not row):
            raise AuthRequired("AUTH_REQUIRED")

    def _submit(self, db, fixture_id, policy, key, digest, timeout_ms, account_id="fixture-default", kind="selection", retry=None):
        self.check_session(db, account_id)
        retry = retry or self.validate_retry()
        fixture = db.execute("SELECT payload FROM fixtures WHERE id=?", (fixture_id,)).fetchone()
        if not fixture:
            raise ValidationError("fixture does not exist")
        task_id = uuid.uuid4().hex
        now = self.clock()
        db.execute("INSERT INTO tasks(id,dedupe_key,request_hash,fixture_id,policy,state,generation,timeout_ms,created,updated) VALUES (?,?,?,?,?,'queued',0,?,?,?)",
                   (task_id, key, digest, fixture_id, canonical(policy), timeout_ms, now, now))
        db.execute("UPDATE tasks SET account_id=?,kind=?,retry_mode=?,max_attempts=?,backoff_ms=? WHERE id=?", (account_id, kind, retry["mode"], retry["max_attempts"], retry["backoff_ms"], task_id))
        for index, product in enumerate(json.loads(fixture[0])["products"]):
            db.execute("INSERT INTO items(task_id,item_no,raw) VALUES (?,?,?)", (task_id, index, canonical(product)))
        self.event(db, task_id, "task_accepted", source="test_fixture", published=0)
        return {"id": task_id, "deduplicated": False, "accepted": True}

    def recover(self):
        """Call only after acquiring the single-engine lock for this database."""
        with self.transaction() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks WHERE state='running'")]
            for task_id in ids:
                db.execute("UPDATE tasks SET state='interrupted',generation=generation+1,updated=? WHERE id=?", (self.clock(), task_id))
                self._interrupt_items(db,task_id)
                self.event(db, task_id, "restart_requires_resume")
            # Past schedules must not silently run after a restart.
            for row in db.execute("SELECT id FROM schedules WHERE state='pending' AND due<=?", (self.clock(),)).fetchall():
                db.execute("UPDATE schedules SET state='missed' WHERE id=?", (row[0],))
                self.event(db, None, "schedule_missed_skipped", schedule_id=row[0])
            from .scheduling import DailyScheduler
            DailyScheduler(self).recover(db)

    def claim(self):
        with self.transaction() as db:
            exhausted=db.execute("""SELECT i.task_id,i.item_no FROM items i JOIN tasks t ON t.id=i.task_id
                WHERE i.state IN ('pending','retry_wait') AND t.state IN ('queued','running') AND i.attempt>=t.max_attempts""").fetchall()
            for item in exhausted:
                db.execute("UPDATE items SET state='failed',error='attempt_budget_exhausted',generation=generation+1 WHERE task_id=? AND item_no=?",(item["task_id"],item["item_no"]))
                self.event(db,item["task_id"],"attempt_budget_exhausted",item["item_no"])
                self._finalize(db,item["task_id"])
            row = db.execute("""SELECT i.*,t.policy,t.timeout_ms FROM items i JOIN tasks t ON t.id=i.task_id
                WHERE i.state IN ('pending','retry_wait') AND i.next_run<=? AND i.attempt<t.max_attempts AND t.state IN ('queued','running')
                AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.account_id=t.account_id AND s.state!='authenticated_fixture')
                ORDER BY t.created,t.rowid,i.item_no LIMIT 1""", (self.clock(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE tasks SET state='running',updated=? WHERE id=?", (self.clock(), row["task_id"]))
            db.execute("UPDATE items SET state='running',attempt=attempt+1,generation=generation+1,error=NULL WHERE task_id=? AND item_no=?", (row["task_id"], row["item_no"]))
            claim = dict(row)
            claim["attempt"] += 1
            claim["generation"] += 1
            claim["raw"] = json.loads(claim["raw"])
            claim["policy"] = json.loads(claim["policy"])
            self.event(db, claim["task_id"], "item_started", claim["item_no"], attempt=claim["attempt"])
            return claim

    @staticmethod
    def _current(db, claim):
        return db.execute("""SELECT 1 FROM items i JOIN tasks t ON i.task_id=t.id
            WHERE i.task_id=? AND i.item_no=? AND i.generation=? AND i.state='running' AND t.state='running'
            AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.account_id=t.account_id AND s.state!='authenticated_fixture')""",
                          (claim["task_id"], claim["item_no"], claim["generation"])).fetchone() is not None

    def complete(self, claim, product, inject_save_failure=False):
        with self.transaction() as db:
            if not self._current(db, claim):
                self.event(db, claim["task_id"], "stale_result_ignored", claim["item_no"])
                return False
            task_id, index = claim["task_id"], claim["item_no"]
            if product["accepted_skus"]:
                db.execute("INSERT INTO products VALUES (?,?,?,?,?,?)", (task_id, index, product["id"], product["platform"], product["title"], "test_fixture"))
                for sku in product["skus"]:
                    db.execute("INSERT INTO skus VALUES (?,?,?,?,?)", (task_id, index, sku["sku"], sku["decision"], canonical(sku)))
            if inject_save_failure:
                raise sqlite3.OperationalError("injected transaction failure")
            state = "succeeded" if product["accepted_skus"] else "filtered"
            db.execute("UPDATE items SET state=?,result=? WHERE task_id=? AND item_no=?", (state, canonical(product), task_id, index))
            self.event(db, task_id, "item_committed", index, outcome=state, accepted_skus=product["accepted_skus"], published=0)
            self._finalize(db, task_id)
            return True

    def record_late_result(self, claim):
        with self.transaction() as db:
            self.event(db, claim["task_id"], "stale_result_ignored", claim["item_no"])

    def fail(self, claim, code):
        if code not in {"adapter_failed", "save_failed", "timed_out", "calculation_failed"}:
            raise ValueError("unknown safe failure code")
        with self.transaction() as db:
            if not self._current(db, claim):
                return False
            state = "timed_out" if code == "timed_out" else "failed"
            task = db.execute("SELECT retry_mode,max_attempts,backoff_ms FROM tasks WHERE id=?", (claim["task_id"],)).fetchone()
            next_run = 0
            if task["retry_mode"] == "automatic_fixture" and claim["attempt"] < task["max_attempts"]:
                state = "retry_wait"
                next_run = self.clock() + task["backoff_ms"] / 1000
                self.event(db, claim["task_id"], "retry_scheduled", claim["item_no"], next_attempt=claim["attempt"]+1, backoff_ms=task["backoff_ms"])
            db.execute("UPDATE items SET state=?,error=?,generation=generation+1,next_run=? WHERE task_id=? AND item_no=?", (state, code, next_run, claim["task_id"], claim["item_no"]))
            self.event(db, claim["task_id"], code, claim["item_no"])
            self._finalize(db, claim["task_id"])
            return True

    def _finalize(self, db, task_id):
        states = [r[0] for r in db.execute("SELECT state FROM items WHERE task_id=?", (task_id,))]
        if all(s in ITEM_TERMINAL for s in states):
            state = "completed_with_errors" if any(s in {"failed", "timed_out"} for s in states) else "completed"
            db.execute("UPDATE tasks SET state=?,updated=? WHERE id=? AND state IN ('queued','running')", (state, self.clock(), task_id))

    def _interrupt_items(self,db,task_id):
        maximum=db.execute("SELECT max_attempts FROM tasks WHERE id=?",(task_id,)).fetchone()[0]
        exhausted=db.execute("SELECT item_no FROM items WHERE task_id=? AND state='running' AND attempt>=?",(task_id,maximum)).fetchall()
        db.execute("UPDATE items SET state=CASE WHEN attempt>=? THEN 'failed' ELSE 'pending' END,error=CASE WHEN attempt>=? THEN 'attempt_budget_exhausted' ELSE error END,generation=generation+1 WHERE task_id=? AND state='running'",(maximum,maximum,task_id))
        for item in exhausted:self.event(db,task_id,"attempt_budget_exhausted",item[0])

    def control(self, task_id, action):
        with self.transaction() as db:
            row = db.execute("SELECT state,account_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            state = row[0]
            if action in {"resume","retry"}:
                self.check_session(db,row["account_id"])
            if action == "pause" and state in {"queued", "running"}:
                next_state = "paused"
                self._interrupt_items(db,task_id)
            elif action == "resume" and state in {"paused", "interrupted"}:
                next_state = "queued"
            elif action == "cancel" and state not in TERMINAL:
                next_state = "canceled"
                db.execute("UPDATE items SET state='canceled',generation=generation+1 WHERE task_id=? AND state IN ('pending','running','retry_wait')", (task_id,))
            elif action == "retry" and state == "completed_with_errors":
                next_state = "queued"
                # Only synthetic, idempotent failed items; bounded to 3 physical attempts.
                maximum = db.execute("SELECT max_attempts FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                changed = db.execute("UPDATE items SET state='pending',generation=generation+1,error=NULL,next_run=0 WHERE task_id=? AND state IN ('failed','timed_out') AND attempt<?", (task_id, maximum)).rowcount
                if not changed:
                    raise ConflictError("No retryable items; configured physical attempt budget exhausted")
            elif (action, state) in {("pause", "paused"), ("cancel", "canceled"), ("resume", "queued")}:
                return {"id": task_id, "state": state}
            else:
                raise ConflictError(f"action {action} is unavailable in state {state}")
            db.execute("UPDATE tasks SET state=?,generation=generation+1,updated=? WHERE id=?", (next_state, self.clock(), task_id))
            self._finalize(db, task_id)
            self.event(db, task_id, "task_" + action)
            final_state=db.execute("SELECT state FROM tasks WHERE id=?",(task_id,)).fetchone()[0]
            return {"id": task_id, "state": final_state}

    def list_tasks(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM tasks ORDER BY created DESC,rowid DESC LIMIT 200")]
        return [self.task(task_id, details=False) for task_id in ids]

    def summary(self):
        with self.connect() as db:
            row = db.execute("SELECT (SELECT count(*) FROM tasks) AS tasks, coalesce(sum(state='succeeded'),0) AS saved, coalesce(sum(state='filtered'),0) AS filtered FROM items").fetchone()
            return {**dict(row), "published": 0}

    def has_runnable(self):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE state IN ('queued','running') LIMIT 1").fetchone() is not None

    def task_state(self,task_id):
        """Cheap polling without repeatedly decoding every persisted product/event."""
        with self.connect() as db:
            row=db.execute("SELECT state FROM tasks WHERE id=?",(task_id,)).fetchone()
            if not row:raise KeyError(task_id)
            return row[0]

    def task(self, task_id, details=True):
        with self.connect() as db:
            db.execute("BEGIN")  # Consistent task/items/events snapshot.
            row = db.execute("SELECT id,fixture_id,policy,state,timeout_ms,created,updated,account_id,kind,retry_mode,max_attempts,backoff_ms FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            task = dict(row)
            task["policy"] = json.loads(task["policy"])
            rows = db.execute("SELECT item_no,state,attempt,error,result FROM items WHERE task_id=? ORDER BY item_no", (task_id,)).fetchall()
            counts = {key: sum(r["state"] == key for r in rows) for key in ("pending", "retry_wait", "running", "succeeded", "filtered", "failed", "timed_out", "canceled")}
            counts["total"] = len(rows)
            counts["processed"] = sum(counts[key] for key in ITEM_TERMINAL)
            counts["accepted_skus"] = sum(json.loads(r["result"])["accepted_skus"] for r in rows if r["result"])
            counts["published"] = 0
            task["counts"] = counts
            task["source"] = "test_fixture"
            if details:
                task["items"] = [{**dict(r), "result": json.loads(r["result"]) if r["result"] else None} for r in rows]
                task["events"] = [{**dict(r), "details": json.loads(r["details"])} for r in db.execute("SELECT seq,item_no,at,code,details FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
            return task

    def export(self, task_id):
        return {"schema_version": 1, "source": "test_fixture", "real_connections_enabled": False,
                "published": 0, "task": self.task(task_id)}

    def schedule(self, fixture_id, policy, due, account_id="fixture-default"):
        self.validate_account(account_id)
        if type(due) not in {int, float} or not 0 < due < 4102444800:
            raise ValidationError("due: expected UTC epoch seconds before 2100")
        policy = validate_policy(policy)
        with self.transaction() as db:
            self.check_session(db, account_id)
            if not db.execute("SELECT 1 FROM fixtures WHERE id=?", (fixture_id,)).fetchone():
                raise ValidationError("fixture does not exist")
            ident = uuid.uuid4().hex
            state = "pending" if due > self.clock() else "missed"
            db.execute("INSERT INTO schedules(id,due,fixture_id,policy,state,task_id,account_id) VALUES (?,?,?,?,?,NULL,?)", (ident, due, fixture_id, canonical(policy), state, account_id))
            self.event(db, None, "schedule_created" if state == "pending" else "schedule_missed_skipped", schedule_id=ident)
            return {"id": ident, "state": state, "due": due}

    def tick_schedules(self):
        with self.transaction() as db:
            for row in db.execute("SELECT * FROM schedules WHERE state='pending' AND due<=?", (self.clock(),)).fetchall():
                # A >2s missed window is skipped, never replayed as a burst.
                if self.clock() - row["due"] > 2:
                    db.execute("UPDATE schedules SET state='missed' WHERE id=?", (row["id"],))
                    self.event(db, None, "schedule_missed_skipped", schedule_id=row["id"])
                    continue
                key = "schedule/" + row["id"]
                digest = hashlib.sha256(canonical([row["fixture_id"], json.loads(row["policy"]), 2000]).encode()).hexdigest()
                task = self._submit(db, row["fixture_id"], json.loads(row["policy"]), key, digest, 2000, row["account_id"])
                db.execute("UPDATE schedules SET state='submitted',task_id=? WHERE id=?", (task["id"], row["id"]))
            from .scheduling import DailyScheduler
            DailyScheduler(self).tick(db)

    def list_schedules(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,due,state,task_id FROM schedules ORDER BY due DESC LIMIT 200")]
