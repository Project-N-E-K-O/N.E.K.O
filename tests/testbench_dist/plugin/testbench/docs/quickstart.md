# Testbench 插件快速开始

本插件是 **驱动器**：点击「启动 Testbench」后会拉起独立 FastAPI，并在**能力允许时优先打开独立 WebView 客户端窗口**。

- 系统浏览器 / Electron 外开仅作降级
- 正式 Nuitka/Steam 宿主可能使用进程内嵌 HTTP（PLAN Mode B），仍会尽量开独立窗口
- Hosted 面板不承载完整 Testbench UI

## 安装

1. 获取 `testbench.neko-plugin`（插件通道构建，不依赖 PyInstaller exe）。
2. 导入或：

```bash
uv run neko-plugin install path/to/testbench.neko-plugin
```

3. 启动插件 → 面板 → 「启动 Testbench」。

## 数据目录

例如 Windows：`%LOCALAPPDATA%\NEKO-Testbench`（可与独立安装包共用）。  
embedding 优先用本体已有模型。

## 排障

| 现象 | 处理 |
|------|------|
| 服务起不来 | 端口、`bundled/tests/testbench`、vendor、驱动器日志 |
| 有服务无窗口 | 装系统 WebView，或「打开窗口」走浏览器 |
| 连点启动 | 先停止再启 |
| 功能异常 | 先用 `run_testbench.py` 复现 |

详见仓库内 [PLUGIN_INSTALL.md](../../PLUGIN_INSTALL.md) 与 [PLAN.md](../../docs/PLAN.md)。

官方插件入门：[Plugin CLI 快速开始](https://project-neko.online/zh-CN/plugins/quick-start)
