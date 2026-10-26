# 桌面与测试运行条件

Node24.19+用于开发；Electron精确依赖44.9.3及传递锁文件已提供，但未安装/执行。Python3.12+核心无第三方运行依赖。不得借用其他软件内部Electron或在没有安装/运行授权时下载运行时。

主进程以参数数组、shell:false启动自有Python服务，仅绑定127.0.0.1并默认选择空闲端口。数据目录固定项目内data-desktop；不杀端口占用者。就绪需启动行、实际health和本次实例标记一致；异常退出为failed，显式重启后继续读取同一SQLite。

任务读取/导入/提交使用受限本地Web接口；桌面watch/control/export采用窄IPC。桌面错误不回退HTTP控制。固定7个IPC有字段、sender/mainFrame、任务关联及截止时间校验；刷新/导航/关闭解绑监听并阻挡迟到响应。导出只写管理目录，渲染器不能传路径。

sandbox/contextIsolation/webSecurity开启，renderer Node、外部导航/窗口、webview、权限与下载关闭。未通过实际Electron验收，不能把Node的明确test doubles或Chromium网页通过当作原生隔离证据。

测试入口node desktop/scripts/test.cjs由标准库Linux监督器管理期限和后代归属；内外超时、runner异常、父包装器死亡均有真实heartbeat/文件锁回归。系统不支持subreaper/pidfd时BLOCKED，不回退只杀直接runner。最外监督器自身被SIGKILL或主机崩溃不在清理保证内。Windows R2模块尚未接收或集成，Windows整套仍BLOCKED。

正式原生验收需先批准、核验并放置本项目的44.9.3运行时，再显式授权启动。启动器只接受项目node_modules/electron/dist或approved-runtime，不使用npx自动下载。官方Windows资产精确大小/SHA256尚需核验。
