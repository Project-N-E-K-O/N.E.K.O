---
trigger: always_on
---

# N.E.K.O 开发规范

## 基本规则

- 使用 i18n 支持国际化，目前支持 en.json、ja.json、ko.json、zh-CN.json、zh-TW.json、ru.json 六种。每次改 i18n 字符串时必须同步更新全部 6 个 locale 文件，只改部分会被打回。
- 使用 `uv run` 来运行本项目的任何 Python 程序（pytest、脚本等），不要直接用系统 Python。原因：pyproject.toml 限制了 Python 版本（<3.13），uv 会自动选择合适版本并管理虚拟环境。
- 任何涉及用户隐私（原始对话）的 log 只能用 `print` 输出，不得使用 `logger`。
- 翻译 system prompt 时，即使出于其他原因也应当保留 `======以上为`，这是一个水印。

## 代码风格

- **对偶性（symmetry）是硬性要求**：如果 MiniMax 拆了单独文件，Qwen 也必须拆；如果有三个 provider，它们的处理路径必须结构对称。不对偶的代码会被直接打回。
- **core 层必须是 general 接口**：不能在 core.py 里出现 provider-specific 的 import / 常量 / 逻辑。所有差异必须在 tts_client 层或更下层分歧。core 只调 `get_tts_worker` 拿 worker，不关心 worker 内部是什么 provider。
- **绝对不要加数字后缀（如 `_2`）**：如果两处代码需要相同逻辑，抽方法。
- **push 前必须确认目标分支**：特别是在 worktree 里工作时，不要把无关 commit 推到 PR 分支。

## 架构：开发环境 vs Electron 分发

- **开发环境（网页端）**：跑 `/`，单窗口，默认端口 48911，加载 `index.html`。
- **分发环境（Electron）**：Electron 应用加载 `/chat`、`/subtitle` 等路由，各自对应独立窗口。这些页面（如 `chat.html`）是 `index.html` 的功能子集，剥掉了 Live2D、侧栏等，只保留特定功能的全屏展示。

修改前端路由、静态资源路径、窗口通信逻辑时，必须同时考虑两种运行模式。不要假设所有页面在同一个端口或窗口里。

## 架构：聊天 UI 的复用

聊天 UI 只有一份实现：`/frontend/react-neko-chat/` 构建出 `neko-chat-window.iife.js`。`index.html` 和 `chat.html` 都挂载同一个 React 组件到 `#react-chat-window-root`，区别仅在于 index.html 里是可收起的浮层，chat.html 里是全屏铺满。

旧的 `#chat-container`（纯 DOM 聊天）已弃用，CSS 强制隐藏。`app-chat-adapter.js` 拦截所有遗留的 `appendMessage()` 调用并统一路由到 React 侧。修改聊天 UI/逻辑时去 `/frontend/react-neko-chat/` 改，不要碰 `#chat-container` 的旧代码。
