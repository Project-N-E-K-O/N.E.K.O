# 页面与模板

## 渲染模型

`main_routers/pages_router.py` 使用 `templates/` 中的 Jinja2 模板渲染页面。公共模板上下文包含资源版本；可能初始化 VRM 的页面还会收到后端维护的光照默认值。

资源 URL 应使用根相对形式（`/static/...`）。Electron 窗口和嵌套路由的 URL 深度不同，相对资源路径可能解析到不同位置。

## 公开页面路由

| 路由 | 模板 | 用途 |
| --- | --- | --- |
| `/`、`/{lanlan_name}` | `index.html` | 主界面与角色渲染器 |
| `/model_manager`、旧 `/l2d` | `model_manager.html` | Live2D、VRM、MMD、PNGTuber 模型管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D 参数编辑 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D 动作/表情映射 |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 表情映射 |
| `/mmd_emotion_manager` | `mmd_emotion_manager.html` | MMD morph 映射 |
| `/character_card_manager`、旧 `/chara_manager` | `character_card_manager.html` | 角色卡与角色设置 |
| `/api_key` | `api_key_settings.html` | Provider 与 API Key 设置 |
| `/voice_clone` | `voice_clone.html` | 声音克隆流程 |
| `/cloudsave_manager` | `cloudsave_manager.html` | 云存档管理 |
| `/memory_browser` | `memory_browser.html` | 最近对话记忆审阅与处理设置 |
| `/cookies_login` | `cookies_login.html` | Cookie 登录流程 |
| `/chat` | `chat.html` | compact 独立 React 聊天界面 |
| `/chat_full` | `chat.html` | full 独立 React 聊天界面 |
| `/web_chat_compact` | `index.html` | 强制使用 compact 聊天模式的主页面模板 |
| `/subtitle` | `subtitle.html` | 独立字幕窗口 |
| `/agenthud` | `agenthud.html` | Agent 任务 HUD |
| `/card_maker` | `card_maker.html` | 角色卡制作 |
| `/jukebox`、`/jukebox/manager` | `jukebox.html`、`jukebox_manager.html` | 点歌机及其管理页 |
| `/toast` | `toast.html` | 独立 toast 界面 |
| `/soccer_demo`、`/badminton_demo` | 对应 demo 模板 | 小游戏开发页面 |

`/chara_manager` 会重定向到 `/character_card_manager`。`/l2d` 只是兼容路由，不是另一套 Live2D 实现。

记忆浏览器只编辑主服务器暴露的最近对话文件，不会直接编辑记忆系统维护的事实、反思、人格数据、归档分片或检索索引。

## 聊天与字幕窗口

`index.html` 和 `chat.html` 都把同一份 React 聊天产物挂载到 `#react-chat-window-root`。其余隐藏 DOM 节点只用于兼容公共的语音、会话与截图脚本，并不是第二套聊天 UI。

Electron 通过 preload 提供的 API 控制原生窗口。`chat.html` 检查 `window.nekoChatWindow`；`subtitle-window.js` 在存在时使用 `window.nekoSubtitle`，否则仍可作为 Web 页面运行。跨页面状态使用 `BroadcastChannel('neko_page_channel')` 与同源 `postMessage` 后备通道。Electron 主进程和 preload 实现在本仓库之外。

## 主题

`static/theme-manager.js` 在大部分页面内容之前初始化主题，避免闪烁。主题样式位于 `static/css/dark-mode.css`；需要深色模式的页面必须同时加载两者，并使用共享的数据属性/CSS 变量。

## 静态挂载点

| URL 前缀 | 内容 |
| --- | --- |
| `/static` | 带版本的应用 JS、CSS、图片、内置库与 locale JSON |
| `/user_live2d` | 当前用户 Live2D 目录 |
| `/user_live2d_local` | 当前 Live2D 目录不同时使用的本地可写影子目录 |
| `/user_vrm`、`/user_vrm/animation` | 用户 VRM 模型与 VRMA 动画 |
| `/user_mmd`、`/user_mmd/animation` | 用户 MMD 模型与 VMD 动画 |
| `/user_pngtuber` | 规范化 PNGTuber 包 |
| `/user_mods` | 配置的本地 mod 目录 |
| `/workshop` | Steam 创意工坊内容；可用时在启动阶段挂载 |

挂载点取决于后端目录是否存在。API 响应已经返回公共 URL；客户端不得根据本地路径重新拼接。
