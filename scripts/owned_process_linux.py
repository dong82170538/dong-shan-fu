"""Linux-only test supervision: own descendants, stable pidfds, bounded cleanup.

This is a test harness, not an OS sandbox. One command at a time in a dedicated
supervisor with no pre-existing children. No name/port matching or elevation.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time

_LOCK = threading.Lock()


class OwnershipUnsupported(RuntimeError):
    pass


def _stat(pid):
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = text[text.rfind(')')+2:].split()
    return {'pid': pid, 'ppid': int(fields[1]), 'start_ticks': int(fields[19]), 'state': fields[0]}


def _descendants():
    # Some supported Linux kernels omit /proc/PID/task/PID/children. Only retain
    # stat records linked to this supervisor, never select by names or ports.
    table = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            info = _stat(int(entry.name))
        except (PermissionError, ProcessLookupError):
            continue
        if info:
            table[info['pid']] = info
    owned = {os.getpid()}
    found = {}
    while True:
        added = {pid: row for pid, row in table.items() if row['ppid'] in owned and pid not in owned}
        if not added:
            return found
        found.update(added)
        owned.update(added)


def support():
    if sys.platform != 'linux' or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise OwnershipUnsupported('LINUX_SUBREAPER_PIDFD_REQUIRED_WINDOWS_JOB_NOT_IMPLEMENTED')
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, current process only
            raise OSError(ctypes.get_errno(), 'subreaper unavailable')
        fd = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(fd, 0)
        finally:
            os.close(fd)
        if _stat(os.getpid()) is None:
            raise OSError('own stat unavailable')
    except (OSError, AttributeError) as error:
        raise OwnershipUnsupported('LINUX_OWNERSHIP_PRIMITIVE_UNAVAILABLE') from error


def _remember(known):
    for pid, row in _descendants().items():
        if pid in known:
            continue
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            continue
        try:
            again = _stat(pid)
        except BaseException:
            os.close(fd)
            raise
        if again is None or (again['ppid'], again['start_ticks']) != (row['ppid'], row['start_ticks']):
            os.close(fd)
            continue
        known[pid] = {**row, 'fd': fd, 'signals': [], 'reaped': False}


def _send(row, sig):
    try:
        signal.pidfd_send_signal(row['fd'], sig)
        row['signals'].append(signal.Signals(sig).name)
    except ProcessLookupError:
        pass


def _exited(row):
    poller = select.poll()
    poller.register(row['fd'], select.POLLIN)
    return bool(poller.poll(0))


def _reap(process, known):
    process.poll()
    if process.returncode is not None and process.pid in known:
        known[process.pid]['reaped'] = True
        known[process.pid]['wait_exit_code'] = process.returncode
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if not pid:
            break
        if pid == process.pid:
            process.returncode = os.waitstatus_to_exitcode(status)
        if pid in known:
            known[pid]['reaped'] = True
            known[pid]['wait_exit_code'] = os.waitstatus_to_exitcode(status)


def _cleanup(process, known, grace=0.35, limit=3.0):
    started = time.monotonic()
    empty_scans = 0
    while True:
        _remember(known)
        elapsed = time.monotonic()-started
        for row in known.values():
            if elapsed >= grace:
                _send(row, signal.SIGKILL)
            elif 'SIGTERM' not in row['signals']:
                _send(row, signal.SIGTERM)
        _reap(process, known)
        remaining = _descendants()
        empty_scans = empty_scans+1 if not remaining and all(_exited(row) for row in known.values()) else 0
        if empty_scans >= 3:
            return {'confirmed': True, 'remaining_pids': [], 'seconds': time.monotonic()-started}
        if elapsed >= limit:
            return {'confirmed': False, 'remaining_pids': sorted(remaining), 'seconds': elapsed}
        time.sleep(0.02)


def run_owned(argv, *, cwd, env=None, timeout=60, stop_event=None):
    """Always clean owned descendants, including after a successful root exit."""
    support()
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('ONE_OWNED_COMMAND_AT_A_TIME')
    known = {}
    process = None
    result = None
    started = time.monotonic()
    try:
        if _descendants():
            raise RuntimeError('SUPERVISOR_HAS_PREEXISTING_CHILDREN')
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            if stop_event and stop_event.is_set():
                return {'status': 'FAIL', 'reason': 'supervisor_signal_before_start', 'exit_code': None,
                        'timed_out': False, 'runner_pid': None, 'supervisor_pid': os.getpid(),
                        'cleanup': {'confirmed': True, 'remaining_pids': []}, 'stdout': '', 'stderr': ''}
            process = subprocess.Popen(list(argv), cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            timed_out = False
            while process.poll() is None:
                _remember(known)
                if stop_event and stop_event.is_set():
                    reason = 'supervisor_signal'
                    break
                if time.monotonic()-started >= timeout:
                    timed_out = True
                    reason = 'timeout'
                    break
                time.sleep(0.02)
            else:
                reason = 'runner_exit_zero' if process.returncode == 0 else 'runner_exit_nonzero'
            leftovers_before_settle = sorted(_descendants())
            # A successful runner can close before already-exiting browser
            # helpers are reaped. Give natural exits a short bounded window;
            # a live orphan that persists is still a failed command contract.
            settle_started = time.monotonic()
            if reason == 'runner_exit_zero':
                while _descendants() and time.monotonic()-settle_started < 0.25:
                    _remember(known); _reap(process, known); time.sleep(0.02)
            leftovers = sorted(_descendants())
            cleanup = _cleanup(process, known)
            stdout.seek(0); stderr.seek(0)
            output, errors = stdout.read(16_000_000).decode('utf-8', 'replace'), stderr.read(16_000_000).decode('utf-8', 'replace')
            result = {'status': 'PASS' if reason == 'runner_exit_zero' and cleanup['confirmed'] and not leftovers else 'FAIL',
                      'reason': reason, 'timed_out': timed_out, 'supervisor_pid': os.getpid(), 'runner_pid': process.pid,
                      'exit_code': process.returncode, 'cleanup': cleanup, 'leftovers_at_runner_exit': leftovers,
                      'descendants_before_natural_exit_settle': leftovers_before_settle,
                      'natural_exit_settle_seconds': time.monotonic()-settle_started-cleanup['seconds'],
                      'owned_processes': [{k: v for k, v in row.items() if k != 'fd'} for row in known.values()],
                      'seconds': time.monotonic()-started, 'stdout': output, 'stderr': errors,
                      'platform': sys.platform, 'ownership': 'explicit Popen + Linux subreaper ancestry + stable pidfds',
                      'windows': 'BLOCKED: equivalent Job ownership not implemented or tested'}
            for row in result['owned_processes']:
                row['exit_observed_via_pidfd'] = _exited(known[row['pid']])
            if not all(row['exit_observed_via_pidfd'] for row in result['owned_processes']):
                result['status'] = 'FAIL'; result['cleanup']['confirmed'] = False
            return result
    finally:
        try:
            if process and _descendants():
                final_cleanup = _cleanup(process, known)
                if result is not None:
                    result['status'] = 'FAIL'
                    result['late_descendant_cleanup'] = final_cleanup
                    if not final_cleanup['confirmed']:
                        result['cleanup']['confirmed'] = False
        finally:
            for row in known.values():
                os.close(row['fd'])
            _LOCK.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--owner-pid', type=int, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 0 < args.timeout <= 600 or not args.command or args.command[0] != '--':
        parser.error('bounded timeout and -- command required')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    try:
        support()
        # Parent wrapper death requests cleanup even for a standalone Node run.
        if os.getppid() != args.owner_pid:
            stop.set()
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            raise OwnershipUnsupported('PARENT_DEATH_NOTIFICATION_UNAVAILABLE')
        if os.getppid() != args.owner_pid:
            stop.set()
        result = run_owned(args.command[1:], cwd=args.cwd, timeout=args.timeout, stop_event=stop)
    except OwnershipUnsupported as error:
        result = {'status': 'BLOCKED', 'reason': str(error), 'platform': sys.platform, 'command_started': False}
    except Exception as error:
        result = {'status': 'FAIL', 'reason': type(error).__name__, 'cleanup_unconfirmed': True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({k: v for k, v in result.items() if k not in {'stdout', 'stderr'}}, indent=2)+'\n', encoding='utf-8')
    sys.stdout.write(result.get('stdout', '')); sys.stderr.write(result.get('stderr', ''))
    return 0 if result['status'] == 'PASS' else 2 if result['status'] == 'BLOCKED' else 1


if __name__ == '__main__':
    sys.exit(main())
