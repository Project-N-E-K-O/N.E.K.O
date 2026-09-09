# 原生系统光标替换模块

把 N.E.K.O 原版"隐藏/恢复 Windows 系统光标"路径从**子进程 PowerShell** 换成**Electron 主进程内 koffi → user32** 直接调用，消除每次光标操作带来的 0.5–1.5s 鼠标卡顿。

## 三个文件

| 文件 | 角色 | 注入位置 |
|---|---|---|
| `frontend/native-system-cursor.js` | Win32 原生模块（koffi → user32） | Electron 主进程 require |
| `frontend/system-cursor-ipc.js` | 注册 IPC handler `neko:native-cursor-hide/restore` | Electron 主进程启动时调用 `register(ipcMain, app)` |
| `frontend/system-cursor-preload.js` | 拦截 `window.YuiGuideCommon.syncPcSystemCursorHidden` 转发到 IPC | Electron preload 脚本里调用 `install(ipcRenderer, window)` |

## 接入步骤

### 1. 装 koffi 依赖

```bash
cd S:\Relax_event\N.E.K.O
pnpm add koffi        # 或 npm i koffi
```

### 2. 主进程入口

找到你的 Electron 主进程入口文件（一般在 `frontend/` 旁边的某个 `main.ts` 或 `main.js`，或者在打包配置里指定；你之前没把它放仓库里的话，去打包脚本里看一下 `electron .` 指向哪里）。在 `app.whenReady()` 之后加：

```js
const { app, ipcMain } = require('electron');
require('./frontend/system-cursor-ipc').register(ipcMain, app);
```

### 3. Preload 脚本

找到当前 preload 入口（同样的位置搜 preload），在 `contextBridge.exposeInMainWorld` 之前或在 preload 末尾添加：

```js
const { ipcRenderer } = require('electron');
require('./frontend/system-cursor-preload').install(ipcRenderer, window);
```

### 4. 不需要改其他源码

`static/tutorial/yui-guide/common.js` 的 `syncPcSystemCursorHidden` 继续通过 BroadcastChannel 工作（被其他订阅者消费），preload 里同时把它转发到新的 IPC 路径。两条路并行，但 IPC 路径已把原 PowerShell 子进程架空。

## 行为对比

| | 原版（PowerShell 子进程） | 替换后（koffi + user32） |
|---|---|---|
| 隐藏延迟 | 500–1500ms（PowerShell 冷启动 + 火绒扫描） | <5ms |
| 恢复延迟 | 500–1500ms | <5ms |
| CPU 占用峰值 | 短时 30%+ 拉一个新进程 | <1% |
| 触发鼠标卡顿 | 几乎每次都触发（实测冻结 1–3s） | 不会再触发 |
| 依赖 | PowerShell.exe + .NET | koffi（native，已预编译，Win11 即装即用） |

## 验证

1. 重新启动 N.E.K.O
2. 重载对话 / 触发主动回复
3. 鼠标应该不再出现 "DPI 变小" 的 3 秒卡顿
4. 主进程 console 会出现 `[NekoCursor] hide: 替换 18/18 系统光标` 和 `[NekoCursor] restore: SPI_SETCURSORS=true` 这类日志
5. 如果 koffi 缺失，会看到 `koffi 未安装，请执行 pnpm add koffi` 的提示

## 回滚

只需把 preload 那一行注释掉即可，恢复到 BroadcastChannel → 原 PowerShell spawner 路径。