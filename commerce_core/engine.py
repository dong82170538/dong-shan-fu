"""Single engine owner, bounded physical workers, durable fenced completion."""
from concurrent.futures import ThreadPoolExecutor
import copy
import os
import sqlite3
import threading
import time

from .adapters import FixtureAdapter, FixtureFailure
from .domain import ValidationError, integer, validate_result


class Engine:
    def __init__(self, store, concurrency=2, adapter=None, clock=time.monotonic):
        self.store = store
        self.concurrency = integer(concurrency, "concurrency", 1, 8)
        self.adapter = adapter or FixtureAdapter()
        self.clock = clock
        self.active = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.pool = None
        self.lock_file = None
        self.health = "stopped"
        self.max_observed_workers = 0

    def start(self):
        if self.thread is not None:
            raise RuntimeError("Engine has already been started")
        self.lock_file = open(self.store.path + ".lock", "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock_file.seek(0)
                if not self.lock_file.read(1):
                    self.lock_file.write(b"0")
                    self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("Another engine owns this database") from None
        try:
            self.store.recover()
            self.pool = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="fixture")
            self.health = "ready"
            self.thread = threading.Thread(target=self._run, daemon=True, name="queue")
            self.thread.start()
        except BaseException:
            self.lock_file.close()
            self.lock_file = None
            raise
        return self

    def _run(self):
        while not self.stop_event.is_set():
            try:
                for future, entry in list(self.active.items()):
                    claim, deadline, timed_out = entry
                    outcome = future.result() if future.done() else None
                    observed_time = outcome["finished_at"] if outcome is not None else self.clock()
                    if not timed_out and observed_time >= deadline:
                        self.store.fail(claim, "timed_out")
                        entry[2] = True
                        timed_out = True
                    if outcome is None:
                        continue
                    if timed_out:
                        self.store.record_late_result(claim)
                    else:
                        if outcome["error"]:
                            self.store.fail(claim, outcome["error"])
                        else:
                            try:
                                self.store.complete(claim, outcome["product"], inject_save_failure=claim["attempt"] <= claim["raw"].get("test_behavior", {}).get("save_fail_attempts", 0))
                            except sqlite3.Error:
                                self.store.fail(claim, "save_failed")
                    del self.active[future]
                self.store.tick_schedules()
                while len(self.active) < self.concurrency and not self.stop_event.is_set():
                    claim = self.store.claim()
                    if claim is None:
                        break
                    deadline = self.clock() + claim["timeout_ms"] / 1000
                    future = self.pool.submit(self._compute, claim)
                    self.active[future] = [claim, deadline, False]
                    self.max_observed_workers = max(self.max_observed_workers, len(self.active))
                self.health = "ready"
            except sqlite3.Error:
                # No success is claimed; preserve durable raw inputs and retry recording.
                self.health = "storage_unavailable"
            self.stop_event.wait(0.01)

    def _compute(self, claim):
        error, product = None, None
        try:
            product = self.adapter.run(copy.deepcopy(claim["raw"]), copy.deepcopy(claim["policy"]), claim["attempt"])
        except FixtureFailure:
            error = "adapter_failed"
        except (ValidationError, ArithmeticError):
            error = "calculation_failed"
        except Exception:
            error = "adapter_failed"
        if error is None:
            try:
                validate_result(product, claim["raw"], claim["policy"])
            except Exception:
                error, product = "adapter_failed", None
        return {"finished_at": self.clock(), "product": product, "error": error}

    def stop(self):
        if self.thread is None or self.lock_file is None:
            return
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("Queue did not stop; engine lock retained")
        # Workers only compute. After shutdown they cannot commit a late result.
        self.pool.shutdown(wait=True, cancel_futures=True)
        try:
            self.store.recover()
        finally:
            self.lock_file.close()
            self.lock_file = None
            self.health = "stopped"

    def status(self):
        health = "worker_failed" if self.thread is not None and not self.thread.is_alive() and self.health != "stopped" else self.health
        return {"health": health, "concurrency": self.concurrency,
                "physical_workers": len(self.active), "max_observed_workers": self.max_observed_workers,
                "real_connections_enabled": False, "published": 0}
