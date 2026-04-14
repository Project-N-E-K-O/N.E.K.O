# 使用NEKO接入 QwenPaw

## QwenPaw安装指南

### 第一步：安装

无需手动配置 Python，一行命令自动完成安装。脚本会自动下载 `uv`（Python 包管理器）、创建虚拟环境、安装 QwenPaw 及其依赖（含 Node.js 和前端资源）。注意：部分网络环境或企业权限管控下可能无法使用。

macOS / Linux：

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows（PowerShell）：

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### 第二步：初始化

安装完成后，请打开新终端并运行：

```bash
qwenpaw init --defaults
```

这里有个很贴心的设计，安全警告。QwenPaw 会明确告诉你：

> 这是一个运行在你本地环境的个人助理，可以连接各种渠道、运行命令、调用API。如果让多个人访问同一个QwenPaw实例，他们将共享相同的权限（文件、命令、密钥）。

![启用 Neko 频道步骤图 1](assets/openclaw_guide/image1.png)

你需要选择 `yes` 确认理解后才能继续。

### 第三步：启动

```bash
qwenpaw app
```

启动成功后终端最后一行会出现：

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

服务启动后，访问 `http://127.0.0.1:8088`，就能看到 QwenPaw 的控制台界面了。

## NEKO频道配置：让NEKO接入QwenPaw

初始化完成后，QwenPaw 会自动创建配置文件目录。Windows 默认在 `C:\Users\你的用户名\.qwenpaw`，mac 默认在 `~/.qwenpaw`，并启用所有内置技能。

找到该路径。因为 `.qwenpaw` 是隐藏文件夹：

- Windows 用户需要从任务栏打开“文件资源管理器”，选择“查看 > 显示”，然后选择“隐藏的项目”以查看隐藏的文件和文件夹。
- mac 用户需要打开访达，进入主文件夹后，同时按下 `Command + Shift + .`

将我们准备好的频道配置文件 `custom_channels` 复制到 `.qwenpaw` 文件夹中。

将[人设文件夹中的文件](assets/openclaw_guide/%E6%9B%BF%E6%8D%A2%E5%86%85%E5%AE%B9.zip)复制到 `.qwenpaw/workspaces/default` 中，并删除 `BOOTSTRAP.md`。

然后在终端按 `CTRL+C` 结束 qwenpaw，再输入 `qwenpaw app` 重启。

然后按照图片中的步骤启用 Neko 频道。

![启用 Neko 频道步骤图 2](assets/openclaw_guide/image2.png)

## 基础配置：模型配置

点击模型，然后选择 DashScope（根据你的 API Key 也可以选择别的模型），点击设置，输入阿里云百炼 API Key 并保存。

![启用 Neko 频道步骤图 3](assets/openclaw_guide/image3.png)

保存后回到聊天就能选择配置好的模型了。

回到 N.E.K.O 就能使用 openclaw 了。
