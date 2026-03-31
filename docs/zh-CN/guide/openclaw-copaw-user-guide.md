# OpenClaw 与 CoPaw 使用手册

本文面向想把 **N.E.K.O 的 OpenClaw** 接到 **CoPaw** 的用户，重点说明：

- 如何确认 CoPaw 已安装
- 自定义通道文件应该放到哪里

本文基于当前项目中的实现：

- N.E.K.O 侧适配器：`brain/openclaw_adapter.py`
- CoPaw 自定义通道脚本：`scripts/custom_channels/openclaw.py`

## 1. 前提说明

N.E.K.O 当前的 OpenClaw 接入方式是：

1. N.E.K.O 向 OpenClaw HTTP 服务发送 `POST /neko/send`
2. OpenClaw 服务返回健康检查 `ready: true`
3. OpenClaw 服务返回最终回复内容给 N.E.K.O

在这套方案里，CoPaw 承担的是“OpenClaw HTTP 服务”的角色。

## 2. CoPaw 安装指南

推荐优先使用 CoPaw 官方脚本安装。

### 2.1 脚本安装

无需预装 Python，安装脚本会通过 `uv` 自动管理运行环境。

步骤一：安装

macOS / Linux：

```bash
curl -fsSL https://copaw.agentscope.io/install.sh | bash
```

然后打开新终端，或者执行：

```bash
source ~/.zshrc
```

或：

```bash
source ~/.bashrc
```

Windows（CMD）：

```bat
curl -fsSL https://copaw.agentscope.io/install.bat -o install.bat && install.bat
```

Windows（PowerShell）：

```powershell
irm https://copaw.agentscope.io/install.ps1 | iex
```

然后打开新终端，安装脚本会自动将 CoPaw 加入 `PATH`。

### 2.2 Windows 企业版 LTSC / 受限语言模式说明

如果你使用的是 Windows LTSC，或者处于受严格安全策略管控的企业环境，PowerShell 可能运行在“受限语言模式”下，可能遇到下面两类情况。

如果你使用的是 CMD（`.bat`）并且脚本执行成功但无法写入 `Path`：

脚本已完成文件安装，但由于“受限语言模式”，脚本无法自动写入环境变量，此时只需手动配置。

找到安装目录：

1. 先检查 `uv` 是否可用，在 CMD 中执行 `uv --version`
2. 如果能显示版本号，则只需要配置 CoPaw 路径
3. 如果提示 `uv` 不是内部或外部命令，也不是可运行的程序或批处理文件，则需要同时配置 `uv` 和 CoPaw 路径

常见路径如下：

- `uv` 路径：`%USERPROFILE%\\.local\\bin`
- `uv` 路径：`%USERPROFILE%\\AppData\\Local\\uv`
- `uv` 路径：Python 安装目录下的 `Scripts` 文件夹
- CoPaw 路径：`%USERPROFILE%\\.copaw\\bin`

手动添加到系统 `Path` 的方式：

1. 按 `Win + R`
2. 输入 `sysdm.cpl` 并回车
3. 打开“系统属性”后，点击“高级”
4. 进入“环境变量”
5. 在“系统变量”中找到 `Path`
6. 点击“编辑”
7. 点击“新建”
8. 依次加入上述目录
9. 保存并重新打开终端

如果你使用的是 PowerShell（`.ps1`）并且脚本运行中断：

由于“受限语言模式”，脚本可能无法自动下载 `uv`。

1. 手动安装 `uv`
2. 可参考 GitHub Release 下载 `uv.exe`，放到 `%USERPROFILE%\\.local\\bin` 或 `%USERPROFILE%\\AppData\\Local\\uv`
3. 或者确保已安装 Python，然后执行 `python -m pip install -U uv`
4. 配置 `uv` 环境变量，将 `uv` 所在目录和 `%USERPROFILE%\\.copaw\\bin` 添加到系统 `Path`
5. 重新打开终端，再次执行安装脚本完成 CoPaw 安装
6. 如有需要，再确认 `%USERPROFILE%\\.copaw\\bin` 已加入系统 `Path`

### 2.3 可选安装参数

macOS / Linux：

```bash
# 安装指定版本
curl -fsSL https://copaw.agentscope.io/install.sh | bash -s -- --version 0.0.2

# 从源码安装（开发/测试用）
curl -fsSL https://copaw.agentscope.io/install.sh | bash -s -- --from-source

# 安装本地模型支持（详见本地模型文档）
bash install.sh --extras llamacpp    # llama.cpp（跨平台）
bash install.sh --extras mlx         # MLX（Apple Silicon）
bash install.sh --extras ollama      # Ollama（跨平台，需 Ollama 服务运行）
```

Windows（PowerShell）：

```powershell
# 安装指定版本
.\install.ps1 -Version 0.0.2

# 从源码安装（开发/测试用）
.\install.ps1 -FromSource

# 安装本地模型支持（详见本地模型文档）
.\install.ps1 -Extras llamacpp      # llama.cpp（跨平台）
.\install.ps1 -Extras mlx
.\install.ps1 -Extras ollama
```

升级时重新运行安装命令即可。卸载时可执行：

```bash
copaw uninstall
```

步骤二：初始化

初始化会在工作目录（默认 `~/.copaw`）下生成 `config.json` 与 `HEARTBEAT.md`。常见有两种方式：

快速用默认配置初始化，不进行交互，适合先跑起来后再改配置：

```bash
copaw init --defaults
```

交互式初始化，按提示填写心跳间隔、投递目标、活跃时段，并可顺带配置频道与 Skills：

```bash
copaw init
```

如果已有配置且想覆盖，可执行：

```bash
copaw init --force
```

步骤三：启动服务

```bash
copaw app
```

服务默认监听 `127.0.0.1:8088`。如果已经配置频道，CoPaw 会在对应 app 内回复；如果尚未配置频道，也可以先完成安装与初始化，再继续配置频道。

### 2.4 如何确认 CoPaw 已安装

常见目录结构如下：

```text
~/.copaw/
├── bin/
│   └── copaw
├── custom_channels/
├── venv/
├── workspaces/
└── config.json
```

如果你机器上存在下面这些路径，通常说明 CoPaw 已安装：

- `~/.copaw/bin/copaw`
- `~/.copaw/config.json`
- `~/.copaw/workspaces`

## 3. 自定义通道应该放在哪里

CoPaw 加载自定义通道的目录是：

```text
~/.copaw/custom_channels
```

当前项目中的 OpenClaw 自定义通道脚本已经放在：

```text
N.E.K.O/scripts/custom_channels/openclaw.py
```

你需要把它复制到 CoPaw 的实际加载目录：

```bash
mkdir -p ~/.copaw/custom_channels
cp /你的/N.E.K.O/scripts/custom_channels/openclaw.py ~/.copaw/custom_channels/openclaw.py
```
或者手动复制到对应目录。

## 4. CoPaw 侧要启用什么配置

### 4.1 启用 `openclaw` channel

启动服务后，打开127.0.0.1:8088，点击 ‘控制-频道’ ，找到Openclaw，点击后，找到已启用开关并打开。

### 4.2 必须配置活动模型

在于CoPaw对话前，需要先配置模型。在 ‘设置-模型’ 中可以快捷配置。

前往阿里云百炼的密钥管理页面。
在 API-Key 页签下，创建或查看 API Key。
重要
子账号需要通过主账号完成授权后再去创建 API Key。
请不要将 API Key 以任何方式公开，避免因未经授权的使用造成安全风险或资金损失。
单击 API Key 列中的复制图标，复制 API Key。

在 CoPaw 页面左侧导航，选择设置 > 模型。
找到提供商区域中的DashScope卡片，单击设置，在弹框中输入API Key，然后单击保存。
说明
API Key：填入前面获取的百炼 API Key。
在LLM 配置区域设置提供商和模型，然后单击保存。
说明
提供商：下拉选择DashScope。
模型：下拉选择Qwen3 Max (qwen3-max)。

然后回到 ‘聊天-聊天’ 选择配置好的模型。

这是最容易漏掉的一步。

即使 `openclaw.py` 放对了、CoPaw 也启动了，只要 **CoPaw 没有配置 active model**，请求仍然会失败，并报：

```text
ValueError: No active model configured.
```

这说明：

- 通道已经工作了
- 请求已经进入 CoPaw agent
- 但是 CoPaw 不知道要用哪个大模型来生成回复


如果没有这一步，OpenClaw 一定无法返回最终回复。

## 5. N.E.K.O 侧如何配置

N.E.K.O 里的 OpenClaw 默认访问地址是：

```text
http://127.0.0.1:8089
```

对应配置项在核心配置里是：

- `openclawUrl`
- `openclawTimeout`
- `openclawDefaultSenderId`

默认值大致为：

```json
{
  "openclawUrl": "http://127.0.0.1:8089",
  "openclawTimeout": 300.0,
  "openclawDefaultSenderId": "neko_user"
}
```

只要 CoPaw 的 OpenClaw 通道监听地址与这里一致，N.E.K.O 就能连上。


## 6. 安装后的启动顺序

推荐顺序：

1. 启动 CoPaw
2. 确认 CoPaw 已加载 `openclaw` 自定义通道
3. 确认 CoPaw 已配置活动模型
4. 启动 N.E.K.O
5. 在 N.E.K.O 中打开 OpenClaw 开关


## 7. 文件对照表

关键文件如下：

- N.E.K.O OpenClaw 适配器：`brain/openclaw_adapter.py`
- CoPaw 自定义通道源码：`scripts/custom_channels/openclaw.py`
- CoPaw 实际加载目录：
  `~/.copaw/custom_channels/openclaw.py`
- CoPaw 主配置：
  `~/.copaw/config.json`
- CoPaw 工作区配置示例：
  `~/.copaw/workspaces/default/agent.json`
