# 验证入口与证据边界

本仓库只包含自写测试、合成fixtures、说明与锁文件。测试运行后才在artifacts/产生JSON、日志和SQLite备份；这些输出及内部检查截图不进入源码提交。

Prerequisites: existing Python 3.12+, Node 24.19+, Playwright 1.62.0, Chromium and IANA timezone data. Run python scripts/verify_stage3.py; it installs nothing. Windows dispatches to the integrated R2 Job backend; Linux retains its original backend. The outer Windows acceptance controller uses its own explicit pre-resume Job guard. Ordinary nested R2 wrappers remain rejected.

The verifier runs Python core/lifecycle, Node protocol and actual Python lifecycle, two real browser flows, plus 40 deterministic Windows boundary cases on Windows. Reports bind exact runtime/test bytes before and after. Old reports do not count as fresh execution. TS states require their associated executed tests; native TS16 stays BLOCKED.

持久化验收使用独立进程PID和同一磁盘SQLite，真实强杀/活跃Engine锁竞争、已提交结果保留和显式恢复；取消后迟到结果、幂等冲突、1001条部分保存失败、明确币种/两种价格profile、日志标记和调度均有实际断言。进程清理验收观察root/child/leaf的PID消失、心跳停止、文件锁重取以及后续正常命令。

The Python audit hook covers external connect/sendto/sendmsg and listed DNS events; browser page routing permits loopback and disables background networking. These are test controls, not OS firewall isolation. Linux uses subreaper/pidfd; Windows uses retained process/Job handles and creation identity, atomic pre-resume assignment, two empty Job observations and signaled retained identity handles. Cleanup acts only on proven owned processes, never names or ports.

Historical observed-parity cases are independent characterization fixtures from parent static evidence; no original implementation is executed. Final Windows verification passed the integrated core/lifecycle/owned cleanup/Web subset only. Native Electron, packaging, real MV3, merchant operations and Linux kernel regression remain outside this run. See WINDOWS_INTEGRATION.md and the exact manifest-bound artifacts; independent R2 success alone is not product acceptance.
