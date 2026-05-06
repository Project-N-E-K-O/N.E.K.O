# OpenClaw 与 Subtitle Window 故障调查

## 适用现象

本记录面向以下两个问题：

1. `Agent` 总开关能打开，但 `OpenClaw` / “猫爪总开关”勾上后会自动消失。
2. `DevTools` 内的 `Subtitle Window` 无法正常打开。

## 结论摘要

### 1. OpenClaw 开关回弹是当前后端的设计结果，不是纯前端显示问题

`N.E.K.O` 会在打开 `OpenClaw` 时立即做一次可用性检查；如果检查失败，后端会直接把 `openclaw_enabled` 改回 `false`。

- 默认连接地址：`http://127.0.0.1:8088`
- 默认健康检查接口：`GET /api/agent/health`

代码证据：

- [brain/openclaw_adapter.py](../../../brain/openclaw_adapter.py) 中：
  - `DEFAULT_OPENCLAW_URL = "http://127.0.0.1:8088"`
  - `QWENPAW_HEALTH_ENDPOINT_PATH = "/api/agent/health"`
  - `OpenClawAdapter.is_available()` 会直接请求健康检查地址
- [agent_server.py](../../../agent_server.py) 中：
  - `POST /agent/flags` 在开启 `openclaw_enabled` 时，会先调用 `adapter.is_available()`
  - `GET /openclaw/availability` 在发现 `ready=false` 且当前开关为开时，会强制把 `openclaw_enabled` 设回 `False`

这正对应用户看到的现象：勾上以后过一会消失。

### 2. OpenClaw 本质上是“连接外部 QwenPaw”，不是“由 N.E.K.O 自动拉起的内置服务”

当前仓库中没有找到自动启动 QwenPaw 的逻辑；`OpenClaw` 适配器只负责连接外部服务。

代码与文档证据：

- [brain/openclaw_adapter.py](../../../brain/openclaw_adapter.py) 只有 HTTP 连接和请求逻辑，没有 `subprocess` / `Popen` / `create_subprocess` 之类的启动逻辑
- [docs/zh-CN/guide/openclaw_guide.md](./openclaw_guide.md) 明确要求用户单独执行：

```bash
qwenpaw app
```

并以 `http://127.0.0.1:8088` 作为运行实例。

因此，重启电脑或重启 N.E.K.O 后，如果 QwenPaw 没有被重新启动，`OpenClaw` 可用性检查就会失败，开关会再次被打回。

### 3. 当前已排除“健康检查路径写错”这一怀疑

这次调查里重点核对过当前仓库的健康检查路径。代码使用的是：

```text
http://127.0.0.1:8088/api/agent/health
```

这与仓库内的接入说明一致，因此目前没有证据表明“开关回弹”是由健康检查路径写错导致的。

### 4. Subtitle Window 依赖桌面壳注入的 `window.nekoSubtitle`

独立字幕窗口页面本身可以被路由到 `/subtitle`，但页面脚本依赖一个桌面桥接对象：`window.nekoSubtitle`。

代码证据：

- [templates/subtitle.html](../../../templates/subtitle.html) 只加载：
  - `subtitle-shared.js`
  - `subtitle-window.js`
- [static/subtitle-window.js](../../../static/subtitle-window.js) 中：
  - 用 `window.nekoSubtitle` 调 `setSize()`
  - 用 `window.nekoSubtitle.changeSettings()` 把字幕设置回传给宿主窗口

而当前仓库内能确认存在的是主页面使用的 `window.subtitleBridge`：

- [static/subtitle.js](../../../static/subtitle.js) 会暴露 `window.subtitleBridge`

但在本仓库内没有找到 `window.nekoSubtitle` 的定义位置。这说明：

1. `Subtitle Window` 的正常工作依赖桌面壳或 preload 注入层。
2. 如果桌面端打包壳没有注入这层桥接，或者壳与当前前端资源版本不匹配，字幕窗口就可能打不开或打开后不可用。

## 已确认事实

### OpenClaw 侧

1. 主 Agent 服务本身是活着的，用户日志里多次出现：
   - `GET http://127.0.0.1:48915/health 200`
   - `GET http://127.0.0.1:48915/agent/flags 200`
   - `GET http://127.0.0.1:48915/agent/state 200`
2. `OpenClaw` 开关消失并不是 UI 自己改回去，而是后端在可用性检查失败后主动清掉。
3. 当前代码默认要求外部 QwenPaw 实例监听在 `127.0.0.1:8088`。
4. 仓库内没有证据表明 `N.E.K.O` 会自动启动 QwenPaw。

### Subtitle Window 侧

1. `/subtitle` 页面路由本身存在。
2. 页面逻辑依赖 `window.nekoSubtitle`。
3. 当前仓库检索结果只能确认 `window.subtitleBridge`，不能确认 `window.nekoSubtitle` 的实现位置。

## 当前最可能的原因

### OpenClaw

最可能的直接原因是以下其一：

1. 重启后 QwenPaw 没有重新启动。
2. `openclawUrl` 指向的不是当前实际运行的 QwenPaw。
3. 当前运行实例虽然在 `8088`，但健康检查接口无法返回 `200`。

### Subtitle Window

最可能的直接原因是以下其一：

1. 当前桌面壳没有注入 `window.nekoSubtitle`。
2. 当前桌面壳版本较旧，和新的字幕窗口前端脚本不匹配。
3. 独立窗口虽然被打开，但 preload / IPC 桥没有挂上，导致窗口逻辑半初始化。

## 建议的现场验证

### 验证 OpenClaw

1. 直接在浏览器访问 `http://127.0.0.1:8088/api/agent/health`
2. 如果打不开，先单独启动：

```bash
qwenpaw app
```

3. 再回到 `N.E.K.O` 打开 `Agent` 和 `OpenClaw`

### 验证 Subtitle Window

1. 先确认 `/subtitle` 页面能否单独打开
2. 在独立字幕窗口控制台检查：

```js
window.nekoSubtitle
```

3. 如果结果是 `undefined`，基本可以确认是桌面壳 / preload 注入缺失，而不是 `subtitle-window.js` 自身逻辑报错

## 待继续补证据的部分

以下结论还没有在本仓库内完全闭环，只能作为下一步调查方向：

1. 当前用户实际运行的桌面壳代码位于哪里
2. `window.nekoSubtitle` 的真实注入实现在哪个 preload / 壳工程中
3. 用户当前打包产物是否与仓库前端资源版本一致

在没有这三条证据前，不应把 `Subtitle Window` 的最终根因写成“已经确认的打包壳 bug”。
