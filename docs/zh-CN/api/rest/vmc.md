# VMC 动作输出

**前缀：** `/api/vmc`

N.E.K.O. 可以通过 OSC/UDP，把当前 VRM 角色的人形骨骼与表情发送给兼容 VMC Protocol 的接收端。发送器默认关闭，默认目标为 `127.0.0.1:39539`，默认频率为 60 Hz；只有当前激活的是 VRM 模型时才会产生动作帧。

第一方页面应优先使用 `window.vrmVmcSender`。下面的原始 REST 与 WebSocket 协议面向实现，可能随 VRM 运行时继续演进。

浏览器初始只加载轻量 API 代理。在调用 `enable()`、`syncStatusFromBackend()` 等控制方法之前，不会加载完整发送器、轮询状态、创建 VMC 定时器、进入逐帧采样路径或改变原有 VRM 限帧行为。

## 快速开始

1. 启动 VSeeFace、Warudo 或 Unity/Unreal VMC 集成等接收端。
2. 把接收端 UDP 监听端口设为 `39539`。
3. 在 N.E.K.O. 中加载一个 VRM 角色。
4. 在主页面启用输出：

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

可选控制：

```js
await window.vrmVmcSender.requestTPose(2)
await window.vrmVmcSender.disable()
```

目标地址与发送频率会持久化，但后端每次重启后仍会有意保持关闭，需要重新启用。

## 输出协议

后端把 Three.js 右手坐标转换为 Unity/VMC 坐标，并发送：

- `/VMC/Ext/OK`
- `/VMC/Ext/T`
- `/VMC/Ext/Root/Pos`
- `/VMC/Ext/Bone/Pos`
- `/VMC/Ext/Blend/Val`
- `/VMC/Ext/Blend/Apply`
- `/VMC/Ext/VRM`（低频：仅在模型变化时发送一次，用于让接收端标注当前模型）

单帧最多发送 64 根骨骼和 256 个表情。两个上限各自独立生效：65 根骨骼的帧无论带多少表情都会被截断，反之亦然。第一方采样器遍历的是固定的 55 根人形骨骼，因此只有直接向 `/api/vmc/ws` 投递的第三方发布端才可能触顶骨骼上限；表情上限则是浏览器侧可达的——自定义表情数以百计的模型就会触顶。

超出部分会被丢弃，每个上限各记录一次警告。采样器在发送前就已截断，因此浏览器发布端的表情告警出现在浏览器控制台；后端自身的告警覆盖第三方发布端。

网页展示使用的位置、缩放和旋转不会作为 VMC 根节点。VMC 使用独立的单位根，因此拖动桌宠或调整窗口不会改变接收端的世界原点。

禁用输出、切换目标地址或释放当前 VRM 时，N.E.K.O. 会先把活跃表情清零，再发送 `/VMC/Ext/OK 0`。模型释放帧收到确认后，浏览器才关闭专用套接字。

浏览器发布者意外断开后有 2 秒宽限期。若新的发布者在此期间完成认证，输出会无终止过渡地继续；否则后端会清零活跃表情并发送 `/VMC/Ext/OK 0`。

## REST 控制面

修改状态的接口需要 N.E.K.O. 的同源 CSRF 请求头。第一方代码应调用 `window.vrmVmcSender`，不要自行拼接安全请求头。

### `GET /api/vmc/status`

返回当前有效运行状态：

```json
{
  "success": true,
  "enabled": false,
  "host": "127.0.0.1",
  "port": 39539,
  "send_rate_hz": 60,
  "config_path": ".../vmc_config.json",
  "t_pose_requested": false,
  "t_pose_duration_sec": 2.0,
  "t_pose_generation": 0
}
```

### `POST /api/vmc/enable`

JSON 字段均为可选：

```json
{
  "host": "127.0.0.1",
  "port": 39539,
  "send_rate_hz": 60
}
```

`host` 接受 ASCII 主机名或 IPv4 地址；`port` 必须是 `1..65535` 的整数；`send_rate_hz` 必须是 `1..120` 的整数。

帧数据由浏览器采样，因此后端在「关闭 → 开启」这一次跳变时，会通过主聊天 WebSocket 广播一条 `{"type": "vmc_state_changed", "enabled": true}`，唤醒页面加载完整发送器并开始逐帧采样。插件等非浏览器客户端因此可以只调用本接口就完成启用，无需再手动在页面控制台执行 `enable()`。若此时没有页面连接，UDP 发送器仍会打开，但在页面连上之前不会有动作帧。

重复调用本接口（例如仅调整 `port`）不会重新广播：采样已在运行。

### `POST /api/vmc/disable`

发送 VMC 终止状态、关闭 UDP 客户端并返回已禁用的运行状态。

### `POST /api/vmc/t_pose`

请求当前 VRM 在一段时间内输出原始静止姿势：

```json
{
  "duration_sec": 2
}
```

时长必须是有限正数，最大按 10 秒处理。

## WebSocket 数据面

`/api/vmc/ws` 是第一方页面专用的数据通道，与主聊天 WebSocket 完全隔离。

浏览器必须：

1. 从允许的本地 HTTP(S) Origin 建立连接。
2. 在 5 秒内发送带当前 CSRF Token 的 `auth` 消息。
3. 等待 `{"type":"ready"}`。
4. 发送带序号的 `frame` 或 `release` 信封。

同一进程只允许一个发布者持有租约。服务端只保留一个最新的待发送普通帧；释放帧会排在已经在途的帧之后；连续 10 秒没有合法帧会回收发布租约。释放和旧表情清零使用 `frame_ack`，确保 OSC 发送成功前不会提前丢弃状态。

浏览器建立这条连接时，用的是从隐藏同源 iframe 借来的构造函数，而不是 `window.WebSocket`。桌宠版的 preload 会替换顶层构造函数，并把最近创建的套接字注册为聊天 IPC 代理目标，且不按 URL 区分；因此用它创建的 VMC 套接字会抢占聊天通道。如果探测用 iframe 无法创建（CSP `frame-src`、sandbox），**且**顶层构造函数的源码不是 `[native code]`，VMC 会拒绝连接、记录原因并停止采样，而不是继续重连：动作输出断了还能恢复，聊天通道被抢走则不能。

后一项是启发式判断，不是关于宿主的事实。正常浏览器的构造函数通常是原生绑定，所以那里 iframe 被拦一般没有代价；但如果宿主的 WebSocket 是用 JavaScript 实现而非引擎内建，其源码可读，就会被判为非原生（Node 的 undici `WebSocket` 打印的是 `class _WebSocket extends EventTarget`）。两个条件同时命中时的表现是输出静哑，放开同源 iframe（`frame-src 'self'`）即可恢复——借用一旦成功就完全不走这个判断。

关闭码：

| 代码 | 含义 |
| --- | --- |
| `4403` | Origin 或认证被拒绝 |
| `4409` | 消息超过 256 KiB |
| `4428` | 发布者空闲超时 |
| `4429` | 已有另一个 VMC 发布者 |

## 安全与部署

- 不要把主服务器端口 `48911` 暴露给不可信局域网或公网。
- 除非接收端明确运行在另一台可信设备上，否则使用 `127.0.0.1`。
- OSC 使用 UDP，没有传输层确认；浏览器 ACK 只代表 N.E.K.O. 已把帧交给 UDP 发送器。
- 开发环境需要锁文件中的 `python-osc` 依赖，可运行 `uv sync` 安装。

## 故障排查

- **没有动作：**确认 VMC 已启用，且当前激活的是 VRM，而不是 Live2D、MMD 或 PNGTuber。
- **接收端没有数据：**检查目标主机、目标端口、接收端监听端口和本机防火墙。
- **发送频率与配置不符：**正常满帧渲染时，累计调度的长期平均值应接近配置频率；VRM 没有主动动画或交互时会有意节流到约 30 Hz，恢复活动后再升频。
- **提示发布者占用：**关闭另一个 N.E.K.O. 页面，或等待其 10 秒租约超时。
- **控制台报错说会抢占桌面聊天通道：**页面没能拿到原生 WebSocket 构造函数——同源探测 iframe 被拦，**且** `window.WebSocket` 的源码不是 `[native code]`。后一项是启发式判断，所以理论上一个完全没有 preload 的浏览器也可能落到这里。无论哪种情况，在放开同源 iframe（`frame-src 'self'`）之前都不会发出任何动作帧；iframe 一旦可用，借用成功就完全不会走这个判断。
- **采样异常：**系统会暂停采样以保护渲染，并在后续后端状态轮询后重试。
