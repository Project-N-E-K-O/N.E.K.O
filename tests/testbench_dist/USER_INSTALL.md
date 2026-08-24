# Testbench 独立安装包 — 用户说明

## 安装

### Windows（当前推荐）

1. 下载 `Testbench-win-x64.zip` 与同目录的 `Install-Testbench.ps1`
2. 右键以 PowerShell 运行安装脚本（或）：
   ```powershell
   powershell -ExecutionPolicy Bypass -File Install-Testbench.ps1
   ```
3. 默认安装到 `%LOCALAPPDATA%\Programs\NEKO-Testbench`，并创建桌面/开始菜单快捷方式后启动。

也可直接解压 zip，双击其中的 `Testbench.exe`。

若本机已安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)，可用 `scripts/build_installer_win.iss` 生成正式 `TestbenchSetup.exe` 向导安装包。

无需安装 Python，无需克隆 NEKO 仓库，无需用浏览器打开页面。

## 数据目录

| 系统 | 默认路径 |
|------|----------|
| Windows | `%LOCALAPPDATA%\NEKO-Testbench` |
| macOS | `~/Library/Application Support/NEKO-Testbench` |
| Linux | `~/.local/share/NEKO-Testbench` |

可用环境变量 `NEKO_TESTBENCH_DATA_DIR` 覆盖。

内含：会话沙盒、JSONL 日志、`live_runtime/current.log`、`api_keys.json`、导出文件等。

## API Key

首次启动会在数据目录创建空的 `api_keys.json`。也可在应用内 **Settings** 配置模型与 Provider。

## 从本机 N.E.K.O 导入角色

若本机已安装并使用过 N.E.K.O，Setup → Import 可读取系统标准用户数据目录中的角色。未安装 N.E.K.O 时列表为空属正常。

## 排障

| 现象 | 处理 |
|------|------|
| 窗口空白 | 确认 WebView2 Runtime（Windows）已安装 |
| 无实时日志 | 查看数据目录 `live_runtime/current.log` |
| 功能 bug | 开发者用 `uv run python tests/testbench/run_testbench.py` 复现；能复现则与打包无关 |

## 与插件版关系

`.neko-plugin` 启动的是同一套桌面程序，数据目录共用。
