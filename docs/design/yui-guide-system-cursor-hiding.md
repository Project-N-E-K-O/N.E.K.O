# Yui 教程系统鼠标隐藏

> **文档性质：external-repository integration design。** 本仓库只包含教程、Ghost Cursor 和发往桌面宿主的 relay 生产方；真正的系统鼠标隐藏服务在 N.E.K.O-PC。没有检查外部仓库和目标平台前，不得声称该能力已端到端验证。

## 本仓库边界

教程运行时位于 `static/tutorial/`：director/visual 模块可以显示 Ghost Cursor，并通过当前跨页面/宿主桥发送教程状态。Ghost Cursor 是页面元素，不等于操作系统鼠标已经隐藏。

本仓库负责：

- 在需要接管演示时请求隐藏，并带 session/reason；
- 在结束、跳过、异常、页面隐藏和 teardown 时请求恢复；
- 防止旧教程 session 的延迟消息影响新 session；
- Web 环境没有桌面 bridge 时安全 no-op；
- reduced motion/无 Ghost Cursor 时仍能恢复真实鼠标。

## 外部宿主要求

N.E.K.O-PC 需要在 preload/main/平台服务中消费 relay，按窗口和 session 引用计数或持有 lease，并在窗口销毁、renderer 崩溃、超时和应用退出时强制恢复。Windows、macOS、Linux 的系统 cursor API 和权限不同，必须分别测试。

这些是集成要求，不是本仓库当前源码的证明。

## 安全不变量

- 隐藏请求必须有对应恢复路径；
- 最终兜底超时后恢复；
- 用户离开教程或应用失焦时优先恢复；
- 不因重复 hide 导致需要多次无法配平的 show；
- renderer 崩溃不能让系统鼠标永久隐藏；
- 普通网页模式绝不尝试调用不存在的原生 API。

## 验证

本仓库验证教程 relay、session 和 teardown；外部仓库验证 preload/main、崩溃恢复与各平台 API。端到端验收必须包含跳过、刷新、窗口关闭、renderer crash、多显示器和应用退出。
