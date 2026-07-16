
# 将 N.E.K.O. 接入 QwenPaw

N.E.K.O. 为兼容现有配置，仍把 QwenPaw 集成称为 **OpenClaw**。本指南中的 OpenClaw 开关会连接到另行运行的 QwenPaw 服务。

## 1. 核对来源并安装

请以 [QwenPaw 官方仓库](https://github.com/agentscope-ai/QwenPaw)的当前说明为准。以下命令会下载并直接执行远程安装脚本；如果安全策略有要求，请先审阅脚本。受限网络或受管设备可能会阻止安装。

macOS / Linux：

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

安装器会准备 `uv`、隔离环境、QwenPaw 及其依赖。完成后请打开新终端。

## 2. 初始化

```bash
qwenpaw init --defaults
```

接受前请阅读 QwenPaw 显示的安全提示。同一个本地实例能够访问其运行账户可用的文件、命令和凭据；不要在互不信任的用户之间共用实例。

![QwenPaw 初始化安全提示](assets/openclaw_guide/image1.png)

## 3. 启动并确认

```bash
qwenpaw app
```

默认控制台地址是 `http://127.0.0.1:8088/`。保持终端运行，并在浏览器打开该地址。如果页面无法加载，应先解决 QwenPaw 的启动错误，再启用 N.E.K.O.。

除非已经理解并配置认证与网络边界，否则不要把服务暴露到 localhost 之外。

## 4. 在 QwenPaw 中配置模型

进入 QwenPaw 控制台的模型页面，选择 provider，填写所需凭据并保存；然后回到聊天页选择已配置的模型。可用 provider 和模型名由当前安装的 QwenPaw 版本决定，请以它的实时界面为准，不要依赖复制的列表。

![QwenPaw 模型配置页面](assets/openclaw_guide/image2.png)

## 5. 可选：执行器人设

随文档提供的[替换文件包](assets/openclaw_guide/qwenpaw-executor-profile.zip)包含 `SOUL.md`、`AGENTS.md` 和 `PROFILE.md`，用于偏执行器的人设。此步骤并非连接 N.E.K.O. 的必要条件，而且会改变 QwenPaw 行为。

替换前：

1. 停止 QwenPaw，并备份 `.qwenpaw/workspaces/default`；
2. 检查压缩包内容，与当前 workspace 对比；
3. 只复制你确认要替换的文件。

配置目录通常位于 Windows 的 `%USERPROFILE%\.qwenpaw`，或 macOS/Linux 的 `~/.qwenpaw`。删除 `BOOTSTRAP.md` 只属于这套可选执行器人设流程，并非连接 N.E.K.O. 的要求。修改后重新运行 `qwenpaw app`。

## 6. 在 N.E.K.O. 中启用

1. 启动 QwenPaw 并保持运行。
2. 打开 N.E.K.O. 的猫爪/Agent 面板。
3. 打开 Agent 总开关。
4. 打开 **OpenClaw** 子开关。
5. 等待可用性检查。

N.E.K.O. 默认连接 `http://127.0.0.1:8088`。如果 QwenPaw 使用其他地址，请在 N.E.K.O. 的 core 配置中更新 `openclawUrl`（适配器也接受 `qwenpawUrl`），再重试。

当前适配器同时识别 QwenPaw v2 控制台 API 和旧版 agent 兼容 API。可用性检查会按实际版本探测 `/api/version` 或 `/api/agent/health`，之后使用匹配的控制台或 agent 端点。默认 console 场景不需要另建 channel 文件。
