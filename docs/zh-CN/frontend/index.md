# 前端概述

N.E.K.O. 的主界面由 FastAPI 主服务器提供。仓库中有三套构建与运行边界不同的前端代码。

## 代码组成

| 界面 | 技术 | 源码 | 运行产物 |
| --- | --- | --- | --- |
| 主界面与辅助页面 | Jinja2、原生 JavaScript、CSS | `templates/`、`static/app/`、`static/live2d/`、`static/vrm/`、`static/mmd/` | 由主服务器渲染，通常使用 `48911` 端口 |
| 聊天 UI | React 18、TypeScript | `frontend/react-neko-chat/` | `static/react/neko-chat/neko-chat-window.iife.js` 与 `.css` |
| 插件管理器 | Vue 3、TypeScript | `frontend/plugin-manager/` | `frontend/plugin-manager/dist/`，由插件服务器提供 |

角色渲染器属于主界面：Live2D 使用 Pixi/Cubism，VRM 和 MMD 使用 Three.js，PNGTuber 使用 `static/pngtuber-core.js`。Electron 桌宠是宿主模式，不是另一种角色格式。

## 唯一的聊天实现

`frontend/react-neko-chat/` 是聊天 UI 的唯一真实实现。IIFE 产物暴露 `window.NekoChatWindow`，`static/app/app-react-chat-window/` 下的脚本把它挂载到 `#react-chat-window-root`。

`templates/index.html` 与 `templates/chat.html` 都提供该挂载点。前者在主页面中显示可收起的浮动聊天界面；后者承载 compact 或 full 独立聊天界面。

旧 `#chat-container` DOM 仅作为旧脚本的兼容空壳保留。两个模板都会隐藏它，`static/app/app-chat-adapter.js` 会把遗留的 `appendMessage()` 调用替换为对 `window.reactChatWindowHost` 的调用。不要再往旧容器增加 UI 或逻辑。

## Web 与 Electron 宿主

在浏览器中，`/` 是单一主页面；也可以直接打开 `/chat`、`/chat_full` 与 `/subtitle` 进行开发和测试。

Electron 分发应用是独立的宿主程序。它把多个路由加载到不同窗口：桌宠使用主页面模板，聊天窗口使用 `/chat` 或 `/chat_full`，字幕使用 `/subtitle`。渲染器代码检测 `window.nekoChatWindow`、`window.nekoSubtitle` 等 preload 全局；原生窗口创建与 IPC 由宿主负责。

跨窗口的 Web 后备通道位于 `static/app/app-interpage/`，使用 `neko_page_channel` `BroadcastChannel`，并以同源 `postMessage` 兜底。修改路由、资源 URL、初始化顺序或窗口通信时，必须同时检查浏览器与 Electron 模式。

## 加载与资源规则

- 服务端渲染页面使用 `/static/...` 形式的根相对 URL；不要根据当前路由推导资源路径。
- 用户模型与创意工坊模型通过专用挂载点提供；不要把文件系统路径转换成浏览器 URL。
- `static/` 下的经典脚本通过约定的全局对象和 DOM 事件通信，因此模板加载顺序属于运行时契约。
- React 聊天改动应进入 `frontend/react-neko-chat/`，重新构建 IIFE，不要编辑生成文件。
- 插件管理器改动应进入 `frontend/plugin-manager/`；它的构建与本地化独立于主页面。

当前入口请继续阅读[页面与模板](/zh-CN/frontend/pages)、[国际化](/zh-CN/frontend/i18n)以及各渲染器页面。
