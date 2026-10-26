"""Self-owned offline process tree for timeout/failure cleanup acceptance only."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--folder', type=Path, required=True)
parser.add_argument('--nonce', required=True)
parser.add_argument('--role', choices=['root', 'child', 'leaf'], default='root')
parser.add_argument('--mode', choices=['wait', 'abnormal', 'zero_orphan'], default='wait')
args = parser.parse_args()
args.folder.mkdir(parents=True, exist_ok=True)
if os.name=='nt':
    import msvcrt
    lock=(args.folder/(args.role+'.lock')).open('a+b');lock.write(b'L');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
else:
    import fcntl
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    lock=(args.folder/(args.role+'.lock')).open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
(args.folder/(args.role+'.heartbeat')).write_text(str(time.monotonic_ns())+'\n')

identity={'pid':os.getpid(),'ppid':os.getppid(),'nonce':args.nonce,'role':args.role}
if os.name=='nt':
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
    import _winjob as N
    identity.update(N.identity(N.K.GetCurrentProcess()))
(args.folder/(args.role+'.json')).write_text(json.dumps(identity))
if args.role != 'leaf':
    role = 'child' if args.role == 'root' else 'leaf'
    subprocess.Popen([sys.executable, __file__, '--folder', str(args.folder), '--nonce', args.nonce, '--role', role], close_fds=True)
with (args.folder/(args.role+'.heartbeat')).open('a', buffering=1) as beat:
    fixture_deadline=time.monotonic()+30 if os.name=='nt' else float('inf')
    while time.monotonic()<fixture_deadline:
        beat.write(str(time.monotonic_ns())+'\n')
        if args.role == 'root' and (args.folder/'leaf.json').exists():
            (args.folder/'ready').touch()
            if args.mode != 'wait':
                os._exit(23 if args.mode == 'abnormal' else 0)
        time.sleep(0.02)
