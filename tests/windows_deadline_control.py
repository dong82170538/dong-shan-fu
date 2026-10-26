"""Deterministic control-flow tests. No native API, process or thread creation."""
import ast,pathlib,types,json,hashlib,builtins,argparse
ROOT=pathlib.Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def load_functions(source,clock,config):
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_base','_terminal_decision','_supervise')]
    class Event:
        def is_set(self):return config.get('stop_at') is not None and clock.now>=config['stop_at']
    class Thread:
        def __init__(self,**kw):pass
        def start(self):pass
    class Job:
        launched=True;read_fds=[]
        def start_suspended(self,*a):return {'pid':42,'created_100ns':1}
        def resume(self):clock.now=config['initial']
        def observe(self):pass
        def root_exit(self):return config.get('exit_code',0)
        def accounting(self):return {'active':int(clock.now<config['ended_at'])}
        def cleanup(self,*a):
            clock.now=max(clock.now,config.get('cleanup_at',clock.now))
            return {'confirmed':config.get('cleanup_confirmed',True),'remaining_pids':[],'seconds':0,'accounting':{'active':0}}
        def records(self):return []
        def close(self):return []
    backend=types.SimpleNamespace(open_known_owner=lambda *a:(1,{}),live=lambda h:config.get('owner_exit_at') is None or clock.now<config['owner_exit_at'],OwnedJob=Job,close=lambda h:None)
    real_import=builtins.__import__
    def mock_import(name,*a,**kw):return backend if name=='_winjob' else real_import(name,*a,**kw)
    builtins_map=dict(vars(builtins));builtins_map['__import__']=mock_import
    ns={'__package__':'','__builtins__':builtins_map,'sys':types.SimpleNamespace(platform='win32'),'os':types.SimpleNamespace(getpid=lambda:24,getppid=lambda:12,environ={}), 'time':clock,'threading':types.SimpleNamespace(Event=Event,Thread=Thread),'secrets':types.SimpleNamespace(token_hex=lambda n:'fake-nonce'),'MARKER':'WINDOWS_OWNED_RUNNER_ACTIVE','CAPTURE_LIMIT':131072,'_save_report':lambda *a:None}
    exec(compile(ast.Module(body=body,type_ignores=[]),str(source),'exec'),ns)
    ast_hash=hashlib.sha256(ast.dump(ast.Module(body=body,type_ignores=[]),include_attributes=False).encode()).hexdigest()
    return ns,ast_hash
class Clock:
    def __init__(self,next_at=None):self.now=0.0;self.next_at=next_at;self.trace=[]
    def monotonic(self):
        self.trace.append({'read':self.now})
        if len(self.trace)>300:raise RuntimeError('deterministic test exceeded bounded clock operations')
        return self.now
    def sleep(self,seconds):
        before=self.now
        if self.next_at is not None and self.now<self.next_at:self.now=self.next_at
        else:self.now+=seconds
        self.trace.append({'sleep_requested':seconds,'before':before,'after':self.now})
def simulate(source,config):
    clock=Clock(config.get('next_at'));ns,functions_hash=load_functions(source,clock,config)
    actual=ns['_supervise']({'caller_pid':12,'caller_identity':{},'env':{},'argv':['SYNTHETIC_NOT_EXECUTED'],'cwd':str(ROOT),'timeout':1.0})
    return {'actual':actual,'clock_trace':clock.trace,'functions_ast_sha256':functions_hash,'source_sha256':digest(source),'native_calls':0,'native_processes_started':0,'native_threads_started':0}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--old-source',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    source=ROOT/'scripts'/'owned_process_windows.py';old=pathlib.Path(a.old_source)
    before={'source':digest(source),'test':digest(pathlib.Path(__file__)),'old_source':digest(old)}
    cases=[]
    f1={'initial':.99,'ended_at':1.005,'next_at':1.01}
    baseline=simulate(old,f1)
    reproduced=baseline['actual']['status']=='PASS' and not baseline['actual']['timed_out'] and baseline['actual']['seconds']==1.01
    assert reproduced,'original unchanged control flow did not reproduce the reviewed F1'
    ns,_=load_functions(source,Clock(),{'initial':0,'ended_at':0});decision=ns['_terminal_decision']
    # Pure helper table binds the intended priority at each side of the boundary.
    for now in (.999,1.0,1.001):
        for code in (None,0,7):
            for stopped in (False,True):
                if now>=1:expected=('FAIL','timeout',True)
                elif stopped:expected=('FAIL','stopped',False)
                elif code is None:expected=None
                elif code:expected=('FAIL','command_nonzero',False)
                else:expected=('PASS','command_exit_zero',False)
                actual=decision(now=now,deadline=1,owner_alive=True,stopped=stopped,exit_code=code,active=0)
                cases.append({'name':f'priority_t{now}_exit{code}_stop{stopped}','kind':'pure production decision function','expected':expected,'actual':actual,'test_status':'PASS' if actual==expected else 'FAIL'})
        actual=decision(now=now,deadline=1,owner_alive=False,stopped=True,exit_code=0,active=0)
        expected=('FAIL','timeout',True) if now>=1 else ('FAIL','owner_exited',False)
        cases.append({'name':f'owner_stop_normal_t{now}','kind':'pure production decision function','expected':expected,'actual':actual,'test_status':'PASS' if actual==expected else 'FAIL'})
    scenarios=[
      ('terminal_before',dict(initial=.999,ended_at=.998),('PASS','command_exit_zero',False)),
      ('terminal_on',dict(initial=1.0,ended_at=1.0),('FAIL','timeout',True)),
      ('terminal_after',dict(initial=1.001,ended_at=1.001),('FAIL','timeout',True)),
      ('settle_before',dict(initial=.98,ended_at=.995,next_at=.999),('PASS','command_exit_zero',False)),
      ('settle_on',dict(initial=.99,ended_at=1.0,next_at=1.0),('FAIL','timeout',True)),
      ('review_f1_settle_after',f1,('FAIL','timeout',True)),
      ('stop_normal_before',dict(initial=.999,ended_at=.998,stop_at=.999),('FAIL','stopped',False)),
      ('stop_normal_on',dict(initial=1.0,ended_at=1.0,stop_at=1.0),('FAIL','timeout',True)),
      ('stop_normal_after',dict(initial=1.001,ended_at=1.001,stop_at=1.0),('FAIL','timeout',True)),
      ('owner_stop_normal_before',dict(initial=.999,ended_at=.998,stop_at=.999,owner_exit_at=.999),('FAIL','owner_exited',False)),
      ('nonzero_before',dict(initial=.999,ended_at=.998,exit_code=7),('FAIL','command_nonzero',False)),
      ('nonzero_stop_before',dict(initial=.999,ended_at=.998,exit_code=7,stop_at=.999),('FAIL','stopped',False)),
      ('nonzero_on',dict(initial=1.0,ended_at=1.0,exit_code=7),('FAIL','timeout',True)),
      ('success_cleanup_confirmation_after',dict(initial=.99,ended_at=.99,cleanup_at=1.01),('FAIL','timeout',True)),
      ('success_cleanup_confirmation_on',dict(initial=.99,ended_at=.99,cleanup_at=1.0),('FAIL','timeout',True)),
      ('cleanup_uncertain',dict(initial=.99,ended_at=.99,cleanup_confirmed=False),('FAIL','cleanup_unconfirmed',False)),
      ('nonzero_not_rehabilitated',dict(initial=.99,ended_at=.99,exit_code=7,cleanup_at=1.01),('FAIL','command_nonzero',True)),
      ('stop_not_rehabilitated',dict(initial=.99,ended_at=.99,stop_at=.99,cleanup_at=1.01),('FAIL','stopped',True)),
      ('timeout_not_rehabilitated',dict(initial=.99,ended_at=1.005,next_at=1.01,cleanup_at=1.02),('FAIL','timeout',True)),
    ]
    for name,config,expected in scenarios:
        observed=simulate(source,config);r=observed['actual'];actual=(r['status'],r['reason'],r['timed_out'])
        cases.append({'name':name,'kind':'unchanged production functions AST with controlled clock/Job substitutes; NOT native scheduling','config':config,'expected':expected,'actual_decision':actual,**observed,'test_status':'PASS' if actual==expected else 'FAIL'})
    after={'source':digest(source),'test':digest(pathlib.Path(__file__)),'old_source':digest(old)}
    result={'kind':'DETERMINISTIC ONLY; not a real Windows scheduling reproduction','source_functions':'AST extracted unchanged from this exact source snapshot','fingerprints_before':before,'fingerprints_after':after,'inputs_unchanged':before==after,'original_f1_reproduced':reproduced,'original_f1':baseline,'native_calls':0,'native_processes_started':0,'native_threads_started':0,'cases':cases,'summary':{s:sum(c['test_status']==s for c in cases) for s in ('PASS','FAIL')}}
    pathlib.Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'deterministic_summary':result['summary'],'inputs_unchanged':before==after,'original_f1_reproduced':reproduced,'output':a.output}),flush=True)
    return 0 if before==after and result['summary']['FAIL']==0 else 1
if __name__=='__main__':raise SystemExit(main())
