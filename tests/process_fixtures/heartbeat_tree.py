"""Self-owned offline process tree for timeout/failure cleanup acceptance only."""
import argparse
import fcntl
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
signal.signal(signal.SIGTERM, signal.SIG_IGN)  # Prove bounded force cleanup too.
lock = (args.folder/(args.role+'.lock')).open('w')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
(args.folder/(args.role+'.json')).write_text(json.dumps({'pid': os.getpid(), 'ppid': os.getppid(), 'nonce': args.nonce, 'role': args.role}))
if args.role != 'leaf':
    role = 'child' if args.role == 'root' else 'leaf'
    subprocess.Popen([sys.executable, __file__, '--folder', str(args.folder), '--nonce', args.nonce, '--role', role], close_fds=True)
with (args.folder/(args.role+'.heartbeat')).open('a', buffering=1) as beat:
    while True:
        beat.write(str(time.monotonic_ns())+'\n')
        if args.role == 'root' and (args.folder/'leaf.json').exists():
            (args.folder/'ready').touch()
            if args.mode != 'wait':
                os._exit(23 if args.mode == 'abnormal' else 0)
        time.sleep(0.02)
