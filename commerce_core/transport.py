"""Offline request deadline wrapper; no production HTTP adapter is installed."""
import threading
import time
from .domain import integer


class DeadlineRequest:
    """One terminal state. Cancellation and transport callbacks are serialized."""
    def __init__(self,future,clock=time.monotonic,timeout_ms=10000):
        self.future=future
        self.clock=clock
        self.deadline=clock()+integer(timeout_ms,'request_timeout_ms',1,600000)/1000
        self.state='pending';self.value=None;self.events=[];self.attempts=1
        self.finished_at=None
        self.lock=threading.RLock()
        self.future.add_done_callback(self._finished)

    def _finished(self,_future):
        timestamp=self.clock()
        with self.lock:
            self.finished_at=timestamp

    def poll(self):
        with self.lock:
            if self.state!='pending':return self.state,self.value
            done=self.future.done()
            if done and self.finished_at is None:
                # Let a callback waiting on this lock record its completion stamp.
                if self.clock()>=self.deadline:
                    self.state='timed_out';self.events.append('http_timeout')
                return self.state,self.value
            observed=self.finished_at if self.finished_at is not None else self.clock()
            if observed>=self.deadline:
                self.state='timed_out';self.events.append('http_timeout')
            elif done:
                try:
                    self.value=self.future.result();self.state='completed'
                except Exception:
                    self.state='failed';self.events.append('http_transport_failed')
            return self.state,self.value

    def cancel(self):
        with self.lock:
            if self.state=='pending':
                self.state='canceled'
                self.future.cancel()
            return self.state
