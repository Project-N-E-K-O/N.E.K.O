# 本地变更端点的 CSRF 与 Origin 校验

> **文档性质：current implementation record。** 本页记录 browser-facing 本地变更端点的共享防跨站请求合同。它降低恶意网页调用 localhost 的风险，但不是用户身份认证，也不能保护已取得本机执行权限的进程。

## 威胁边界

浏览器可以从任意站点向 localhost 发请求，因此“只监听本地地址”并不足够。变更端点需要同时验证应用签发的 CSRF token 与请求来源语义。非浏览器本地调用方也必须显式取得并携带 token。

共享实现位于 `main_routers/system_router/_shared.py`，包括允许的本地 Origin、token 提取、常量时间比较和统一错误响应。前端调用方应从已有配置/状态端点取得 token，并通过 `X-CSRF-Token` 发送；兼容 body token 只按当前 helper 支持范围使用。

## 稳定合同

- 变更请求缺少或提供错误 token 时拒绝；
- 浏览器提供 Origin 时必须符合允许的本地 host/port 规则；
- Electron/开发端口差异只允许 helper 中明确的 loopback 兼容，不能接受任意远端 Origin；
- 校验失败使用统一 `csrf_validation_failed`，响应和日志不回显 token；
- GET 读取端点也不能返回超出调用方需要的敏感数据；
- CORS、CSRF 和身份认证是不同层，不能互相替代。

## 前端调用模式

短操作可以在 token 过期/服务重启后刷新一次并重试。心跳或长跑任务遇到校验失败必须停止退避，不能每秒无限重试。fire-and-forget 请求仍要构造完整 headers，并处理页面卸载时的失败语义。

命令行调试应使用项目环境读取 JSON 并显式传 header，例如先保存响应再用：

```bash
uv run python -c "import json,sys; print(json.load(sys.stdin)['autostart_csrf_token'])"
```

不要把真实 token 写入脚本、文档、日志或 shell history。

## 新端点接入

1. 确认它会改变本地状态；
2. 在处理 payload 前调用共享守卫；
3. 前端统一注入 token；
4. 测试合法请求、缺 token、错 token、恶意 Origin 和允许的本地 Origin；
5. 确认失败不会先执行部分副作用。

## 验证

```bash
uv run pytest tests/unit/test_uncovered_endpoints_csrf.py tests/unit/test_activity_signal_router.py tests/unit/test_card_assist_csrf.py -q
```
