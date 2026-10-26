"""Platform dispatch; original Linux backend retained byte-for-byte."""
import importlib
import hashlib
from pathlib import Path
import subprocess
import sys

def _import(name):
    return importlib.import_module((__package__+'.' if __package__ else '')+name)

OwnershipUnsupported=_import('owned_process_linux').OwnershipUnsupported

def _backend():
    if sys.platform=='linux':return _import('owned_process_linux')
    if sys.platform=='win32':return _import('owned_process_windows')
    raise OwnershipUnsupported('SUPPORTED_OWNERSHIP_BACKEND_REQUIRED')

def support():
    if sys.platform=='linux':return _backend().support()
    if sys.platform=='win32':
        if not _import('_winjob').abi_audit()['pass']:
            raise OwnershipUnsupported('WINDOWS_X64_JOB_API_REQUIRED')
        return
    raise OwnershipUnsupported('SUPPORTED_OWNERSHIP_BACKEND_REQUIRED')

def run_owned(argv,*,cwd,env=None,timeout=60,stop_event=None):
    support()
    backend=_backend()
    def windows_fingerprint():
        files=[Path(backend.__file__).resolve(),Path(_import('_winjob').__file__).resolve()]
        return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    before=windows_fingerprint() if sys.platform=='win32' else None
    result=backend.run_owned(argv,cwd=cwd,env=env,timeout=timeout,stop_event=stop_event)
    if sys.platform=='win32':
        after=windows_fingerprint();result['backend']='windows_r2_integrated'
        result['runtime_backend_binding']={'before':before,'after':after,'unchanged':before==after}
        if before!=after:result.update(status='FAIL',reason='runtime_backend_source_changed')
    return result

def __getattr__(name):return getattr(_backend(),name)
def main():return _backend().main()
if __name__=='__main__':raise SystemExit(main())
