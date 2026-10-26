"""Own offline service-stop fixture: heartbeat + kernel lock; bounded lifetime."""
import argparse,json,os,signal,sys,threading,time
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--folder',required=True);parser.add_argument('--nonce');parser.add_argument('--mode',choices=['ignore','nonzero']);parser.add_argument('--check-lock',action='store_true');args=parser.parse_args()
folder=Path(args.folder);lock=(folder/'stop-fixture.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
def acquire():
 if os.name=='nt':
  import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
 else:
  import fcntl;fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def release():
 lock.seek(0)
 if os.name=='nt':
  import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
 else:
  import fcntl;fcntl.flock(lock,fcntl.LOCK_UN)
if args.check_lock:
 try:acquire();release();print(json.dumps({'lock_reacquired':True}));raise SystemExit(0)
 finally:lock.close()
acquire();done=threading.Event();exit_code=0
if args.mode=='ignore' and os.name!='nt':signal.signal(signal.SIGTERM,signal.SIG_IGN)
if args.mode=='nonzero':
 def request_exit():
  global exit_code
  exit_code=23;done.set()
 if os.name!='nt':signal.signal(signal.SIGTERM,lambda *_:request_exit())
 def read_command():
  sys.stdin.buffer.readline(128);request_exit()
 if os.name=='nt':threading.Thread(target=read_command,daemon=True).start()
(folder/'stop-fixture.json').write_text(json.dumps({'pid':os.getpid(),'nonce':args.nonce,'mode':args.mode}),encoding='utf-8')
beat=folder/'stop-fixture.heartbeat';beat.write_text(str(time.monotonic_ns()),encoding='ascii');print('OWNED_STOP_READY',flush=True)
try:
 end=time.monotonic()+20
 while not done.is_set() and time.monotonic()<end:
  with beat.open('a',encoding='ascii') as stream:stream.write('\n'+str(time.monotonic_ns()))
  done.wait(.02)
 if not done.is_set():exit_code=24
finally:release();lock.close()
raise SystemExit(exit_code)
