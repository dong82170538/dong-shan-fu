# 验证入口与证据边界

本仓库只包含自写测试、合成fixtures、说明与锁文件。测试运行后才在artifacts/产生JSON、日志和SQLite备份；这些输出及内部检查截图不进入源码提交。

在已提供Python3.12+、Node24.19+、Playwright1.62.0与Chromium的Linux环境运行python3 scripts/verify_stage3.py；它不安装依赖，所有子命令有期限和自有后代清理。Windows Job机制尚未集成，整体和Node测试入口会在被测命令启动前BLOCKED。

入口执行完整Python套件、Node协议/真实Python生命周期套件、基础网页与控制网页两条真实流程。执行前后对代码清单取SHA256，并绑定四份新报告；旧报告不能冒充新执行。TS逐项初始NOT_RUN，只有关联测试实际成功才为PASS；原生TS16继续BLOCKED。

持久化验收使用独立进程PID和同一磁盘SQLite，真实强杀/活跃Engine锁竞争、已提交结果保留和显式恢复；取消后迟到结果、幂等冲突、1001条部分保存失败、明确币种/两种价格profile、日志标记和调度均有实际断言。进程清理验收观察root/child/leaf的PID消失、心跳停止、文件锁重取以及后续正常命令。

Python测试审计钩子覆盖外部connect/sendto/sendmsg和所列DNS事件；浏览器另有限制页面请求的回环路由。这些不是系统防火墙，不宣称是不可绕过的网络沙箱。进程清理只操作明确启动并通过内核归属确认的子孙，使用pidfd避免旧PID号误指新进程，不按名称或端口杀进程。

历史算术/状态特征由独立纯函数检验，未执行原厂代码。路由库存是惰性事实数据，未取得的原ERP模块不冒称实现。Electron/Windows整套/MV3宿主/真实商家/发布均未验收。Windows R2本地组件的生产者通过记录不能当作本仓库已接入或整套通过。
