"""Bounded startup reads for owned acceptance subprocesses (no shell)."""
from queue import Empty, Queue
import threading
import subprocess


def startup_line(process, timeout=8):
    result = Queue(maxsize=1)
    def read():
        try:
            result.put(process.stdout.readline())
        except Exception:
            result.put('')
    threading.Thread(target=read, daemon=True).start()
    try:
        return result.get(timeout=timeout)
    except Empty:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        raise TimeoutError('OWNED_SERVICE_START_DEADLINE') from None
