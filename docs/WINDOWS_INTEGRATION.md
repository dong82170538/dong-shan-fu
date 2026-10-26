# Windows integration and test limits

The platform runner dispatches to the Windows Job backend or the original Linux subreaper/pidfd backend. Linux backend bytes are unchanged. Windows uses a dedicated supervisor, non-inheritable Job handles, atomic suspended-process assignment before any command instruction, and retained handles plus creation identities. No breakaway, elevated/debug privileges, process-name or port cleanup is used. Ordinary nested wrappers are rejected. The explicit outer acceptance controller owns its own Job and never strips inherited wrapper markers.

PASS requires exit0, no live owned descendants, confirmed cleanup and completion strictly before deadline without cancellation. Timeout/nonzero/uncertain cleanup remain failures. Deadline substitutions are deterministic boundary tests, not a real scheduling-race reproduction. Outermost supervisor/host/kernel crash cases are not universally guaranteed.

The Windows service uses an owner-only stdin shutdown pipe accepting LF/CRLF/EOF; other platforms retain signal handling. Forced termination rejects LOCAL_SERVICE_STOP_FORCED, nonzero exit rejects LOCAL_SERVICE_STOP_NONZERO, and missing close confirmation rejects LOCAL_SERVICE_STOP_UNCONFIRMED. stopResult distinguishes failed operations from confirmed cleanup. Repeated stop/start/restart on a failed manager cannot clear the failure. A new manager can run after confirmed cleanup. An already-exited child before any new stop is reported ALREADY_EXITED, not a new graceful-operation PASS. Shell restart and the quit gate preserve stop failure semantics.

The UTF-8 service startup stream is explicitly decoded as UTF-8 even under a cp936 parent. Reader errors retain their cause rather than becoming empty stdout. Real subprocess tests cover CRLF, non-UTF8 parents, forced/nonzero exits, repeat operations, kernel locks, heartbeat stability, ownership isolation and subsequent healthy commands.

## Reproduction

Use existing Python 3.12+, Node 24.19+, Playwright 1.62.0, a compatible Chrome/Chromium executable and IANA timezone data. Set COMMERCE_CHROMIUM to that executable if needed. The verifier does not download dependencies or alter system configuration.

```sh
python scripts/verify_stage3.py
```

Windows runs use fresh artifacts/windows/<run-id> directories with task temporary files, isolated browser profiles, caches and downloads. Existing logged-in browsers are not reused. Host/browser internals may still use system directories. Only synthetic fixtures and loopback services are tested; real connections and publishing remain disabled.

## Recorded validation scope

Tested runtime/test/config manifest SHA256: b7827c5d8d003a3fcc8bb804855f7c2264448b9e74b2ab739c45462210466dfc. Runtime bytes were unchanged before and after the complete Windows run; publication changes only portable prose outside that manifest.

- Python: 76 PASS, 8 Linux-only skips, 0 failures/errors (84 discovered).
- Node: 32 PASS, including actual Python/SQLite lifecycle plus protocol/Electron event doubles; nativePassed=false.
- Deterministic Windows deadline checks: 40 PASS.
- Actual Chrome Web UI: two flows PASS, covering import, duplicate submit, export, cancellation/retry, same-database restart and delayed-response fencing; published0.
- All five command Jobs confirmed cleanup, with no remaining owned PIDs.

Independent review accepted the bounded Windows integration and stop-failure/decoder fixes. Its targeted rerun covered 13 Node tests, two real CRLF tests, the original forced-stop counterexample, sticky errors and a real-byte encoding comparison. This is not whole-product or stage4 acceptance. Local evidence stays outside source control.

Electron is locked to 44.9.3 but its project runtime was absent and was not installed/executed. Native IPC, packaged desktop, real MV3 loading, Linux kernel rerun, merchant integration, original ERP parity and publication remain BLOCKED or NOT_RUN. Linux compatibility must be rerun separately on Linux. Chrome Web UI and event doubles do not substitute for native Electron/MV3 acceptance.
