# 聊天 surface：full / compact / minimized

状态合同、geometry 合同、手工验收范围见 `docs/design/compact-chat-mode-design.md`；三种宿主上下文的差异见 `frontend/react-neko-chat/README.md` 的「Three host contexts」。这里补代码里的实际结构和踩过的坑。

## 1. 模式从哪来、默认是什么

宿主脚本在 `static/app/app-react-chat-window/`（由原来的单文件拆成同目录多个文件）。surface 取值是 `full` / `compact` / `minimized`，逻辑在 `bootstrap-state-and-geometry.js`：

- 初始值：`body` 的 `data-initial-chat-surface-mode`（由路由声明：`/chat` → compact，`/chat_full` → full，`/web_chat_compact` → compact）优先；没有声明就读 `localStorage` 的 `neko.reactChatWindow.chatSurfaceMode`；再没有才用默认值。
- 默认值（`getDefaultChatSurfaceMode()`）：`window.__NEKO_CHAT_DEFAULT_COMPACT__` 显式指定 > `chat.html` → compact > Electron pet 窗口 → compact > 手机宽度 → compact > 其余（网页宽屏）→ **full**。
- 只有 `compact` 和 `full` 会被持久化。`minimized` 不落盘，展开时回到 `lastRestorableChatSurfaceMode`。
- `/chat`（`data-chat-host-kind="compact"`）里 `full` 会被强制成 `compact`。

## 2. full 和 compact 是两棵子树

`frontend/react-neko-chat/src/App.tsx` 默认导出的 `ChatWindowRoot` 只做分派：`chatSurfaceMode === 'full'` 挂 `FullChatSurface`（`FullChatSurface.tsx`），否则挂同文件里的 `CompactChatApp`。两棵子树同一时刻只挂一棵，各自持有自己的 state 和 hooks。

- 历史列表、输入区、响应式布局两边各写各的，不互相复用。`CompactChatApp` 里的 `isCompactSurface` 之类的判断不需要考虑 full，因为 full 根本进不来。
- 共用的是消息渲染（`MessageList` 等）、头像道具的目录 / 运行时 / 视觉层（`avatar-tools/`）、`useFocusGlow` 这类 hook。改共用部分两边都受影响。
- 宿主侧 full 专属的分支用 `I.getCurrentChatSurfaceMode() === 'full'` 或 `isLegacyFullMinimizedBall()` 严格门控；full 最小化成的是旧的呼吸灯球（`static/css/full-chat-minimize.css`），compact 是毛线球。`full` 故意不在 `CHAT_SURFACE_MODE_SEQUENCE` 里，不参与 compact 的切换循环。

规则：

- 改 compact 的布局和交互时不要去动、也不要去复用 full 的布局代码，反过来也一样。宁可重复，不要让一边的改动牵动另一边。
- 一个新功能要不要在 full 里也有，按功能逐个定，并且两边各接一份。现状是清屏、串门、头像道具等功能两边都接了。
- 宿主脚本的注释把 full 叫作 legacy，但它是网页宽屏的默认 surface，也是 `/chat_full` 独立窗口的内容，用户够得着。

## 3. 宿主脚本的「多文件共享一个 `I`」结构

`static/app/app-react-chat-window/`、`static/app/app-ui/`、`static/app/app-interpage/` 都是把一个大文件拆成同目录多个经典脚本，文件之间通过一个挂在 `window` 上的共享对象（如 `window.__appReactChatWindowParts`，文件里叫 `I`）互相调用。

- 没有 `import` / `export`。加载顺序由模板里的 `<script>` 顺序决定，`index.html` 和 `chat.html` 各有一份列表，新增文件两个模板都要加，并且放在它依赖的文件之后。
- 跨文件用的函数要挂到 `I` 上；只在本文件用的留成局部函数。
- 测试读这些目录用 `tests/static_app_parts.py` 的 `read_js_parts()`（Python）和 `static/app-part-test-utils.cjs` 的 `readJsParts()`（node）：按文件名排序拼接，默认把 `I.` 前缀去掉再做字符串断言。所以把一个局部函数改成 `I.xxx`、或者把函数从一个文件挪到同目录另一个文件，静态断言通常不受影响；但把断言里钉住的那一行改写了就会挂。

## 4. compact 胶囊字幕有两条来源

`CompactChatApp` 里：`compactMessagePreview = compactCaptionPreview || compactMessagePreviewFromMessages`（小剧场进行中两者都不用）。

1. **事件来源 `compactCaptionState`**。由 `neko-compact-caption-update` 和 `neko-assistant-turn-start` / `-ending` / `-end` 驱动，按 `turnId` 把同一轮的多段文本聚成一条（同 `segmentId` 替换、新 `segmentId` 追加）。一轮结束只标 `isEnded`，文本不清，等下一个 `turn-start` 才重置。派发方是真实对话的流式路径：`static/app/app-chat-adapter.js` 的 `emitCompactCaptionUpdate()`，要求 `window._nekoAssistantTurnId` 有值。
2. **消息回退 `getCompactMessagePreview(messages)`**。只在消息列表里存在「正在流式输出的助手消息」时返回内容；没有就返回 `null`，不显示历史。

⚠️ 不经过 adapter、直接往 React 推消息的来源只走第 2 条。新手教程的台词就是这样：`static/tutorial/yui-guide/director/chat-performance.js` 自己构造 `id` 以 `yui-guide-` 开头的消息（没有 `turnId`），用 `host.appendMessage()` 推进去，不派发字幕事件。后果和现行处理：

- 这类台词流式播完、状态变成已发送的那一刻，回退返回 `null`，胶囊里的字幕就没了。普通回复有事件来源兜着，不受影响。
- 没有 `turnId` 的相邻消息会按创建时间窗口合并成一条字幕；`getCompactMessagePreview` 里对 `yui-guide-` 消息单独加了边界，不和相邻消息合并。
- 新增一种「自己往聊天里推台词」的功能时，先决定字幕走哪条：要和真实回复一样稳定，就带上 `turnId` 并派发 `neko-compact-caption-update`（新用户破冰 `static/tutorial/icebreaker/new-user-icebreaker.js` 是这么做的）。

## 5. 直接 `host.appendMessage()` 的消息不会自动带 `turnId`

`turnId` 是 `app-chat-adapter.js` 在处理真实对话流时注入的。绕过 adapter 直接调 `window.reactChatWindowHost.appendMessage()` 的消息（主动搭话附带的表情包、音乐卡、教程台词等）默认没有。

- React 侧凡是按「是不是同一轮」做判断的逻辑，对没有 `turnId` 的消息会退回到按时间或按角色的旧规则。
- 需要归到某一轮的，由调用方自己填。例：`static/app/app-proactive.js` 的 `_showMemeBubbles()` 把表情包绑到所属主动搭话轮的 `turn_id`，和同轮台词是同一个值。
- 宿主的消息克隆会原样保留 `turnId`。

（表情包现在只显示在聊天历史里，不再有浮在输入条上方的浮层，`App.test.tsx` 里有测试钉着这一点。）

## 6. compact 里可点的元素要登记 hit region

桌面端 compact 窗口是穿透窗口：只有登记过的矩形收鼠标事件，其余像素穿到桌面。登记由宿主的 `collectCompactSurfaceGeometryItems()`（`geometry-and-messages.js`）从 DOM 上采集。

- 带 `data-compact-geometry-hit-scope="children"` 的容器，自己不算可点区域，**只有**带 `data-compact-hit-region="true"` 的子元素才登记。子元素同时写 `data-compact-hit-region-id`（稳定 id）和 `data-compact-hit-region-kind`。
- `display: none`、`visibility: hidden`、透明度接近 0、`pointer-events: none` 的子元素不登记。
- 现成的例子：`static/jukebox/music_ui.js` 的音乐条、`App.tsx` 里的 `compact-chat-minimize-ball`。

⚠️ 漏了标记的后果是「桌面端点上去没反应、点击落到桌面」。浏览器和 vite 开发页里所有像素都收事件，测不出来。给 compact 加按钮时把这条当成必查项。

## 7. 折叠球展开的落点，和那条名字带 Ball 的循环

- compact 最小化成球、拖动球、再展开：输入条不会出现在球的位置，而是回到 compact 自己的定位（保存过的位置，或默认位置）。这是现行行为，不是回归。full 的呼吸灯球展开时才从球的位置算对话框位置。
- 机制有两处：`minimize-and-idle-dock.js` 的 `getExpandedTargetFromSavedState()` 在 compact 分支直接取 `I.getCompactSurfaceTarget()`；`static/css/index.css` 里 compact 稳态用 `left / top: var(--compact-surface-left / top) !important`，这些变量由 `I.syncCompactSurfaceAnchor()` 持续重写，写内联位置会被盖回去。要改成跟球展开，这两处要一起改，并把球的位置写回 compact 的位置存储。
- ⚠️ `I.scheduleCompactMinimizeBallTracking()` / `I.compactMinimizeBallFrame` 名字里有 MinimizeBall，实际是整个 compact surface 的定位和 geometry 同步循环（每帧调 `syncCompactSurfaceAnchor()` 和 `syncCompactInteractionGeometry()`）。不要当成球的遗留代码删掉。

## 8. 改完之后验证什么

在 `frontend/react-neko-chat/` 里：

- 类型：`npm run typecheck`（`tsc -b --noEmit`）。`tsconfig.json` 是只含 `references` 的总入口，不带 `-b` 的 `tsc --noEmit` 什么都不检查。
- 单测：`npm test`。
- 构建：`npm run build`，产物写到 `static/react/neko-chat/`（被 git 忽略；CI 的单元测试任务也不构建它，由桌面打包流程重建并校验）。

仓库根目录：

- ⚠️ 改了 `src/styles.css`：在 `tests/` 里搜 `styles.css`，把读它的 Python 测试跑一遍，至少包括 `tests/unit/test_ui_opacity_preferences.py`、`tests/unit/test_react_chat_window_static.py`、`tests/unit/test_plugin_image_frontend_static.py`。其中有按选择器检查具体属性名的断言，例如 `.compact-chat-surface-frame::after` 必须声明 `background-image` 并带 `var(--neko-chat-opacity-factor, 1)`，写成 `background:` 简写就失败。这部分契约不在前端测试套件里。
- 改了宿主脚本：`tests/unit/test_react_chat_window_static.py`、`tests/unit/test_react_chat_idle_dock_static.py`，以及 `static/` 根目录和 `tests/frontend/` 下相关的 `*.test.cjs`。

手工：`index.html` 宽屏、`index.html` 窄屏、`chat.html` 三条路径各过一遍。滚动、动效、尺寸观察这几类改动要在 Electron 里也看。

## 9. 被 Python 测试逐字钉住的行

有些静态测试断言宿主脚本里某一行的原文存在。例：`tests/unit/test_new_user_icebreaker_static_contracts.py` 要求 `var baseAvatarUrl = message.baseAvatarUrl || message.avatarUrl` 原样出现在聊天宿主里。

要在这种行上加逻辑时，保留原行，另起一行处理（现有代码就是下一行再调 `fallbackAvatarUrl(...)`）。改之前先在 `tests/` 里搜一下那行的关键片段。

## 10. 自定义聊天头像留下的规则

相关代码：`static/app/app-chat-avatar*.js`、`main_routers/characters_router/chat_avatar.py`、`utils/chat_avatar_store.py`。

- **只需要角色 UID 时，请求 `/api/characters?language=zh-CN`**。不带语言参数会触发人设翻译，可能调用 LLM 而超时。
- **写操作遇到 403 且 `error_code` 是 `csrf_validation_failed`**：刷新一次令牌，用同一个操作 ID 重发一次。刷新期间用户取消或换了角色就不发。「在途时取消」要单独记录、每次发送清零，只在准备重发时才转成取消——不能做成「取消一律记住」，因为结果不确定时用户会用同一个绑定显式重试，会被误拒。
- **取凭据、等页面配置都要有期限**，超时报具体的错误码，不要无限等。
- **每次打开头像弹窗都重新读一次服务端状态**。用这一条规则覆盖所有「弹窗里的数据是旧的」的情况，不要按错误类型逐个加标记。
- **Escape 键**：弹窗照常无条件 `preventDefault()`（点击引导靠 `event.defaultPrevented` 判断按键是否已被消费），只有事件目标或焦点在弹窗内时才 `stopImmediatePropagation()`。
- **宿主给消息补头像时**，如果拿到的是本页显示过的自定义图（`appChatAvatarState.isCustomDataUrl()`），换成 `appChatAvatar.getModelAvatarDataUrl()`。自定义头像随时可能被用户恢复成默认，不能留在消息上当回退头像。
- **后端配额**：统计占用时，把「UID 合法但不在现存角色里」的记录排除并尝试删除（删角色后清理失败留下的孤儿）。依据是角色 UID 不复用。
- **存储迁移**：`chat_avatars` 只在 `utils/storage/migration.py` 的 `MIGRATED_RUNTIME_ENTRY_NAMES` 里，不在 `V1_MIGRATED_RUNTIME_ENTRY_NAMES` 里（v1 版本从没迁移过它）。以后新增运行时数据目录同理。
