# API 参考

N.E.K.O. 的主 FastAPI 服务器默认监听 `48911` 端口。这里记录的是当前源码中实际存在的路由；这**不代表**所有路由都是稳定、公开或适合远程暴露的 API。

## 兼容性边界

| 接口面 | 预期调用方 | 兼容性预期 |
|---|---|---|
| [运行时工具 API](/zh-CN/api/rest/tools) | 本地插件和伴随进程 | 有文档的本地集成协议 |
| [主 WebSocket 协议](/zh-CN/api/websocket/protocol) | N.E.K.O. 网页端、Electron 端和移动端 | 有文档的客户端协议；标记为内部的消息会随第一方 UI 演进 |
| [云存档 API](/zh-CN/api/rest/cloudsave) | 本地数据管理客户端 | 有文档的破坏性数据操作协议，必须由用户明确触发 |
| 其他主服务器 REST 路由 | N.E.K.O. 第一方页面和贡献者 | 面向实现，可能随 UI 演进，不是通用公共 Web API |
| 记忆服务器和 Agent 服务器 | 主服务器与内部服务之间 | 仅限内部；除调试 N.E.K.O. 本身外，应通过主服务器调用 |

N.E.K.O. 当前没有发布一套单独版本化、承诺整体向后兼容的 HTTP API。稳定的扩展入口是[插件系统](/zh-CN/plugins/)；只有插件需要向模型暴露可调用回调时，才使用运行时工具协议。

## 基础 URL 与安全边界

```text
http://127.0.0.1:48911
```

主 API 前没有统一认证层。`/api/tools`、`/api/capture` 等敏感集成路由会自行限制回环访问，但许多第一方 UI 路由不会。不要把 `48911` 端口暴露给不可信局域网或公网。模型提供商 API 密钥由[配置系统](/zh-CN/config/)管理，不是 API Bearer Token。

除非页面明确说明，这里的路径末尾都没有 `/`。

## 主服务器 REST 路由

### 有文档的集成与数据操作

| 路由 | 前缀 | 边界 |
|---|---|---|
| [运行时工具](/zh-CN/api/rest/tools) | `/api/tools` | 仅回环访问的插件回调注册 |
| [云存档](/zh-CN/api/rest/cloudsave) | `/api/cloudsave` | 角色单元上传/下载，包含破坏性操作 |
| [截图桥](/zh-CN/api/rest/capture) | `/api/capture` | 仅回环访问的第一方 Electron/GalGame 桥 |

### 第一方应用路由

这些页面便于贡献者和替代本地客户端查阅，但路由主要服务于 N.E.K.O. 自身 UI。

| 路由 | 前缀 | 范围 |
|---|---|---|
| [配置](/zh-CN/api/rest/config) | `/api/config` | 提供商配置、偏好和连通性测试 |
| [角色](/zh-CN/api/rest/characters) | `/api/characters` | 角色、人格、角色卡、语音和形象操作 |
| [Live2D](/zh-CN/api/rest/live2d) | `/api/live2d` | Live2D 模型和表情映射 |
| [VRM](/zh-CN/api/rest/vrm) | `/api/model/vrm` | VRM 模型、配置、动画和表情 |
| [VMC 动作输出](/zh-CN/api/rest/vmc) | `/api/vmc` | 把当前 VRM 动作通过 OSC/UDP 发送到本地接收端 |
| [MMD](/zh-CN/api/rest/mmd) | `/api/model/mmd` | MMD 模型和动作管理 |
| [PNGTuber](/zh-CN/api/rest/pngtuber) | `/api/model/pngtuber` | PNGTuber 模型管理 |
| [记忆](/zh-CN/api/rest/memory) | `/api/memory` | 近期记忆文件、审阅/设置、重命名和遗留清理；召回使用内部 `/query_memory` 路由 |
| [Agent 代理](/zh-CN/api/rest/agent) | `/api/agent` | 主服务器代理、任务状态、标志位和诊断 |
| [Steam 创意工坊](/zh-CN/api/rest/workshop) | `/api/steam/workshop` | 浏览、暂存、发布和订阅 |
| [音乐](/zh-CN/api/rest/music) | `/api/music` | 音乐搜索和播放代理 |
| [点歌台](/zh-CN/api/rest/jukebox) | `/api/jukebox` | 歌曲和动作库 |
| [小游戏](/zh-CN/api/rest/game) | `/api/game` | 小游戏状态和操作 |
| [GalGame](/zh-CN/api/rest/galgame) | `/api/galgame` | GalGame 回复选项生成 |
| [破冰引导](/zh-CN/api/rest/icebreaker) | `/api/icebreaker` | 新用户引导流程 |
| [主动搭话](/zh-CN/api/rest/proactive) | `/api/proactive` | 主动搭话模式和设置 |
| [系统](/zh-CN/api/rest/system) | `/api` | 启动、提示词、截图、实用工具、Steam 和诊断 |

## WebSocket

主应用套接字为 `ws://127.0.0.1:48911/ws/{character_name}`。

| 页面 | 内容 |
|---|---|
| [协议](/zh-CN/api/websocket/protocol) | 连接生命周期、会话操作和安全边界 |
| [消息类型](/zh-CN/api/websocket/message-types) | 客户端操作、输入数据和服务端事件 |
| [音频流](/zh-CN/api/websocket/audio-streaming) | JSON PCM 输入与二进制帧服务端音频输出 |

## 内部与未版本化接口

主服务器还挂载了以下第一方实现路由，刻意不把它们作为公共参考页：

- `/api/storage/location` — 首次启动存储位置选择、迁移、目录选择器、重启和保留源清理。
- `/api/avatar-drop` — 编辑器文档解析辅助接口，输出结构随当前第一方 UI 演进。
- `/api/card-assist` — 与当前提示词和已配置 LLM 提供商耦合的角色卡生成/润色流程。
- `/api/auth` — 本地 Cookie 与扫码登录状态，包含兼容接口；涉及凭据。
- `/api/debug` — 持续演进的诊断快照和浏览器健康上报。
- `/health` — 供启动器/进程使用的轻量健康探针。
- `/api/beacon/shutdown` — 浏览器模式生命周期控制，不是应用集成接口。
- `/market` 与 `/market/{path}` — 到用户插件服务器的不透明同源反向代理，其 schema 不属于主 API。

除非同时控制匹配的 N.E.K.O. 版本，否则不要基于这些接口构建第三方集成。

## 内部服务 API

| 服务 | 默认地址 | 边界 |
|---|---|---|
| [记忆服务器](/zh-CN/api/memory-server) | `http://127.0.0.1:48912` | 内部记忆生命周期、渲染和召回 |
| [Agent 服务器](/zh-CN/api/agent-server) | `http://127.0.0.1:48915` | 内部 Agent 执行与 ZeroMQ 传输 |

## 响应与内容类型

响应结构因路由而异。FastAPI/Pydantic 校验通常使用 `detail`；部分应用路由则返回 `success`、`error`/`code` 和消息字段。请遵循各页面的具体协议，并按机器可读错误码而不是英文消息分支。

常见类型包括 JSON、用于上传的 `multipart/form-data`、用于语音试听的音频响应，以及服务端音频使用的 WebSocket 二进制帧。
