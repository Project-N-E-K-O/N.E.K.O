# Testbench NEKO 插件 — 安装说明

对齐官方入门：[用 Plugin CLI 创建并运行第一个插件](https://project-neko.online/zh-CN/plugins/quick-start)

## 这是什么

**驱动器插件**：在 Plugin Manager 里启动 / 停止 Testbench。

- 后端：独立 FastAPI（挂 `127.0.0.1`），**不与插件 ZMQ/IPC 交互**
- 前端：**能力允许时优先打开独立 WebView 窗口**；不可用时再降级到系统浏览器 / Electron 外开
- 正式 Nuitka/Steam 宿主可能走「进程内嵌 HTTP」模式（见 PLAN 能力分层）；开发态 `uv` 通常走单壳进程
- Hosted 面板只负责启停与状态，**不是** Testbench 界面重写版

源码：`tests/testbench_dist/plugin/testbench/`（**不**进内置 `plugin/plugins/`）。  
细节：[docs/PLAN.md](docs/PLAN.md)「NEKO 插件版」。  
**Market 独立仓库**（审核/发布用）：见 [docs/PLUGIN_MARKET_REPO.md](docs/PLUGIN_MARKET_REPO.md)。

## 构建

插件通道**不需要**先跑 PyInstaller：

```powershell
uv run python tests/testbench_dist/scripts/build_plugin.py
```

产物：`tests/testbench_dist/output/testbench.neko-plugin`  
（旧版 `runtime/*.exe` 已废弃。）

## 安装

```powershell
uv run neko-plugin install tests/testbench_dist/output/testbench.neko-plugin
```

或在插件管理器中导入。

## 使用

1. 启动插件进程 → 打开面板 → **启动 Testbench**  
2. 正常情况会出现 **独立客户端窗口**  
3. 若仅服务起来、无窗口：用「打开窗口」走浏览器降级，或检查系统 WebView  

面板状态里的 `mode` / `ui` 可区分 Mode A（单壳）与 Mode B（嵌入）及当前 UI 形态。

## 检查

```powershell
uv run neko-plugin check tests/testbench_dist/plugin/testbench
uv run neko-plugin check -r tests/testbench_dist/plugin/testbench
```

## 自动化验收（无需 Plugin Manager UI）

```powershell
uv run python tests/testbench_dist/scripts/run_plugin_smokes.py
```

覆盖：隔离门禁、单元 smoke、build_plugin、包内容、Mode A shell healthz、Mode B 嵌入 healthz、`neko-plugin check`。

## 排障

| 现象 | 处理 |
|------|------|
| 启动失败 / healthz 不通 | 查 `data_path()/logs/`、端口、`bundled/tests/testbench`、vendor |
| 有服务无窗口 | WebView 缺失或 Mode B 降级 → 「打开窗口」；Windows 可装 WebView2 |
| 连点启动 | 应显示已在运行 |
| 功能问题 | `uv run python tests/testbench/run_testbench.py` 区分业务 vs 驱动层 |
| 版本不符 | 见插件声明的 `compatible_neko` |
