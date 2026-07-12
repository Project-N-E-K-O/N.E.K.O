# 本地变更端点的 CSRF + Origin 校验

> 跟进 [issue #1479](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1479) / [PR #1477](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1477) / [PR #1530](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1530) / [PR #1532](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1532)。本文件覆盖 N.E.K.O. 后端所有 browser-facing 的「本地变更端点」（local mutation endpoint）所共享的 CSRF/Origin 校验合同：威胁模型边界、规则矩阵、Token 流转、前端调用约定、迁移指引。

> **状态说明**：issue #1479 的三个 PR 均已合并，本文件描述的即 main 分支上的当前实现状态：
> - PR #1477（activity_signal 端点 + 临时 Origin-only gate）已合并
> - PR #1530（Step 1：7 个端点收编 + 前端 token 注入 + `tests/unit/test_uncovered_endpoints_csrf.py` canary）已合并
> - PR #1532（Step 2：activity_signal 从临时 Origin-only gate 收编进统一守卫 + 前端 stop-the-heartbeat 退避）已合并
> - 本文件是 issue #1479 Step 3，随 PR #1533 落地。

## 1. 背景

N.E.K.O. 既能跑在 Electron 桌面壳（与渲染器同源），也支持「后端跑在远端／前端跑在用户浏览器」的部署形态。后端有一批「本地变更类」HTTP 端点（POST，且会修改服务端状态或触发副作用）暴露给浏览器/渲染器：

- 主页空闲心跳 / 引导确认 / 自启动决策
- 截图（请求级 + 交互式）
- 主动搭话 / 字幕翻译 / 情绪分析
- Steam 成就解锁 / 时长上报
- 通知 ACK / 音乐播放完成回执
- 跨平台 OS 活动信号（`/api/activity_signal`，PR #1477 引入）

历史上这些端点没有统一的访问控制：任何能访问 `http://localhost:48911` 的同机进程都能 POST，包括运行在用户浏览器里的 *跨站攻击页面*（drive-by CSRF）。

本文件描述的合同就是「在不破坏现有同源 Electron 渲染器与本地脚本调用体验的前提下，为本地变更类端点建立统一的请求证明与跨站拦截」。

## 2. 威胁模型

### 2.1 在范围（in scope）

- **浏览器中介的跨站请求**：用户在另一个标签页打开 `https://attacker.com`，该页面 JS 用 `fetch("http://127.0.0.1:48911/api/...", { method: "POST", body: ... })` 试图触发本地端点的副作用。这是 N.E.K.O. 桌面壳的典型威胁场景：用户邀请你打开一个看起来正常的网页，攻击者就能远程操纵你的桌宠。
- **opaque-origin 浏览器上下文**：sandboxed `<iframe>`、`file://` 页面、某些扩展上下文都会发送 `Origin: null`，必须显式拒绝。
- **同源但攻击者注入**：例如同机其它 localhost 服务（开发服务器、其它 app 起的 web 接口）能读到 token 之前的请求。CSRF token 即便同源也是必填，关掉这类 same-host-different-port-attack。

### 2.2 不在范围（out of scope）

- **本机 root / Electron 主进程 / 同机原生进程**：能读取 token 文件、内存、配置的攻击者已经赢了；CSRF 不解决这一层。
- **客户端环境完整性**：用户安装了恶意浏览器扩展、关掉了浏览器 same-origin policy、用 `--disable-web-security` 启动 Chrome —— 不在合同覆盖范围。
- **后端 → 后端 / 跨服务请求**：例如 OpenFang 工作进程回调后端，走的是 IPC / 内部约定，不通过这套合同。
- **网络层身份认证**：用户身份验证（"这是不是 LyaQanYi 本人"）不是本合同的目标。

### 2.3 CSRF ≠ 鉴权（这是设计契约，不是 bug）

> 这是 [wehos 在 #1479 评论](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1479) 第 2 点指出的关键边界，必须在每次复用本合同的地方反复强调。

`_validate_local_mutation_request` **只挡得住「浏览器中介的跨站」**：
- 它依赖浏览器在 cross-origin POST 时如实发送 `Origin` header（这是浏览器的 same-origin policy，不是 N.E.K.O. 的控制点）
- 它依赖恶意脚本拿不到合法 `X-CSRF-Token`（这成立的前提是 token 没有泄露给恶意 origin）

它**挡不住**：
- 任何能读到 token *并能构造同源 Origin* 的同机攻击者：例如 Electron 主进程通过 IPC 拿到 token 后用 `net.request` 加 `Origin: http://localhost:<port>` 重放，或本机另一个进程读到 page_config 缓存后构造完整的 Origin + token 请求——守卫会放行，因为它就是一个"看起来像合法浏览器请求"
- 注意：**单纯省略 Origin 的本地调用（裸 curl / Python `requests` 默认行为）会被规则矩阵第 7 行直接 403**，不是这一档；这一档是"攻击者主动把 Origin 也带上"的更高水位威胁

换句话说：「本地变更端点」**没有被这套合同变成「已鉴权」**——只是关掉了「浏览器中介的 drive-by CSRF」这一个具体威胁面。任何依赖端点防御能力的高敏感功能仍然要在端点内部自己做更具体的检查，例如：
- `screenshot` / `screenshot/interactive` 走 `_is_loopback_request` 拒绝非环回请求（`main_routers/system_router/screenshot.py:130`）
- `proactive_chat` 用 `mgr.state.can_start_proactive(...)` / `try_start_proactive(...)` 做并发 / 状态机门控（`main_routers/system_router/proactive_chat_flow.py:459` / `498`），并发拒绝时返回 409
- `activity_signal` 用 per-lanlan 5s 限流 + tracker 校验做反垃圾

---

## 3. 规则矩阵

`_validate_local_mutation_request` 检查两件事，**都必须通过**才放行：

1. **Origin 同源**：请求的 `Origin`（或 fallback 到 `Referer`）规范化后必须在 `_get_allowed_local_origins(request)` 集合里
2. **CSRF token 匹配**：请求必须带 `X-CSRF-Token` header（或 body 字段 `_csrf_token`），值与 `AUTOSTART_CSRF_TOKEN` 做 constant-time compare 后相等

| 场景 | `Origin` | `X-CSRF-Token` | 结果 | 说明 |
|---|---|---|---|---|
| 同源 + 合法 token | `http://127.0.0.1:48911` | 正确 | **200** | 正常浏览器/渲染器流量 |
| 同源 + 缺 token | `http://127.0.0.1:48911` | （无） | **403 csrf_validation_failed** | 同机攻击者拿不到 token |
| 同源 + 错 token | `http://127.0.0.1:48911` | 任意错误值 | **403** | 防止穷举（constant-time compare） |
| 跨源 | `https://attacker.com` | 任意 | **403** | drive-by CSRF 主目标 |
| 仅有 Referer 跨源 | （无 Origin）| Referer: `https://attacker.com/...` | **403** | Origin 缺失时回退到 Referer |
| `Origin: null` | `"null"` | 任意 | **403** | sandboxed iframe / file:// |
| 无 Origin 无 token | （无 / 无 Referer）| （无） | **403** | curl / Electron 主进程 / native 脚本 |
| 无 Origin 有 token | （无）| 合法 | **403** | 必须 *Origin AND CSRF* 同时通过 |

### 3.1 关键澄清：Electron 同源渲染器走「有 Origin」分支

> 这是 [wehos 在 #1479 评论](https://github.com/Project-N-E-K-O/N.E.K.O/issues/1479) 第 1 点指出的规格修正。

NEKO-PC 的渲染器（[N.E.K.O.-PC 仓库](https://github.com/Project-N-E-K-O/N.E.K.O.-PC) `BrowserWindow.loadURL(...)`）从 `http://localhost:<port>/` 同源加载页面。Chromium / Electron 在渲染器里的 `fetch()` POST 时会带 `Origin` header（通常值就是页面 origin `http://localhost:<port>`；某些上下文如 sandboxed iframe / file:// 会发字面 `"null"`，被规则矩阵第 6 行显式拒绝）。**只要请求带 Origin（非 null）就必须提供 `X-CSRF-Token`**，命中规则矩阵第 1/2/3 行。

也就是说：
- 同源 Electron 渲染器 → 有 Origin → 必须走 token 路径
- 「无 Origin」分支实际只覆盖 *非浏览器* 调用方：curl、Node 脚本、Electron 主进程直接发的 `net.request`，等等（默认不会带 Origin）

NEKO-PC 拿 token 的路径：跟纯浏览器完全一样——同源 fetch `GET /api/config/page_config`（无 CSRF，返回 `autostart_csrf_token` 字段）→ 前端 `static/app/app-prompt-shared.js` 缓存 → `getMutationHeaders()` 注入。**不需要** NEKO-PC 通过 preload/IPC 暴露任何东西。

> 实测验证建议：用 Chromium DevTools Network 或 Electron `webRequest.onBeforeSendHeaders` 观察一个真实 mutation 请求的 header dump，确认 Origin 值跟你的部署 origin 一致。如果观察到打包版有 Origin 缺失/异常的情况，那是 Chromium/Electron 版本特有 bug，比规则矩阵假设更值得 follow up。

### 3.2 非浏览器调用方手动构造请求

curl / Python `requests` / Node `fetch` / CI 脚本 / Raycast workflow 这类自动化场景默认**不带 Origin**，按规则矩阵会被 403。在 N.E.K.O. 实例本机调试或写自动化时，按以下模板构造合法请求：

```bash
# 1. 拿 token（GET，无守卫）
TOKEN=$(curl -s http://127.0.0.1:48911/api/config/page_config \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['autostart_csrf_token'])")

# 2. 用 token 调本地变更端点
curl -X POST http://127.0.0.1:48911/api/<endpoint> \
  -H "Origin: http://127.0.0.1:48911" \
  -H "X-CSRF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{...}'
```

**通用契约层**（`_validate_local_mutation_request`）**没有**给"loopback + 正确 token 但缺 Origin"留 escape hatch。理由：
- 真正的非浏览器场景按上面模板手动加 Origin header 就行，成本几乎为零
- 加 escape hatch 实际上扩大了 trusted callers 的边界——但 trusted callers 的定义本来就是"能读到 token 的本地进程"，而能读到 token 的进程在我们的威胁模型里跟"Electron 主进程 / 本机 root"是一个等级（见 2.2 节），不需要再用 HTTP 路径把这个集合扩大
- 误开 escape hatch 会让威胁模型变模糊：一个能拿到 token 但被 Origin policy 挡掉的攻击进程（例如恶意浏览器扩展通过 DevTools 协议读到 page_config 缓存）会绕过 Origin 这一层防护

**但个别端点可以并且确实定义了自己的 bypass**：例如 `POST /api/screenshot/interactive`（`main_routers/system_router/screenshot.py:181`）在 198 行起先用 `_is_loopback_request` 限制只能从 loopback 访问，再判断「请求是否带 `Origin` 或 `Referer`」——**只在 header 缺失时**跳过 `_validate_local_mutation_request`，专门保留给 curl / 本地脚本 / 测试 这类无浏览器调用方。这种端点级 bypass 不破坏通用契约（因为带 Origin 的浏览器请求仍然走守卫），但用文档化的方式标在 handler 里，写新端点时**不要默认抄这个模式**——如果你的端点不像 screenshot/interactive 那样有明确的"本地脚本调用"业务需求，就坚持走通用契约（无 Origin → 403）。

---

## 4. 配置项

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `NEKO_AUTOSTART_CSRF_TOKEN` | `INSTANCE_ID`（每次启动随机 UUID4 hex） | CSRF token 值。生产环境通常不设——默认值已经足够，且重启即失效。**只接受加 `NEKO_` 前缀的形式**（`config/__init__.py:316` 直接 `os.getenv("NEKO_AUTOSTART_CSRF_TOKEN")`，没有裸名 fallback）|
| `NEKO_AUTOSTART_ALLOWED_ORIGINS` / `AUTOSTART_ALLOWED_ORIGINS` | 自动生成 `http://127.0.0.1:<MAIN_SERVER_PORT>` / `http://localhost:<MAIN_SERVER_PORT>` / `http://[::1]:<MAIN_SERVER_PORT>` | 允许的 Origin 集合（逗号分隔追加）。`request.base_url` 总是自动加入，确保 reverse-proxy 部署不需要手配。**两种键名都支持**（走 `_read_list_env` 的 `NEKO_<NAME>` → `<NAME>` 双键 fallback）|
| `NEKO_INSTANCE_ID` | （由 launcher 注入）| INSTANCE_ID 来源，间接影响默认 CSRF token |

> **关于命名不对称**（`NEKO_AUTOSTART_CSRF_TOKEN` 单键 vs `AUTOSTART_ALLOWED_ORIGINS` 双键）：实现历史差异——CSRF token 在 `config/__init__.py:316` 走 `os.getenv()` 直读，allowed origins 走 `_read_list_env()` helper。统一化（让 token 也走 helper 拿到双键 fallback）不在 issue #1479 范围，没有 known 痛点；如果以后运维真的因为这个命名差异踩坑，再单独 PR。**实务建议：所有 N.E.K.O. 相关 env 都加 `NEKO_` 前缀，这套规则在所有 helper 下都生效**。

### 4.1 故意没引入的配置

issue #1479 原文提议加 `NEKO_REQUIRE_CSRF_FOR_ORIGIN` / `NEKO_REQUIRE_CSRF_FOR_NO_ORIGIN` 两个开关。**没引入**，理由：

- 当前规则 `Origin AND CSRF` 已经把「有 Origin → 必须 CSRF」内化，不需要额外 flag
- 「无 Origin → 拒绝」是 CSRF != 鉴权 的边界自然落地，不是可配置项；如果有人想让 curl 直接访问，应该走「在 N.E.K.O. 进程内部 / 通过 IPC 操作」而不是 HTTP
- 多一个 flag 多一倍的部署矩阵需要测试 / 文档化 / 回滚，得不偿失

如果未来 remote 部署真有需要给特定可信 native caller 放行的场景，再加一个更精确的机制（例如签名头、特定路径白名单），而不是退化已有规则。

---

## 5. Token 流转

```text
[server boot]
  ↓
AUTOSTART_CSRF_TOKEN = env('NEKO_AUTOSTART_CSRF_TOKEN') or INSTANCE_ID
  ↓
[GET /api/config/page_config]  ← 浏览器/渲染器初始加载
  返回 { ..., autostart_csrf_token: "<token>" }
  Cache-Control: no-store
  ↓
[static/app/app-prompt-shared.js: createLocalMutationSecurity]
  - readInitialToken(): 从 window.pageConfigReady Promise 或 fetch /api/config/page_config
  - cachedToken：模块级缓存
  - refreshToken(): 服务端返回 403 csrf_validation_failed 时重新拉
  ↓
[业务调用方]
  const headers = await window.nekoLocalMutationSecurity.getMutationHeaders();
  // → { 'X-CSRF-Token': '<token>' }
  fetch('/api/<endpoint>', { method: 'POST', headers, body: ... });
  ↓
[server: _validate_local_mutation_request(request)]
  - 校验 X-CSRF-Token + Origin
  - 失败 → 403 { ok: false, error_code: "csrf_validation_failed", error: "Request could not be verified" }
  ↓
[业务调用方处理 403]
  - 短期请求：refreshToken() → 重试一次（参考 `tutorial/core/universal-manager.js` 的 `postTutorialPromptReset()` pattern）
  - 长跑心跳：连续 N 次失败后停止（参考 static/app/app-activity-signal.js）
```

### 5.1 Token 安全特性

- **每实例随机**：`INSTANCE_ID = uuid4().hex`，跨重启失效 —— 攻击者不能依赖一次泄露长期复用
- **constant-time compare**：用 `secrets.compare_digest`，防止 timing-based 穷举（虽然 UUID4 hex 已经 36 字符）
- **不持久化**：token 只在内存里，不写文件不进 log（除了 `/api/config/page_config` 直接返回给同源 client）
- **`page_config` 端点本身无 CSRF**：因为是 GET、只读，且 Cache-Control: no-store 防止中间层缓存

### 5.2 错误响应合同

`_validate_local_mutation_request` 返回的 403 body：

```json
{
  "ok": false,
  "error_code": "csrf_validation_failed",
  "error": "Request could not be verified"
}
```

调用方可以通过 `error_defaults` 参数 *合并额外字段*。例如 `/api/activity_signal` 历史上用 `{ success: false, ... }` 契约，所以它的守卫调用是：

```python
validation_error = _validate_local_mutation_request(
    request,
    error_defaults={"success": False},
)
if validation_error is not None:
    _set_no_store_headers(validation_error)
    return validation_error
```

得到的 body 同时带 `success: false` 和 `ok/error_code`，让历史 client 和新 client 都不破。

---

## 6. 已接入端点清单

### 6.1 PR #1477 之前（历史接入）

通过 `_validate_local_mutation_request` 守卫的端点：

- `POST /api/yui-guide/handoff/create`
- `POST /api/yui-guide/handoff/consume`
- `POST /api/tutorial-prompt/heartbeat`
- `POST /api/tutorial-prompt/shown`
- `POST /api/tutorial-prompt/decision`
- `POST /api/tutorial-prompt/reset`
- `POST /api/tutorial-prompt/tutorial-started`
- `POST /api/tutorial-prompt/tutorial-completed`
- `POST /api/autostart-prompt/heartbeat`
- `POST /api/autostart-prompt/shown`
- `POST /api/autostart-prompt/decision`
- `POST /api/screenshot`
- `POST /api/screenshot/interactive`
- `POST /api/mini_game/invite/respond`

### 6.2 PR #1530 — #1479 Step 1（已合并）

PR #1530 把下列 7 个端点接入 `_validate_local_mutation_request`，并改造对应前端调用方注入 `X-CSRF-Token`：`POST /api/pending-notices/ack`、`/api/emotion/analysis`、`/api/steam/set-achievement-status/{name}`、`/api/steam/update-playtime`、`/api/proactive_chat`、`/api/proactive/music_played_through`、`/api/translate`。（#1530 当初还把 dead-code 端点 `/api/personal_dynamics` 一并接入守卫作为防御性默认，但该端点已在 PR #1531「删除从未被调用的 /api/personal_dynamics 端点」中移除，故不在此清单，`test_uncovered_endpoints_csrf.py` 的 canary 也只覆盖上述 7 个。）

### 6.3 PR #1532 — #1479 Step 2（已合并）

PR #1532 把 `POST /api/activity_signal` 从 PR #1477 的临时 Origin-only gate 收编到统一守卫；前端 `static/app/app-activity-signal.js` 改用 `getMutationHeaders()` + 连续 6 次 csrf_validation_failed 后 stop 心跳（避免静默永久降级）。收编后 activity_signal 与其它端点一样返回统一的 `csrf_validation_failed`，不再是 PR #1477 临时 gate 的 `{"success": false, "error": "origin not allowed"}`。

---

## 7. 前端调用模式

### 7.1 短期事件驱动调用（绝大多数）

```js
const headers = { 'Content-Type': 'application/json' };
const sec = window.nekoLocalMutationSecurity;
if (sec && typeof sec.getMutationHeaders === 'function') {
    try { Object.assign(headers, await sec.getMutationHeaders()); } catch (_) { }
}

let response = await fetch('/api/some-endpoint', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
});

// 可选：CSRF-403 retry-once（参考 tutorial/core/universal-manager.js 的 postTutorialPromptReset()）
if (response.status === 403 && sec && typeof sec.refreshToken === 'function') {
    let shouldRetry = false;
    try {
        const body = await response.clone().json();
        shouldRetry = body && body.error_code === 'csrf_validation_failed';
    } catch (_) { }
    if (shouldRetry) {
        await sec.refreshToken();
        response = await fetch('/api/some-endpoint', { /* same args */ });
    }
}
```

### 7.2 fire-and-forget 调用（**PR #1530 引入**）

参考 `static/music_ui.js` 的 `/api/proactive/music_played_through` 调用：用 async IIFE 包一层，让 `getMutationHeaders()` 能 await 但不阻塞外层事件回调（aplayer 的 `'ended'` 回调本身不关心后端回执）。IIFE 内先把 mutation 安全头 `Object.assign` 进初始只含 `Content-Type` 的 header 集，再 `await fetch(...)`；整段 fetch 包在 `try/catch` 里，后端不可达时静默吞掉——播放体验不依赖这个回执，是*业务上确认失败可以丢弃*的场景。

### 7.3 长跑心跳（**PR #1532 引入**）

参考 `static/app/app-activity-signal.js` 的 5s 心跳：

- 加 `isCsrfValidationFailure(resp)` helper（clone body 检查 error_code），**只对真正的 csrf_validation_failed 走 refresh+retry+stop 路径**。其它 403（业务规则、反代、WAF）走 generic 失败计数，不触发停心跳。
- 连续 `MAX_CONSECUTIVE_CSRF_FAILURES`（默认 6 ticks = 30s = 2× tracker TTL）次 csrf_validation_failed 后 `stop()` 心跳并打 console.error 排查指引。
- `start()` early return：sticky `heartbeatStoppedDueToCsrf` 标志阻止 `visibilitychange` 自动重启；恢复需要 page reload（同时 token bootstrap 会重做）。
- 网络异常 / 非 CSRF 响应 / 200 都会清零 csrf streak，避免间隔性失败累计触发误停。

### 7.4 跟 attempt/backoff 状态机交互的调用（**PR #1530 引入**）

参考 `static/app/app-proactive.js` 的 `/api/proactive_chat`：

- 收到 403 + csrf_validation_failed → refreshToken() + 重试一次。
- retry 仍是 csrf-403 / 其它 403 → 都归到「server 没真正跑业务」分支：不计入 `_voiceProactiveNoResponseCount` / backoffLevel，按 baseInterval 重排。
- 这点跟 HTTP 409（`try_start_proactive` 并发拒绝）同语义——server 在 claim 阶段早退，没消耗任何上下文资源。

---

## 8. 迁移与回滚

### 8.1 添加新端点

1. handler 第一行就调 `_validate_local_mutation_request(request)` 或带 `error_defaults` 的变体
2. 失败时 return validation_error（必要时先 `_set_no_store_headers`）
3. 前端调用方走 `getMutationHeaders()` 注入 token，必要时加 CSRF-403 retry-once
4. 测试：参考 `tests/unit/test_uncovered_endpoints_csrf.py`（批量 parametrize canary，脱胎于 `tests/unit/test_tutorial_prompt_router.py` 里的 `unauthenticated_prompt_client` fixture 模式），对每个端点 parametrize 两条 canary（无 token → 403、错 token → 403）

### 8.2 部署时序

由于规则是 `Origin AND CSRF`，**后端先部署、前端未跟上**的中间态会让所有现有浏览器请求被 403 拒绝。建议：

1. **同 PR 同 release**：把后端守卫和前端调用方一起 merge / 一起发版，避免中间态
2. **NEKO-PC 不需要专门动作**：渲染器是同源加载，自动拿 token，跟纯浏览器走同一条路径（不要相信 wehos 评论里"NEKO-PC 要 preload 暴露 token"的描述，那条已经过时）
3. **长跑心跳的部署兜底**：`app-activity-signal.js` 的 stop-the-heartbeat 退避保证即使遇到老前端 + 新后端的中间态，5s × 6 ≈ 30s 内会干净降级到本地采集器，不会无限自旋
4. **短期端点的兜底**：业务调用方收到 403 → 用户视角看到的是「这次操作没生效」，下次操作会重试（GET 拿到的 cursor 仍带这批，自然重试 ack；proactive_chat 下个间隔重试；翻译失败时回落原文；等等）

### 8.3 紧急回滚 / 事故应急路径

- **撤回后端 PR**：所有端点退回到「无守卫」状态，前端 `getMutationHeaders()` 会照常注入 header（被后端忽略）—— 不破坏前端
- **撤回前端 PR**：所有调用退回到「不带 token」状态，后端会 403 拒绝所有 mutation —— **破坏前端**，所以前后端解耦回滚不建议
- **没有运行时绕过开关**：`AUTOSTART_ALLOWED_ORIGINS` 走的是 *字面字符串相等* 比较（`_get_allowed_local_origins` 把每个值过 `_normalize_origin_value`，只接受 `http/https` scheme + 非空 netloc，然后跟 `request_origin` 做 set 包含检查），所以**没有** `*` / `<any>` 这种 wildcard。同理 `NEKO_AUTOSTART_CSRF_TOKEN` 也只能设成具体 token 字符串，不能开关式禁用。如果守卫真的卡死，唯一应急路径是改源码（让 `_validate_local_mutation_request` 直接 return None）+ 重发版 —— 这就是为什么这一项叫"没有"而不是"如何"。

#### 8.3.1 守卫误杀（守卫 bug / 配置错乱）的应急流程

如果守卫 implementation 真的出 bug 导致所有 mutation 都被 403（例如 `_normalize_origin_value` 误判某种新形态的合法 Origin、`secrets.compare_digest` 在某平台行为异常、或者跟某个反向代理的交互改写了 Origin header）：

1. **立刻识别**：用户报"主动搭话不响应 / 字幕翻译失败 / 通知反复弹"，看 server log 是否被 `csrf_validation_failed` warning 刷屏（守卫拒绝时会打 `logger.warning("Rejected local mutation request...")`）
2. **确认非客户端问题**：照 [3.2 节](#32-非浏览器调用方手动构造请求) 的模板用 curl 手动构造合法请求直接测同一端点；如果 curl 同样 403 → 确认是 server 守卫问题，不是客户端 token 没刷新
3. **临时止血**：在用户机器的 N.E.K.O. 源码 `main_routers/system_router/_shared.py` 里 `_validate_local_mutation_request` 第一行加 `return None`，重启服务（关掉守卫，**只是临时操作**，绝不要 commit 到 main）
4. **正式修复**：identify root cause → 提交修复 PR → merge 后 release → 用户更新到新版本 → 撤回步骤 3 的临时改动

> **为什么不接受"加一个 dev-only env var 应急开关"的建议**（[CodeRabbit on PR #1533](https://github.com/Project-N-E-K-O/N.E.K.O/pull/1533) 提议过）：
> - 任何运行时 env var 开关本质都是把守卫降级权放到运行时，扩大了攻击面（任何能改 env 的进程都能绕过守卫；恶意软件 / supply-chain attack 可以构造 launcher 在 env 里塞 `NEKO_DEV_SKIP_CSRF_VALIDATION=1`，用户毫无感知）
> - "dev/staging 生效、production 硬编码 deny"的双轨实现需要可靠的环境识别，而 N.E.K.O. 桌面壳里"什么是 production"边界模糊：用户机器全是 production，没有部署 stage 区分
> - 改源码 + 重启服务的应急路径**对桌面壳真的够用**——重启服务 = 重启进程（秒级生效），不像 cloud service 需要走 CI/CD pipeline 数小时
> - 如果未来出现一次真实的 incident、改源码路径确实救不了场，再具体讨论；现在 yagni

---

## 9. 测试矩阵

每个接入守卫的端点至少跑两条 canary：

```python
# tests/unit/test_<endpoint>_router.py 或共享文件
@pytest.mark.unit
def test_endpoint_rejects_no_csrf_no_origin(unauthenticated_client):
    """无 token / 无 Origin → 403 csrf_validation_failed."""
    resp = unauthenticated_client.post("/api/<endpoint>", json={...})
    assert resp.status_code == 403
    assert resp.json().get("error_code") == "csrf_validation_failed"


@pytest.mark.unit
def test_endpoint_rejects_wrong_csrf(unauthenticated_client):
    """同源 + 错误 token → 403."""
    resp = unauthenticated_client.post(
        "/api/<endpoint>",
        json={...},
        headers={"Origin": "http://testserver", "X-CSRF-Token": "wrong"},
    )
    assert resp.status_code == 403
```

对于有定制 `error_defaults` 的端点（例如 `/api/activity_signal` 加了 `success: false`），还要断言：

```python
assert resp.json().get("success") is False
assert "no-store" in resp.headers.get("Cache-Control", "").lower()
```

参考批量 canary 的写法：`tests/unit/test_uncovered_endpoints_csrf.py`（PR #1530 引入）parametrize 一次性覆盖 6.2 节列出的 7 个端点；它脱胎于 `tests/unit/test_tutorial_prompt_router.py` 的 `unauthenticated_prompt_client` fixture 模式。

---

## 10. 相关代码

### 后端
- `main_routers/system_router/_shared.py` — `_validate_local_mutation_request`（line 158）/ `_get_request_origin` / `_get_allowed_local_origins` / `_normalize_origin_value` / `_build_public_error_response` / `_set_no_store_headers`
- `config/__init__.py` — `AUTOSTART_CSRF_TOKEN` / `AUTOSTART_ALLOWED_ORIGINS` / `INSTANCE_ID`
- `main_routers/config_router/page_config.py` — `GET /api/config/page_config`

### 前端
- `static/app/app-prompt-shared.js` — `createLocalMutationSecurity()` / `window.nekoLocalMutationSecurity.getMutationHeaders()` / `refreshToken()`（已合并）
- `static/tutorial/core/universal-manager.js` — 短期事件驱动调用 + CSRF-403 retry-once 的参考实现（已合并）
- `static/app/app-activity-signal.js` — 长跑心跳 + stop-the-heartbeat 退避的参考实现（PR #1532 引入）
- `static/app/app-proactive.js` — 与 attempt/backoff 状态机协作的参考实现（PR #1530 引入）

### 测试
- `tests/unit/test_tutorial_prompt_router.py` — 早期 canary 模式（已合并）
- `tests/unit/test_system_screenshot_router.py` — fixture 注入 token 的写法（已合并）
- `tests/unit/test_activity_signal_router.py` — `_build_client(authenticated=True/False)` + custom error_defaults 断言（PR #1532 引入）
- `tests/unit/test_uncovered_endpoints_csrf.py` — 批量 parametrize canary（PR #1530 引入）

---

## 11. 未来工作

- **`page_config` token 暴露的 cookie 化**：当前通过 GET API 把 token 返回给同源 fetch；如果未来想让 token 完全不进 JS 上下文（例如减小 XSS 暴露面），可以改成 `Set-Cookie: HttpOnly; SameSite=Strict` + `_csrf_token` body 字段读取。需要权衡：cookie 路径会牵动 Electron 渲染器、subdomain 部署等场景。
- **远端部署的更细控制**：如果未来真的支持 remote 后端 + 多用户访问，可能需要把 token 从「per-instance 静态」升级到「per-session 短期」，并加用户身份层。当前 `NEKO_ACTIVITY_TRACKER_REMOTE=1` 已经在 screenshot 等端点显式拒绝以避开这层风险，是临时方案。
