# N.E.K.O 光标卡顿临时补丁（cursor-fix）

> 适用版本：**v0.9.0.1_win**（`S:\Relax_event\N.E.K.O_v0.9.0.1_win\resources\app.asar`）
>
> 状态：**临时方案**。真正的修复应该在私有仓库 `N.E.K.O.-PC` 的 Electron 主进程侧，把 spawn PowerShell 替换为原生 Win32 调用——本仓库只是 Python 后端 + 静态前端，没有 Electron 壳代码。

## 问题

每次桌宠需要"隐藏/恢复 Windows 系统光标"（典型时机：打开对话框、重载对话、猫主动搭话前的几秒），应用会 `spawn` 一个 `powershell.exe` 子进程，加载 .NET Framework 后调用 `user32!SetSystemCursor/GetSystemCursor`。在带实时防护的杀软（典型如火绒）下：

- PowerShell 冷启动 300–800ms
- 杀软扫描子进程再加 100–300ms
- Windows 输入栈在光标重置瞬间对鼠标移动的响应被卡住

最终体感：**鼠标 DPI 突然变小、卡顿约 1–3 秒、然后恢复**。同步发生时 GPU/CPU 占用正常，不是性能问题。

## 这个补丁做了什么

把 spawn PowerShell 替换为 spawn 一个**自写的 140KB Win32 helper 可执行文件**，直接调 `user32!CreateCursor / SetSystemCursor / SystemParametersInfo(SPI_SETCURSORS)`：

- 启动 <10ms（不加载 .NET）
- 杀软扫描时间从几百毫秒降到几毫秒
- 与原 PowerShell 脚本的外部行为完全一致

## 文件清单

| 文件 | 用途 |
|---|---|
| `neko_cursor_helper.c` | Win32 helper 源码（纯 C，无外部依赖）。用 `user32.lib` 链接 |
| `compile.bat` | 用 MSVC 2022 BuildTools 编译 `neko_cursor_helper.c` 的脚本 |
| `patch-app-asar.js` | 一键打/验/回滚 asar 的 Node 脚本（依赖 `@electron/asar`） |
| `system-cursor-visibility-service.js.patch` | unified diff 格式的源码 patch，便于审计与回退 |

> **没有** `.exe` 文件，也没有 `app.asar` 备份——这两类产物不在源码仓库里跟踪。

## 完整使用步骤

### 1. 编译 helper（一次性）

需要本机已安装 **MSVC 2022 BuildTools**（含 Windows SDK 10.0.x）。

```bat
:: 在仓库根目录打开 Developer Command Prompt 或先手动 vcvars
cd tools\cursor-fix
compile.bat
```

成功后会得到 `neko_cursor_helper.exe`（~140KB）。脚本会用 dumpbin 自动校验 user32 导入是否正确。

### 2. 应用 patch 到已编译的 N.E.K.O

首次需要先安装 asar 工具（在仓库根目录一次性执行）：

```bash
mkdir -p tools/asar_tools && cd tools/asar_tools
npm init -y && npm install --no-audit --no-fund @electron/asar
```

然后应用 patch（先**退出 N.E.K.O**，否则 Electron 锁住 asar 写不进去）：

```bash
cd tools/cursor-fix
node patch-app-asar.js install
```

默认会：

1. 备份当前 `resources/app.asar` 为 `resources/app.asar.original_backup`
2. 解包 → 改 `src/system-cursor-visibility-service.js` → 重打包
3. 把 `neko_cursor_helper.exe` 拷到 `resources/bin/`

### 3. 验证

```bash
node patch-app-asar.js verify
```

输出 `✅ 当前已是修复版` 即生效。

### 4. 触发验证场景

启动 N.E.K.O，正常操作：

- 打开 / 关闭聊天对话框
- 重载对话
- 等猫主动搭话前的几秒

观察鼠标是否还有"3 秒 DPI 骤降"。如不再卡，patch 成功。

### 5. 回滚（如出问题）

```bash
node patch-app-asar.js rollback
```

会从 `.original_backup` 恢复 asar。helper exe 不会自动删（万一你想保留作别用），需要手动从 `resources/bin/` 删除 `neko_cursor_helper.exe`。

## 注意事项

1. **Electron 应用必须先完全退出**，否则 asar 被锁，写入会失败。
2. **asar 备份默认放在 `resources/app.asar.original_backup`**。如果 N.E.K.O 升级（重新安装），备份会过期，patch 需要重做。
3. **火绒 / Defender 误报**：自写的 `neko_cursor_helper.exe` 没有代码签名，杀软可能弹"未知发布者"。建议首次扫描后手动加信任区。
4. **这不是上游修复**。真正的修复方向是私有仓库 `N.E.K.O.-PC` 里：
   - 在 Electron 主进程直接用 `koffi` 或 `ffi-napi` 调 `user32.dll`，干掉子进程
   - 配套接入 `frontend/native-system-cursor.js` / `frontend/system-cursor-ipc.js` / `frontend/system-cursor-preload.js`（这 3 个文件已经放在主仓库 `frontend/` 下，等 N.E.K.O.-PC 仓库拿到访问权后对接）
5. **跨版本兼容**：本 patch 是基于 v0.9.0.1 的源码写的。上游只要 `startPlatformHelper` 的 PowerShell 代码块不变，`patch-app-asar.js` 仍能工作；如果上游动了那一段，脚本会主动拒绝（"找不到原 PowerShell 代码块"）以免误伤。

## 故障排查

| 现象 | 可能原因 | 排查 |
|---|---|---|
| `verify` 仍报未 patch | patch 步骤没走完 | 重跑 `install` |
| 启动 N.E.K.O 直接闪退 | asar 完整性 / unpack 规则破坏 | 检查 `app.asar.unpacked/` 还在；`verify` 输出"helper installed: NO"说明 helper 没拷过去 |
| 鼠标仍然卡顿 | 可能是别的卡顿源（GPU 调度 / 音频设备唤醒） | 看 `%APPDATA%\N.E.K.O\neko-electron-debug.log`，确认日志里 `command=...neko_cursor_helper.exe` 而非 `command=...powershell.exe` |
| patch-app-asar.js 找不到 `@electron/asar` | 没装依赖 | 回到步骤 2 安装 asar 工具 |