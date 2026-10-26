# 东山 · 自有离线电商工作台

独立编写的Python/SQLite核心、本地网页、Electron壳源码和MV3 fixture预览源码。平台边界为OZON/WB，所有输入、费用与适配器明确标注test_fixture；真实店铺连接、采集和发布关闭。这里没有原软件客户端、安装包、品牌资源、真实凭据或业务数据，也不表示全量产品已经复刻。

## 运行

Python3.12+，生产核心只需标准库与SQLite；每日时区计划需要可用的IANA ZoneInfo数据。

```sh
python3 scripts/seed_demo.py
python3 -m commerce_core serve --port 8765 --concurrency 2
```

在当前运行环境打开http://127.0.0.1:8765。默认数据库data/commerce.sqlite3，退出重启继续读取。可导入明确的合成fixture、去重/归一化、解释规则、币种定价、提交任务、暂停/取消/失败重试和JSON/CSV导出；已发布始终为0。桌面与扩展划线价profile分开；内置流水线为3条去重成2条，筛出p1，mock费用CNY100经汇率12得到RUB1080.00/1920.00。

## 验证与范围

```sh
python3 scripts/verify_stage3.py
```

The historical verifier name is retained. On Windows, python scripts/verify_stage3.py creates a fresh artifacts/windows/<UTC-id> directory, owns each stage through an explicit acceptance Job guard, and binds results to unchanged runtime/test source bytes. Linux retains its original subreaper/pidfd backend. Existing Python 3.12+, Node 24.19+, Playwright 1.62.0 and Chromium are prerequisites; this command does not install them. Set COMMERCE_CHROMIUM to the existing browser path. See docs/WINDOWS_INTEGRATION.md.

After independent review fixes, the recorded Windows integration run completed 76 Python PASS / 8 Linux-only skips (84 discovered), 32 Node PASS, 40 deterministic boundary PASS, and two real Chrome Web UI flows PASS. Forced/nonzero stop outcomes now reject and stay failed; cp936-parent CRLF decoding is explicitly tested. The 20 TS scenarios remain 19 PASS / 1 BLOCKED (TS16 native Electron IPC). Historical Linux evidence remains separate; Linux kernel regression was not executed on Windows.

Electron is locked to 44.9.3. Its project runtime is absent and was not installed or executed. Native Electron IPC, packaged Windows application, real MV3 host and merchant integration remain unverified. The integrated Windows Job/core/lifecycle/Web subset passed locally and passed limited independent review; this is not whole-product or stage4 acceptance.

详见docs/ARCHITECTURE.md、docs/DECISIONS.md、docs/DESKTOP.md与docs/VALIDATION.md。docs/analysis/erp-route-inventory.json仅为自写的惰性路由事实库存，不是原模块实现；它被现有只读能力端点和测试使用。表达式文本不会执行。

数据、证据、截图、归档、依赖目录与运行时均不属于源码提交。本仓库保存自写源码；运行数据和验证输出保持本地，不自动部署或发布。
