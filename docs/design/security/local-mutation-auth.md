# 本地变更端点的 CSRF 与 Origin 校验

> **文档性质：current implementation record。** 本页记录 browser-facing 本地变更端点的共享防跨站请求合同。它降低恶意网页调用 localhost 的风险，但不是用户身份认证，也不能保护已取得本机执行权限的进程。

## 威胁边界

浏览器可以从任意站点向 localhost 发请求，因此“只监听本地地址”并不足够。浏览器变更端点需要同时验证应用签发的 CSRF token 与请求来源语义。主服务的非浏览器本地调用方必须显式取得并携带 token；插件生命周期与插件包导入路由另有一个为既有本地原生调用保留的兼容路径，见下文。

主服务共享实现位于 `main_routers/system_router/_shared.py`，包括允许的本地 Origin、token 提取、常量时间比较和统一错误响应。前端调用方应从已有配置/状态端点取得 token，并通过 `X-CSRF-Token` 发送；兼容 body token 只按当前 helper 支持范围使用。

插件服务器保护七个插件生命周期路由、五个插件包导入路由和 `GET /security/csrf-token` 引导路由。插件包导入路由是 `POST /plugin-cli/upload`、`/plugin-cli/upload-and-install`、`/plugin-cli/install`，以及 legacy alias `/plugin-cli/upload-and-unpack`、`/plugin-cli/unpack`；它们会写入或安装可执行插件代码。带 `Origin` 的浏览器变更请求必须同时通过可信来源和 token 校验。桌面与 NAS/Docker 都是支持场景；正常页面自动获取并携带 token，普通 NAS 用户无需新增来源白名单或手动配置 token。

为兼容现有本地原生脚本，暂时保留无 `Origin` 的 loopback 路径：客户端和 Host 必须是 loopback，且不能携带 Referer 或 Fetch Metadata；没有 token 时仍可调用，但显式提供空值或错误 token 必须拒绝。该例外只用于本地原生调用，不适用于远程脚本，也不是对恶意本地进程的身份认证。强制所有原生调用带 token 需要另行评估调用方迁移。

## 稳定合同

- 浏览器变更请求缺少或提供错误 token 时拒绝；插件本地原生兼容路径仅允许省略 token，不允许错误 token；
- 浏览器提供 Origin 时必须符合对应端点的来源规则；
- NAS/Docker 非 loopback 页面来源优先匹配外部完整地址，并允许仅 hostname 匹配的兼容兜底（不比较协议与端口）；桌面跨端口来源只允许明确的 loopback 前端来源，不能接受任意 Origin；
- 校验失败保留统一 `csrf_validation_failed`，响应 JSON 的 `detail.csrf_failure` 与 `X-CSRF-Failure: token` 表示可刷新重试的 token 失败，`origin` 表示来源失败；响应和日志不回显 token；
- GET 读取端点也不能返回超出调用方需要的敏感数据；
- CORS、CSRF 和身份认证是不同层，不能互相替代。

## NAS/Docker 与代理边界

官方 Docker 的 HTTP 和 HTTPS Nginx 配置都将 `/security/csrf-token` 转发到插件服务，保留外部 `Host`（包括映射端口），并覆盖 `X-Forwarded-Proto`。插件服务的嵌入式与独立 Uvicorn 入口共用代理边界，仅信任 `127.0.0.1`、`::1` 代理传来的客户端地址和协议信息。路由守卫使用处理后的请求协议与原始 Host 比较来源，不直接读取或信任任意 `X-Forwarded-*`。浏览器来源校验不要求客户端地址是 loopback，因此通过 Nginx 的真实 NAS 客户端可以正常操作。

`HostOriginGuardMiddleware` 在路由校验之前仍负责防 DNS rebinding：IP 地址与 localhost 可用，自定义域名沿用 `NEKO_TRUSTED_HOSTS` 显式配置。官方 IP 访问、HTTP/HTTPS 与端口映射无需新增用户配置。外层 NAS 代理终结 HTTPS 后通过 HTTP 转发到容器时，内层 Nginx 仍覆盖协议头；非 loopback Host 的 hostname 兜底使此场景无需新增配置。自建代理仍须保留 Host，自定义域名仍沿用既有主机信任配置；非本机代理不自动获得转发头信任。

这是明确接受的信任取舍：同一 NAS hostname 的其他协议或端口也通过来源校验，可能读取共享 token；不能把此实现描述为隔离同机其他应用的严格 origin 防护。不同 hostname 仍拒绝，loopback 桌面保留完整来源规则，5173 仍需显式允许。

token 引导允许可信 Origin、可信完整 Referer（忽略页面路径），以及无 Origin/Referer 但带 `Sec-Fetch-Site: same-origin` 的请求。仅 `same-site` 不足以授权，因为同一 NAS 的不同端口可能属于其他应用。没有任何浏览器来源信息的请求只允许本机原生调用。上述生命周期与插件包导入路由以外的 mutation 不依赖 token bootstrap，仍需分别评估安全性。

现有 `require_admin` 是兼容占位，不提供身份认证；multipart/form-data 请求可能无需 CORS 预检即可产生副作用，缺少 `Content-Type` 的 JSON 请求也会被 FastAPI 按 JSON 解析，因此不能依靠 CORS 预检或拦截响应来保护变更路由。插件包导入路由必须连同两步链路一起保护：只保护 `upload-and-install` 时，`/plugin-cli/upload` 加 `/plugin-cli/install` 仍可完成安装；legacy alias 以普通函数调用目标处理器，必须单独注册守卫。

FastAPI 在解析 JSON/multipart 请求体之后才执行路由依赖，因此带请求体的插件包导入路由使用 `PluginMutationGuardedRoute`：在读取请求体之前调用同一个 `require_plugin_mutation_access`，失败响应、token 规则和本机原生兼容路径与生命周期路由一致，被拒绝的上传不会落盘或进入临时文件。

本次仍未保护全部插件变更接口，例如 `DELETE /plugin-cli/upload`、`/plugin-cli/build`（及 `/pack`）、`POST /runs`、配置写入和插件 UI 安装动作。统一 mutation 校验需要一起审计网页、原生 CLI、上传与代理调用，并同步前端 token 发送范围，保留 NAS 零新增配置合同。

这套保护防止跨站网页借用浏览器执行插件操作，不是远程访问登录认证。公网访问的身份认证、网络隔离、防火墙仍属于部署层责任；这些措施不能替代应用的 CSRF 校验。安全修复不得通过禁用官方 NAS/Docker 访问来规避兼容问题。

## 开发来源与共享 token

插件引导接口复用实例级 `AUTOSTART_CSRF_TOKEN`，因此允许读取它的来源也可能影响主服务认同一 token 的接口。生产默认不信任通用 Vite 端口 `5173`。开发插件前端时，启动后端前显式配置 `NEKO_PLUGIN_MUTATION_ALLOWED_ORIGINS=http://localhost:5173`（如实际使用 `127.0.0.1`，配置对应完整来源；多个来源以逗号分隔）。这仅对开发者有配置要求，官方 NAS 用户不需要设置此变量。

`AUTOSTART_ALLOWED_ORIGINS` 中的显式配置也属于共享 token 的信任合同。显式配置的完整来源对 loopback、LAN IP 和代理改写后的 Host 均生效，因此 Vite 代理指向 LAN 后端时仍可使用开发来源 opt-in；自动生成的 loopback 端口默认值仍只对 loopback Host 生效。不要把无关应用加入允许列表；配置只识别网页来源，不能验证该端口运行的是哪个项目。本次没有引入独立插件 token，也没有改变全局 CORS。

`NEKO_TRUSTED_ORIGINS` 是 HostOriginGuard 的 WebSocket/特定 HTTP 来源信任配置，不自动授予读取共享 token 或执行插件生命周期变更的权限。本次保留独立的插件 token 信任合同；官方同源 NAS 页面不需要配置任一来源列表。若将来统一来源配置，需同时明确其对 token 读取、WebSocket 和 CORS 的权限范围，不能仅合并列表就宣称跨来源调用可用。

## 前端调用模式

前端 `request.ts` 中的受保护路由匹配必须与后端守卫范围保持一致；后端新增受保护路由而前端漏配时，页面请求会以不重试的 token 失败告终。插件短操作与插件包上传/安装仅在收到 token 失败标记时刷新一次并重试（multipart 重试复用同一 FormData）；来源拒绝不刷新重试。token 引导使用独立的 API_TIMEOUT（30 秒），失败时保持调用方的静默配置；引导超时使用通用请求超时提示，不套用“插件操作超时”，因为原变更请求尚未发送。生命周期请求自身的超时提示配置保持有效。心跳或长跑任务遇到校验失败必须停止退避，不能每秒无限重试。fire-and-forget 请求仍要构造完整 headers，并处理页面卸载时的失败语义。

命令行调试应使用项目环境读取 JSON 并显式传 header，例如先保存响应再用：

```bash
uv run python -c "import json,sys; print(json.load(sys.stdin)['autostart_csrf_token'])"
```

不要把真实 token 写入脚本、文档、日志或 shell history。

## 新端点接入

1. 确认它会改变本地状态；
2. 在处理 payload 前调用共享守卫；带请求体的插件服务器路由使用 `PluginMutationGuardedRoute`，不要只用路由依赖（依赖在请求体解析之后执行），legacy alias 也要单独注册；
3. 前端统一注入 token；
4. 测试合法请求、缺 token、错 token、恶意 Origin 和允许的本地 Origin；
5. 确认失败不会先执行部分副作用。

## 验证

```bash
uv run pytest tests/unit/test_uncovered_endpoints_csrf.py tests/unit/test_activity_signal_router.py tests/unit/test_card_assist_csrf.py -q
uv run pytest plugin/tests/unit/server/test_plugin_mutation_auth.py plugin/tests/unit/server/test_plugin_cli_route.py plugin/tests/unit/server/test_development_routes.py plugin/tests/unit/server/test_docker_plugin_proxy.py -q
```
