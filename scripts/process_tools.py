"""Bounded startup reads for owned acceptance subprocesses (no shell)."""
from queue import Empty, Queue
import threading
import subprocess
import os


def startup_line(process, timeout=8):
    result = Queue(maxsize=1)
    def read():
        try:
            result.put((process.stdout.readline(),None))
        except Exception as problem:
            result.put((None,problem))
    threading.Thread(target=read, daemon=True).start()
    try:
        line,problem=result.get(timeout=timeout)
        if problem is not None:
            raise RuntimeError('OWNED_SERVICE_START_READ_FAILED:'+type(problem).__name__) from problem
        return line
    except Empty:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        raise TimeoutError('OWNED_SERVICE_START_DEADLINE') from None


def stop_service(process,timeout=10):
    """Graceful owned pipe on Windows; POSIX signal behavior unchanged."""
    try:
        if process.poll() is None:
            if os.name=='nt':
                if process.stdin is None:raise RuntimeError('OWNED_SERVICE_PIPE_REQUIRED')
                process.stdin.write('DSF_SHUTDOWN\n');process.stdin.flush();process.stdin.close()
            else:process.terminate()
            try:process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=3)
                raise TimeoutError('OWNED_SERVICE_STOP_DEADLINE')
        if process.returncode!=0:raise RuntimeError('OWNED_SERVICE_NONZERO_EXIT')
    finally:
        for stream in (process.stdout,process.stderr):
            if stream:stream.close()
