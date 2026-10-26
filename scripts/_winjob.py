"""Private Windows x64 handle backend. No PID-based termination or breakaway."""
import ctypes as C
from ctypes import wintypes as W
import os,subprocess,shutil,time,msvcrt
K=C.WinDLL('kernel32',use_last_error=True)
SIZE=C.c_size_t
class BASIC(C.Structure):
    _fields_=[('p',C.c_longlong),('j',C.c_longlong),('flags',W.DWORD),('minimum',SIZE),('maximum',SIZE),('active_limit',W.DWORD),('affinity',SIZE),('priority',W.DWORD),('scheduling',W.DWORD)]
class IO(C.Structure):_fields_=[(n,C.c_ulonglong) for n in ('ro','wo','oo','rb','wb','ob')]
class LIMITS(C.Structure):_fields_=[('basic',BASIC),('io',IO),('pm',SIZE),('jm',SIZE),('peakp',SIZE),('peakj',SIZE)]
class ACCOUNT(C.Structure):_fields_=[('tu',C.c_longlong),('tk',C.c_longlong),('pu',C.c_longlong),('pk',C.c_longlong),('faults',W.DWORD),('total',W.DWORD),('active',W.DWORD),('terminated',W.DWORD)]
class SECURITY(C.Structure):_fields_=[('length',W.DWORD),('descriptor',C.c_void_p),('inherit',W.BOOL)]
class STARTUP(C.Structure):
    _fields_=[('cb',W.DWORD),('reserved',W.LPWSTR),('desktop',W.LPWSTR),('title',W.LPWSTR),('x',W.DWORD),('y',W.DWORD),('xs',W.DWORD),('ys',W.DWORD),('xc',W.DWORD),('yc',W.DWORD),('fill',W.DWORD),('flags',W.DWORD),('show',W.WORD),('reserved2',W.WORD),('reservedp',C.POINTER(W.BYTE)),('stdin',W.HANDLE),('stdout',W.HANDLE),('stderr',W.HANDLE)]
class STARTUPEX(C.Structure):_fields_=[('startup',STARTUP),('attributes',C.c_void_p)]
class PROCESS(C.Structure):_fields_=[('process',W.HANDLE),('thread',W.HANDLE),('pid',W.DWORD),('tid',W.DWORD)]
DECL={
'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)],W.BOOL),
'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),
'CreateProcessW':([W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,C.c_void_p,W.LPCWSTR,C.POINTER(STARTUPEX),C.POINTER(PROCESS)],W.BOOL),
'InitializeProcThreadAttributeList':([C.c_void_p,W.DWORD,W.DWORD,C.POINTER(SIZE)],W.BOOL),
'UpdateProcThreadAttribute':([C.c_void_p,W.DWORD,SIZE,C.c_void_p,SIZE,C.c_void_p,C.c_void_p],W.BOOL),
'DeleteProcThreadAttributeList':([C.c_void_p],None),
'CreatePipe':([C.POINTER(W.HANDLE),C.POINTER(W.HANDLE),C.POINTER(SECURITY),W.DWORD],W.BOOL),
'SetHandleInformation':([W.HANDLE,W.DWORD,W.DWORD],W.BOOL),
'GetHandleInformation':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),
'CreateFileW':([W.LPCWSTR,W.DWORD,W.DWORD,C.POINTER(SECURITY),W.DWORD,W.DWORD,W.HANDLE],W.HANDLE),
'CloseHandle':([W.HANDLE],W.BOOL),
'ResumeThread':([W.HANDLE],W.DWORD),
'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
'GetExitCodeProcess':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),
'GetProcessId':([W.HANDLE],W.DWORD),
'GetCurrentProcess':([],W.HANDLE),
'GetProcessTimes':([W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)],W.BOOL),
'OpenProcess':([W.DWORD,W.BOOL,W.DWORD],W.HANDLE),
'IsProcessInJob':([W.HANDLE,W.HANDLE,C.POINTER(W.BOOL)],W.BOOL)}
for n,(a,r) in DECL.items():getattr(K,n).argtypes=a;getattr(K,n).restype=r

def check(value,operation):
    if not value:
        e=C.get_last_error();raise OSError(e,operation+': '+C.FormatError(e))
    return value

def close(h):
    if h:check(K.CloseHandle(h),'CloseHandle')

def identity(h):
    ts=[W.FILETIME() for _ in range(4)]
    check(K.GetProcessTimes(h,*(C.byref(t) for t in ts)),'GetProcessTimes')
    return {'pid':int(K.GetProcessId(h)),'created_100ns':(ts[0].dwHighDateTime<<32)|ts[0].dwLowDateTime}

def live(h):
    v=K.WaitForSingleObject(h,0)
    if v not in (0,258):raise OSError(C.get_last_error(),'WaitForSingleObject failed')
    return v==258

def open_known_owner(pid,expected=None):
    # Caller must have independently established its direct-parent relationship.
    h=check(K.OpenProcess(0x100000|0x1000,False,pid),'OpenProcess(known owner query/synchronize only)')
    try:
        got=identity(h)
        if expected and got!=expected:raise ValueError('owner creation identity mismatch')
        if got['created_100ns']>identity(K.GetCurrentProcess())['created_100ns'] or not live(h):raise ValueError('owner identity/liveness invalid')
        return h,got
    except BaseException:close(h);raise

def abi_audit():
    sizes={n:C.sizeof(c) for n,c in [('BASIC',BASIC),('IO',IO),('LIMITS',LIMITS),('ACCOUNT',ACCOUNT),('SECURITY',SECURITY),('STARTUP',STARTUP),('STARTUPEX',STARTUPEX),('PROCESS',PROCESS)]}
    expected={'BASIC':64,'IO':48,'LIMITS':144,'ACCOUNT':48,'SECURITY':24,'STARTUP':104,'STARTUPEX':112,'PROCESS':24}
    ok=os.name=='nt' and C.sizeof(C.c_void_p)==8 and sizes==expected and STARTUPEX.attributes.offset==104 and BASIC.minimum.offset==24 and PROCESS.pid.offset==16
    return {'pass':ok,'sizes':sizes,'expected':expected,'pointer_bits':C.sizeof(C.c_void_p)*8,'apis':{n:{'args':[x.__name__ for x in a],'result':r.__name__ if r else 'void'} for n,(a,r) in DECL.items()},'job_list_attribute':'0x2000D','handle_list_attribute':'0x20002'}

class OwnedJob:
    def __init__(self):
        if not abi_audit()['pass']:raise RuntimeError('unsupported ABI; no command created')
        self.job=None;self.pi=PROCESS();self.observed={};self.read_fds=[];self.launched=False;self.resumed=False;self.setup_handles=[]
        self.job=check(K.CreateJobObjectW(None,None),'CreateJobObjectW')
        try:
            check(K.SetHandleInformation(self.job,1,0),'non-inheritable Job handle')
            flags=W.DWORD();check(K.GetHandleInformation(self.job,C.byref(flags)),'GetHandleInformation')
            if flags.value&1:raise RuntimeError('Job handle unexpectedly inheritable')
            info=LIMITS();info.basic.flags=0x2000
            check(K.SetInformationJobObject(self.job,9,C.byref(info),C.sizeof(info)),'KILL_ON_JOB_CLOSE')
        except BaseException:close(self.job);self.job=None;raise
    def start_suspended(self,argv,cwd,env):
        sa=SECURITY(C.sizeof(SECURITY),None,True);parents=[];writers=[];attrs=None;initialized=False
        try:
            for _ in range(2):
                rd=W.HANDLE();wr=W.HANDLE();check(K.CreatePipe(C.byref(rd),C.byref(wr),C.byref(sa),0),'CreatePipe')
                parents.append(rd.value);writers.append(wr.value)
                check(K.SetHandleInformation(rd,1,0),'non-inheritable pipe reader')
            nul=K.CreateFileW('NUL',0x80000000,3,C.byref(sa),3,0,None)
            if nul==C.c_void_p(-1).value:check(False,'CreateFileW(NUL)')
            writers.append(nul)
            size=SIZE();K.InitializeProcThreadAttributeList(None,2,0,C.byref(size))
            if not size.value:check(False,'attribute list size')
            attrs=C.create_string_buffer(size.value);check(K.InitializeProcThreadAttributeList(attrs,2,0,C.byref(size)),'InitializeProcThreadAttributeList');initialized=True
            jobs=(W.HANDLE*1)(self.job);handles=(W.HANDLE*3)(nul,writers[0],writers[1])
            check(K.UpdateProcThreadAttribute(attrs,0,0x2000D,jobs,C.sizeof(jobs),None,None),'atomic JOB_LIST attribute')
            check(K.UpdateProcThreadAttribute(attrs,0,0x20002,handles,C.sizeof(handles),None,None),'explicit HANDLE_LIST attribute')
            si=STARTUPEX();si.startup.cb=C.sizeof(si);si.startup.flags=0x100;si.startup.stdin=nul;si.startup.stdout=writers[0];si.startup.stderr=writers[1];si.attributes=C.cast(attrs,C.c_void_p)
            exe=argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0],path=env.get('PATH'))
            if not exe or not os.path.isfile(exe):raise FileNotFoundError('command executable unavailable')
            if os.path.splitext(exe)[1].lower() in ('.cmd','.bat'):raise ValueError('batch files require a shell and are refused')
            cmd=C.create_unicode_buffer(subprocess.list2cmdline(argv))
            block=C.create_unicode_buffer('\0'.join(k+'='+v for k,v in sorted(env.items(),key=lambda x:x[0].upper()))+'\0\0')
            check(K.CreateProcessW(exe,cmd,None,None,True,0x80000|0x4|0x8000000|0x400,block,cwd,C.byref(si),C.byref(self.pi)),'CreateProcessW(atomic Job, suspended, shell=False)')
            self.launched=True
            inside=W.BOOL();check(K.IsProcessInJob(self.pi.process,self.job,C.byref(inside)),'verify Job before first instruction')
            if not inside.value:raise RuntimeError('pre-execution Job membership verification failed')
            ident=identity(self.pi.process)
            if ident['pid']!=self.pi.pid:raise RuntimeError('root process handle identity mismatch')
            self.observed[(ident['pid'],ident['created_100ns'])]=(self.pi.process,dict(ident,basis='CreateProcessW handle + creation identity + pre-resume Job membership'))
            while parents:
                h=parents[0];fd=msvcrt.open_osfhandle(h,os.O_RDONLY|os.O_BINARY)
                parents.pop(0);self.read_fds.append(fd)
            return ident
        finally:
            if initialized:K.DeleteProcThreadAttributeList(attrs)
            for h in writers:close(h)
            for h in parents:close(h)
    def resume(self):
        if K.ResumeThread(self.pi.thread)==0xffffffff:check(False,'ResumeThread')
        self.resumed=True;close(self.pi.thread);self.pi.thread=None
    def accounting(self):
        a=ACCOUNT();check(K.QueryInformationJobObject(self.job,1,C.byref(a),C.sizeof(a),None),'Job accounting')
        return {'total':a.total,'active':a.active,'terminated':a.terminated}
    def pids(self):
        count=4096;buf=C.create_string_buffer(8+count*C.sizeof(SIZE))
        check(K.QueryInformationJobObject(self.job,3,buf,C.sizeof(buf),None),'Job-owned PID list (bounded at 4096)')
        assigned=W.DWORD.from_buffer(buf,0).value;n=W.DWORD.from_buffer(buf,4).value
        if assigned>count or n>count:raise RuntimeError('Job PID enumeration exceeds bound')
        return [int(SIZE.from_buffer(buf,8+i*C.sizeof(SIZE)).value) for i in range(n)]
    def observe(self):
        for pid in self.pids():
            if any(v[1]['pid']==pid and live(v[0]) for v in self.observed.values()):continue
            h=K.OpenProcess(0x100000|0x1000,False,pid)
            if not h:continue # accounting remains authoritative; never terminate by PID.
            keep=False
            try:
                inside=W.BOOL();check(K.IsProcessInJob(h,self.job,C.byref(inside)),'verify observed descendant Job')
                if inside.value:
                    ident=identity(h);key=(pid,ident['created_100ns'])
                    if key not in self.observed and len(self.observed)<1024:
                        self.observed[key]=(h,dict(ident,basis='own Job PID list + query-only handle + creation identity + Job membership'));keep=True
            finally:
                if not keep:close(h)
    def root_exit(self):
        if not self.launched or live(self.pi.process):return None
        code=W.DWORD();check(K.GetExitCodeProcess(self.pi.process,C.byref(code)),'root exit code');return code.value
    def cleanup(self,budget=4):
        begin=time.monotonic();confirmed=False;remaining=[];error=None;acct=None;zero=0
        try:
            self.observe();check(K.TerminateJobObject(self.job,0xE002),'TerminateJobObject(own Job only)')
            while time.monotonic()-begin<budget:
                acct=self.accounting();remaining=self.pids()
                handles_done=all(not live(h) for h,_ in self.observed.values())
                zero=zero+1 if acct['active']==0 and not remaining and handles_done else 0
                if zero>=2:confirmed=True;break
                time.sleep(.025)
        except BaseException as e:error=repr(e)
        return {'confirmed':confirmed,'remaining_pids':remaining,'seconds':round(time.monotonic()-begin,5),'accounting':acct,'error':error,'proof':'two empty Job-accounting/PID observations and all retained identity handles signaled'}
    def records(self):
        out=[]
        for h,ident in self.observed.values():
            out.append(dict(ident,exited=not live(h)))
        return out
    def close(self):
        errors=[]
        all_handles={h for h,_ in self.observed.values()}
        if self.pi.process:all_handles.add(self.pi.process)
        if self.pi.thread:all_handles.add(self.pi.thread)
        # Closing our sole non-inheritable Job handle is final fail-safe.
        if self.job:all_handles.add(self.job)
        for h in all_handles:
            try:close(h)
            except BaseException as e:errors.append(repr(e))
        for fd in self.read_fds:
            try:os.close(fd)
            except OSError:pass
        self.job=None;self.pi=PROCESS();self.observed.clear();self.read_fds=[]
        return errors

