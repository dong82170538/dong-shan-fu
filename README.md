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

保留历史入口名，默认输出artifacts/stage4。完整网页验证需环境已有Playwright1.62.0与Chromium；本命令不下载依赖。已有浏览器可用COMMERCE_CHROMIUM指定。Linux进程监督器负责测试命令超时/失败/退出后的自有后代清理。当前Windows等效Job机制尚未集成，Windows测试入口在启动被测命令前BLOCKED；不能用Linux通过记录替代Windows验收。

stage4基线在云端实际通过73项Python（包含8项进程清理回归）、28项Node协议/生命周期测试和两条Chromium网页流程。20个TS中19 PASS、TS16原生IPC BLOCKED。此精简候选树的验证结果另行记录，不把不同源码清单视为同一指纹。

Electron精确锁定44.9.3，项目内未安装运行时；桌面启动器默认BLOCKED，不自动下载，也不使用其他应用的Electron。Windows R2进程组件尚未接收、未集成。原生Electron、Windows整套、MV3真实宿主及真实商家集成未验收。

详见docs/ARCHITECTURE.md、docs/DECISIONS.md、docs/DESKTOP.md与docs/VALIDATION.md。docs/analysis/erp-route-inventory.json仅为自写的惰性路由事实库存，不是原模块实现；它被现有只读能力端点和测试使用。表达式文本不会执行。

数据、证据、截图、归档、依赖目录与运行时均不属于源码提交。本仓库保存自写源码；运行数据和验证输出保持本地，不自动部署或发布。
