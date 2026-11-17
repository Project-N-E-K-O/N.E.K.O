# 屏幕分享，以及前端和测试里的零散坑

## 1. 屏幕分享（`static/app/app-screen.js`）

### 「停发送」和「收尾」是两个导出

- `window.stopScreening`：**只停发送**。分享还要继续的调用方用它，比如切换麦克风、隐私模式暂停主动视觉。
- `window.teardownScreenSharing`：**收尾**。停发送，并取消「换来源后的重启」和还在进行中的启动。会话结束、后端报错、结束会话按钮、停止录音这些地方用它。
- ⚠️ 不要混用。收尾的地方只用 `stopScreening`，授权请求晚一点返回时会在会话已经结束的情况下把分享重新打开。反过来，临时暂停的地方用了收尾，会把用户正在进行的换来源取消掉。
- 收尾可能发生在「录音标志还没关」的时刻，所以它只撤掉「共享中」的样式，不按录音状态重新启用按钮。

### 枚举来源会不会弹系统对话框

- Linux 上（Wayland 的 portal，X11 下也可能）枚举屏幕来源会弹系统的选择对话框，并且只返回用户在对话框里选中的那一项。
- 只有一个判断入口：`static/app/desktop-capture-provider.js` 的 `window.desktopSourceEnumerationMayPrompt(provider)`。桌面壳显式声明了布尔值就用它；没声明的旧桥按 UA 推断，只有 Linux 算会弹。悬停展开、来源列表、启动校验都读这个函数，**不要再分出第二套语义**。
- ⚠️ 不能把「没声明」一律当成会弹：旧版 macOS 在单显示器、没有屏幕录制权限时也只返回一项，会被误当成 portal 的选择结果，每次都重启分享。
- 会弹窗的平台上：悬停时不枚举，面板显示「点击选择屏幕来源」，用户点了才枚举；如果只返回一项就直接选中它。即使面板这时已经收起，结果也要采用。
- 「X11 单屏零窗口被当成 portal 结果」「X11 空列表不报错」这类意见不修：前端分不出这两种情况，触发条件极窄。

### 换来源

- 换来源的整个过程（停旧的、等授权、起新的）界面保持「共享中」，开关按「停止」处理。不要把换来源的重启算进「启动进行中」的状态，否则界面显示未共享、点一下却是停止。
- ⚠️ 新一次换来源**不能 `await` 上一次重启**：上一次可能卡在系统授权请求上。靠停止加令牌让被取代的那次不再启动。
- 用户手动点启动时先清掉换来源的令牌，成败都不补启动。

### 当前来源的名称

- 记在 `localStorage` 的 `selectedScreenSourceLabel` 里，存的是 `{id, screenIndex?, name?}`，id 对不上就退回通用文案。屏幕的名字显示时按当前语言现算，不存死。窗口标题只在用户开了「记住窗口」时才落盘，否则只留在本页内存里。
- 跨窗口同步靠自定义事件加 `storage` 事件。⚠️ 桌面端的完整聊天窗在独立的存储分区里，`localStorage` 和 `BroadcastChannel` 都到不了它。
- 文案：名称未知时的兜底用单数的通用词；常驻摘要用「标签：值」的形式。不要复用复数的分组标题或 toast 的模板，西班牙语和葡萄牙语里性数会对不上。

### 测试

- ⚠️ 测试桩里的假 stream 对象过不了 `video.srcObject` 赋值，用它的话启动从来没成功过，换来源相关的断言都是空转。要用 `canvas.captureStream()` 产生的真 `MediaStream`（`tests/frontend/test_screen_source_progressive_loading.py` 的 `_install_share_session`）。
- 这一块的行为在真实 Linux Wayland（GNOME / KDE）上没有自动化覆盖，改动后需要人在真机上走一遍。

## 2. TypeScript 类型检查可能在空转

`tsconfig.json` 是 solution-style（`"files": []` 加 `references`）时，`tsc --noEmit` 一个文件都不检查，永远是绿的。要用 `tsc -b --noEmit`。`frontend/react-neko-chat` 的 `npm run typecheck` 现在就是这个写法。

vitest 不做类型检查，只在某条分支里才执行的代码（比如只有拖拽路径会走到的函数）里的类型错误，测试也跑不到。报告「类型检查通过」之前，先确认这个命令真的检查了文件：故意注入一个类型错误看它会不会红。

## 3. pytest 本地跑法的坑

- ⚠️ **不要把 `tests/` 根目录下的测试文件和 `tests/unit/` 的文件混在一条命令里。**（实测观察）命令行里排在某个根目录文件之后的 `tests/unit/` 文件，不会挂上 `tests/unit/conftest.py` 的 autouse 夹具，表现为「单跑全绿、一起跑时全局状态泄漏」，很像跨用例污染。CI 是整目录跑的，不会出现。看到「单跑绿、批跑红」先检查命令行里有没有混进根目录文件，根目录的文件单独跑。
- 用同步 Playwright 的 Chromium 用例（比如 `tests/unit/test_chat_sync_ui.py`）和 `tests/frontend` 放在同一个 pytest 进程里会报「在 asyncio 循环里用了同步 API」，分开跑。

## 4. Windows 上 `os.replace` 和并发 `open()` 互斥

- 生产代码写文件走 `utils/file_utils.py` 的 `atomic_write_*`：先写临时文件，再 `os.replace` 到目标。在 Windows 上，有人正 `open()` 着目标时 `os.replace` 会报 `PermissionError`（WinError 5 或 32）；反过来，替换进行中去 `open()` 也会被拒。
- 写入侧已经对这两个错误码做了短退避。⚠️ 退避只在不在事件循环上时才做：在事件循环里第一次遇到就直接抛，因为在循环上 `sleep` 会卡住所有会话。
- 测试里要读一个后台正在写的文件时，用 `tests/atomic_read.py` 的 `read_text_when_readable` / `read_text_tolerating_replace`。⚠️ `path.exists()` 不能当门：替换进行中它返回 True，紧跟着的 `open()` 照样失败。
- 判断是不是这类错误看系统错误码，不要猜错误文本。

## 5. 对整个对象的字符串形式做子串匹配会被随机 id 撞上

realtime 连接曾经把 `'429'`、`'503'`、`'1008'` 这类关键字匹配打在整个 error 字典的字符串形式上，而字典里回显着随机生成的十六进制 id。id 里碰巧出现这几个数字时，一条正常的错误会被当成致命错误断开整条连接，现象是「用户说到一半断线、无法复现」。

规则：按关键字给错误分类之前，先剔掉 id 类字段（`main_logic/omni_realtime_client/_transport.py` 的 `_error_classification_text` 剔掉 `id` 和 `*_id`），只在 `message`、`code`、`type` 这类字段上匹配。新写这类分类逻辑时照这个做。
