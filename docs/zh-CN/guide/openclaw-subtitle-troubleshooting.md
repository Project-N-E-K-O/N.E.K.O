# OpenClaw / 猫爪总开关与 Subtitle Window 故障调查

## 适用现象

本记录面向以下问题：

1. 猫爪总开关勾上后，过一会儿自动消失。
2. 重启 N.E.K.O 多次、甚至重启电脑后，猫爪总开关仍然打不开。
3. 有时等待一段时间后又可能恢复。
4. DevTools 菜单里的 Subtitle Window 看起来打不开。

本次复查涉及两个仓库：

- 后端：`D:\work\N.E.K.O`
- 桌面端：`D:\work\NEKO-PC`

## 结论摘要

### 猫爪总开关

当前能确定的不是“某一个配置项一定错了”，而是旧实现中存在这条回弹链路：

1. 用户打开猫爪总开关。
2. N.E.K.O 检查外部 QwenPaw 服务是否可用。
3. 检查结果为 `ready=false`。
4. 后端把 `openclaw_enabled` 改回 `false`。
5. UI 表现为“勾上后一会儿消失”。

因此，“猫爪总开关回弹”的直接原因是：**N.E.K.O 在打开猫爪时判定 QwenPaw 当前不可用**。

当前项目中，`OpenClaw` 是对外部 `QwenPaw` 的兼容接入名。N.E.K.O 后端不会自动启动 QwenPaw，只会检查它是否已经可用。

默认检查地址：

```text
http://127.0.0.1:8088/api/agent/health
```

修复后的启用语义是：用户打开猫爪时，N.E.K.O 会保留这次“想启用 OpenClaw”的意图，把 OpenClaw 标记为预检中，并在后台短时间等待 QwenPaw health 变为 ready。只有等待窗口结束后仍然不可用，才会关闭开关并提示真实 reason。

### 为什么重启不一定解决

重启 N.E.K.O 或电脑不一定能解决，因为 N.E.K.O 当前没有 QwenPaw 生命周期管理。

如果 QwenPaw 没有随系统启动，或者启动较慢，或者 health 接口需要一段时间才 ready，那么每次重启后立刻打开猫爪，都可能再次撞上 `ready=false`，于是看起来像“重启好多次都打不开”。

“过一段时间可能自己好了”这个现象更支持启动时序问题：

1. N.E.K.O 已经启动。
2. QwenPaw 还没完全 ready。
3. 用户打开猫爪，health 检查失败。
4. 开关回弹。
5. 稍后 QwenPaw ready，再打开就可能成功。

## 代码证据

### QwenPaw 检查地址

`brain/openclaw_adapter.py`

- `DEFAULT_OPENCLAW_URL = "http://127.0.0.1:8088"`
- `QWENPAW_HEALTH_ENDPOINT_PATH = "/api/agent/health"`
- `OpenClawAdapter.is_available()` 会请求 health 地址。

### 后端会清掉开关

`agent_server.py`

- `POST /agent/flags` 开启 `openclaw_enabled` 时会先记录用户启用意图，并启动后台 readiness probe。
- readiness probe 会反复调用 `adapter.is_available()`，等待 QwenPaw health 变为 ready。
- 等待期间 capability reason 为 `AGENT_PRECHECK_PENDING`，`GET /openclaw/availability` 不会因为临时 `ready=false` 立刻清掉开关。
- 等待期间 `/openclaw/availability` 会保留 QwenPaw 原始 `ready=false`，但额外带上 `pending:true`，用于区分“正在等待”和“最终失败”。
- 如果等待窗口结束后仍然不可用，后端才会把 `openclaw_enabled` 设置回 `false`，并把真实 reason 传给前端。

### 前端会保留启用等待状态

`static/js/agent_ui_v2.js`

- 用户打开 OpenClaw 时不再因为第一轮 availability 失败直接取消勾选。
- 当后端返回 `AGENT_PRECHECK_PENDING` 时，前端保持开关处于开启/等待状态。
- 后端最终确认 ready 后，开关保持开启；最终失败后，开关关闭并显示 reason。

## 日志判断

用户日志里的这些内容不能证明猫爪可用：

```text
GET http://127.0.0.1:50051/api/health 200
[OpenFang] Ready
[CUA] LLM connectivity OK
```

原因：

1. `127.0.0.1:50051` 是 OpenFang，不是 QwenPaw。
2. `48915/openclaw/availability` 返回 HTTP 200，也只代表 N.E.K.O 的接口正常响应；必须看 JSON body 里的 `ready`。
3. 目前日志里没有看到 `GET http://127.0.0.1:8088/api/agent/health 200` 这类 QwenPaw health 成功证据。

## 目前能确定与不能确定的点

### 已确定

1. 旧版开关回弹是由 QwenPaw availability 检查 `ready=false` 触发。
2. N.E.K.O 当前不会自动启动 QwenPaw。
3. 重启 N.E.K.O 不等于重启或拉起 QwenPaw。
4. `50051/api/health 200` 不是猫爪可用证据。
5. `EmbeddingService: vectors disabled` 与猫爪 health 检查无关。
6. `Task was destroyed but it is pending` 出现在 shutdown 附近，不是当前开关回弹的直接证据。

### 高度怀疑

如果用户反馈“过一段时间可能就好了”，更可能是：

1. QwenPaw 启动慢。
2. QwenPaw 端口监听晚于 N.E.K.O。
3. QwenPaw health 接口初始化晚。
4. QwenPaw 依赖模型、运行环境或内部服务热身完成后才 ready。

### 仍需现场区分

以下不是并列确定根因，而是 `ready=false` 的候选分支：

1. QwenPaw 没启动。
2. QwenPaw 启动了，但没有监听 `127.0.0.1:8088`。
3. N.E.K.O 的 `openclawUrl` 指向错误地址。
4. QwenPaw 开了鉴权，但 N.E.K.O 配置的 token 不匹配。
5. QwenPaw 在 `8088`，但 `/api/agent/health` 返回非 2xx。
6. QwenPaw 正在启动中，短时间内 health 尚未 ready。

这些分支有些互斥，不能同时写成“确定根因”。

## 可复现方式

### 复现猫爪开关回弹

1. 使用未修复版本，或把后台 readiness probe 等待窗口调到极短。
2. 确保 QwenPaw 未启动，或启动但 health 暂时不可用。
3. 启动 N.E.K.O。
4. 打开 Agent 总开关。
5. 打开猫爪总开关。

预期结果：

- 开关短暂勾上。
- availability 检查返回 `ready=false`。
- 后端清掉 `openclaw_enabled`。
- UI 勾选状态消失。

修复后预期结果：

- 开关进入开启/等待状态。
- 等待期间即使 availability 返回 `ready=false`，也不会立刻回弹。
- 如果 QwenPaw 在等待窗口内 ready，开关保持开启。
- 如果等待窗口结束仍不可用，开关关闭并显示真实 reason。

### 复现启动时序问题

1. 让 QwenPaw 启动较慢，或在 QwenPaw 尚未 ready 时启动 N.E.K.O。
2. N.E.K.O 启动后立刻打开猫爪总开关。
3. 等待一段时间后，再次打开猫爪。

预期结果：

- 第一次可能回弹。
- QwenPaw ready 后再次打开可能成功。

## 现场验证命令

### 验证 QwenPaw health

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8088/api/agent/health
```

期望：返回 2xx。

### 验证 N.E.K.O 看到的猫爪状态

如果日志中的 tool server 端口是 `48915`：

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:48915/openclaw/availability).Content
```

重点看：

```json
{
  "ready": true
}
```

只有 `ready:true` 才代表猫爪可用。HTTP 200 本身不够。

## 修复方向

### 当前修复策略

1. 不自动启动 QwenPaw，保持“外部工具臂”设计边界。
2. 用户打开 OpenClaw 时，后端保留启用意图，并进入 `AGENT_PRECHECK_PENDING`。
3. 后端短时间轮询 QwenPaw health，等待启动时序完成。
4. readiness probe 成功后保持 `openclaw_enabled=true`。
5. readiness probe 超时或遇到鉴权失败等确定失败后，才关闭 `openclaw_enabled`，并把真实 reason 传给 UI。
6. UI 不再只表现为“勾消失”，而是显示等待状态或失败原因。

### 仍可继续增强

1. 设置页展示当前 `openclawUrl`，方便确认是否指向 `127.0.0.1:8088`。
2. 如果产品预期是“猫爪随 N.E.K.O 自动启动”，再新增 QwenPaw 启动与生命周期管理。

完整生命周期管理需要额外设计：

1. N.E.K.O 检测 QwenPaw 未启动时尝试拉起。
2. QwenPaw 启动中时显示“连接中 / 启动中”，而不是立刻回弹。
3. 对 health 做短时间重试。
4. 保留用户的“想开启猫爪”意图，等 QwenPaw ready 后再真正打开。
5. health 持续失败时再明确报错。

## Subtitle Window 结论

Subtitle Window 是另一个独立问题，不应和猫爪开关回弹混为同一根因。

已确认：

1. 后端 `/subtitle` 路由存在。
2. 桌面端存在 `src/preload-subtitle.js`，会注入 `window.nekoSubtitle`。
3. Subtitle 窗口是懒创建，不是应用启动时默认创建。
4. DevTools 菜单里的 `Subtitle Window` 只会给已经存在的 Subtitle 窗口打开 DevTools，不负责创建窗口。

因此，如果启动后直接点 `DevTools -> Subtitle Window`，而 Subtitle 窗口尚未创建，就会表现为“打不开”。

另一个可复现风险是 Forge/Webpack 开发产物中缺少 `preload-subtitle.js`，导致窗口创建后 `window.nekoSubtitle` 不存在。

## 不应误判

1. 不要把 OpenFang health 成功当作 QwenPaw 成功。
2. 不要把 HTTP 200 的 `/openclaw/availability` 当作 ready；必须看 body。
3. 不要把 `ready=false` 的所有候选分支都写成确定根因。
4. 不要把 Subtitle Window 问题和猫爪总开关问题混成一个根因。
