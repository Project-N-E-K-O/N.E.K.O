# Testbench Dist — 独立安装包 & NEKO 插件

本目录是 **Testbench 打包小工程**，与 [`tests/testbench/`](../testbench/) 业务代码隔离。

**方案草案（已固化，含 2026-08-24 插件通道修订）**：[docs/PLAN.md](docs/PLAN.md)

## 设计原则

| 规则 | 说明 |
|------|------|
| testbench 源码零改动 | 不在 `tests/testbench/` 加 `IS_FROZEN` / dist import |
| 开发入口不变 | 日常仍用 `uv run python tests/testbench/run_testbench.py` + 浏览器 |
| 单向依赖 | `testbench_dist → testbench`；禁止反向 |
| 冻结适配只在 bootstrap | 独立安装包启动期 monkeypatch 路径 / 日志 / API keys |

## 双通道产物

1. **独立安装包** — PyInstaller one-dir + Inno Setup / DMG / AppImage（冻结二进制 + pywebview）
2. **NEKO 插件** — `.neko-plugin` 驱动器：独立 FastAPI；**优先独立 WebView 窗口**（Nuitka 可嵌入 HTTP；浏览器仅降级；不捆绑多平台 exe）

两通道构建解耦：打插件包**不需要**先跑 PyInstaller。

插件自动化验收：

```powershell
uv run python tests/testbench_dist/scripts/run_plugin_smokes.py
```

## 目录

```
testbench_dist/
├── src/           # desktop_main / bootstrap / plugin_shell_main
├── plugin/        # NEKO 驱动器插件（不进 plugin/plugins/）
├── specs/         # PyInstaller .spec（仅独立通道）
├── scripts/       # 构建脚本
├── assets/        # 安装向导品牌图占位
├── smoke/         # dist 侧门禁与验收
├── docs/PLAN.md   # 方案正文
└── output/        # 构建产物（gitignore）
```

## 开发者快速验证（未打包）

在仓库根目录：

```powershell
# 仅验证 pywebview 壳 + 现有 testbench（需先启动 testbench 或由 desktop_main 内嵌启动）
uv run python tests/testbench_dist/src/desktop_main.py --dev
```

`--dev` 模式下不走冻结路径，直接连本机 `127.0.0.1:48920`（若未监听则由本进程启动 uvicorn）。

## 构建（概要）

```powershell
uv run python tests/testbench_dist/scripts/build_all.py --platform win
```

Windows 当前产物（独立通道已落地；插件通道按 PLAN 修订后需重做）：

| 文件 | 说明 |
|------|------|
| `output/pyinstaller/Testbench/Testbench.exe` | 独立通道 one-dir（含 embedding + tiktoken） |
| `output/installer/TestbenchSetup.exe` | Inno 安装向导（若已构建） |
| `output/installer/Testbench-win-x64.zip` | 完整 zip 分发包 |
| `output/installer/Install-Testbench.ps1` | 免管理员安装脚本 |
| `output/testbench.neko-plugin` | 插件包（修订后：源码快照 + vendor，**无** `runtime/*.exe`） |

仅构建插件：`uv run python tests/testbench_dist/scripts/build_plugin.py`（不依赖 PyInstaller）。

安装向导脚本：`scripts/build_installer_win.iss`。详见 `USER_INSTALL.md` / `PLUGIN_INSTALL.md`。

## 排障分界

- `uv run run_testbench.py` 能复现 → testbench 业务问题
- 仅独立安装包能复现 → 查 bootstrap / PyInstaller / installer
- 仅插件能复现 → 查驱动器、FastAPI/WebView 孙进程、端口与 vendor