---
name: frontend-architecture
description: 主仓库前端的窗口模型和聊天 surface 的不变式——Electron 多窗口怎么判断、chat.html 被两种宿主共用、手机模式怎么排除、启动时序、react-neko-chat 的 full / compact、static/ 搬目录。改 static/、templates/、frontend/react-neko-chat/、main_routers/pages_router.py，或者排查「网页正常、桌面端不对」之前读。
---

# 前端架构：窗口模型与聊天 surface

基础事实（开发跑单窗口、分发是 Electron 多窗口；聊天 UI 只有 `frontend/react-neko-chat/` 一份实现）已经写在 `.agent/rules/neko-guide.md`、`docs/frontend/index.md`、`docs/frontend/pages.md`、`docs/design/compact-chat-mode-design.md`、`frontend/react-neko-chat/README.md`。这里只补那些文档没有、读代码也不容易看出来的部分。

桌面壳在另一个仓库。这里只写主仓库这边必须守住的契约，不写壳的实现。

## 先分清代码跑在哪个窗口

| 场景 | 路由 → 模板 | 判定信号 |
|---|---|---|
| 浏览器单窗口 | `/` → `index.html` | 没有任何 Electron 信号 |
| Electron pet 窗口 | `/` → `index.html` | `window.__LANLAN_IS_ELECTRON_PET__`，`html` / `body` 带 `lanlan-pet-mode` |
| Electron chat 窗口 | `/chat`（compact）、`/chat_full`（full）→ `chat.html` | `body.neko-electron-runtime`（运行时打的） |
| 浏览器直接开 `/chat`、`/chat_full` | `chat.html` | 有 `body.electron-chat-window`，没有 `neko-electron-runtime` |

## 最容易再踩的规则

1. ⚠️ **`window.__NEKO_MULTI_WINDOW__` 不等于「我是 chat 窗口」**。pet 窗口也带这个标志，它只表示「在 Electron 多窗口里」。判 chat 跟随窗必须再加路径：`window.__NEKO_MULTI_WINDOW__ === true && /^\/chat(?:_full)?(?:\/|$)/.test(location.pathname)`。只看标志会让 pet 和 chat 都让位，功能在桌面端整个失效而网页端正常（#3125）。

2. **多窗口下同一条 WebSocket 消息会在 pet 和 chat 两个窗口各执行一遍处理函数**。开窗口、播放、发请求这类只该做一次的事，必须在处理函数里选一个执行者（通常是 pet），跟随窗只更新界面。

3. ⚠️ **`body.electron-chat-window` 是 `chat.html` 模板里写死的 class**，浏览器开 `/chat`、`/chat_full` 也带它；`isElectronChatWindow()` 只看这个 class。只该在 Electron 独立窗口生效的样式和逻辑，用 `body.neko-electron-runtime` 或 `window.nekoChatWindow` 这类运行时信号做门。

4. **手机模式由视口宽度触发，Electron 的两种窗口都必须排除**。CSS 里是 `@media (max-width: 768px)`，靠 `lanlan-pet-mode`（pet）和 `electron-chat-window`（chat）排除；JS 里统一调 `window.isMobileWidth()`，不要自己写 `innerWidth <= 768`。

5. **`index.html` 整页 `pointer-events: none`**（桌宠要点击穿透），可交互的元素自己声明 `auto`。「看得见但点不动」先量 `getComputedStyle(el).pointerEvents`，再怀疑层叠遮挡。

6. **启动早期别裸读 `window.i18next.language`**，也别假设教程已经上锁。要把语言传给后端的请求先等 i18n 就绪；要给「教程即将占屏」让路的逻辑读 `window.isNekoHomeTutorialPending`。

7. **full 和 compact 是两棵互不共享状态的 React 子树**。`App.tsx` 的 `ChatWindowRoot` 按 `chatSurfaceMode` 只挂其中一棵：`full` → `FullChatSurface.tsx`，`compact` / `minimized` → `CompactChatApp`。改了一边，另一边不会跟着变；full 是网页宽屏的默认 surface，不是死代码。

8. **改 `frontend/react-neko-chat/` 要分别验证三条路径**：`index.html` 宽屏、`index.html` 窄屏（手机模式）、`chat.html`。Electron 的行为不能用浏览器完全代替。

9. ⚠️ **改 `frontend/react-neko-chat/src/styles.css`（包括解合并冲突）后要跑读它的 Python 测试**。`tests/unit/` 里有测试用正则按选择器检查具体属性名，前端 vitest 全绿也抓不到。解冲突时保留原来的属性写法，不要顺手改成简写。

10. **compact surface 里新增可点的元素，要带 `data-compact-hit-region="true"`**（加上 `-id`、`-kind`）。不带的话桌面端的穿透窗口会把它当成穿透区，点击落到桌面上；普通浏览器里测不出来。

11. **`static/react/neko-chat/` 是构建产物，不进 git**。改 TS 源码即可；新拉的工作区没有这个目录，要先在 `frontend/react-neko-chat/` 里 `npm run build`，页面才挂得上聊天窗。

12. **前端视觉 / 布局问题不要只靠读代码推理去改**。起开发服务器，用 `getBoundingClientRect`、`getComputedStyle`、`elementFromPoint` 量真实数值，改前改后各量一次。

13. **`static/` 里搬文件、拆文件**：引用点不止模板里的 `<script src>`，还有 JS 里的动态加载列表、`pages_router.py` 的版本路径表、四种写法的测试断言、workflow 里的文件列表。按清单逐类查。


## references 索引

| 文件 | 什么时候读 |
|---|---|
| `references/windows-and-runtime-gates.md` | 写「只在某个窗口执行」的逻辑；改 `chat.html` / `index.html` 的门控 class；处理手机模式、点击穿透、常驻动画；改动可能影响桌面壳的路由、全局变量、事件 |
| `references/startup-timing.md` | 在启动流程里读语言、弹窗口、等教程；排查「冷启动才出现」的顺序问题 |
| `references/chat-surfaces.md` | 改 react-neko-chat 或 `static/app/app-react-chat-window/`：surface 模式和默认值、compact 字幕的两条来源、hit region、折叠球展开、验证清单、自定义头像的规则 |
| `references/visual-debugging.md` | 处理阴影被裁、间距不对、点不动、resize 抖动这类问题 |
| `references/static-dir-migration.md` | 把 `static/` 根目录的 JS 搬进子目录，或把一个大 JS 拆成同目录多个文件 |

后端大文件拆成包之后的门面和打补丁规则在另一份：`.agent/skills/package-split-contracts`。
