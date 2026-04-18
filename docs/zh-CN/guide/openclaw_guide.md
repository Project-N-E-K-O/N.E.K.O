# 使用 NEKO 接入 QwenPaw

## QwenPaw 安装指南

### 第一步：安装

无需手动配置 Python，一行命令即可自动完成安装。脚本会自动下载 `uv`、创建虚拟环境，并安装 QwenPaw 及其依赖。注意：部分网络环境或企业权限管控下可能无法使用。

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

初始化时会出现一段安全警告，提醒你 QwenPaw 运行在本地环境里，如果多人共用同一个实例，就会共享文件、命令和密钥权限。阅读后输入 `yes` 继续即可。

![QwenPaw 初始化安全提示](assets/openclaw_guide/image1.png)

### 第三步：启动

```bash
qwenpaw app
```

启动成功后，终端最后一行通常会显示：

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

服务启动后，访问 `http://127.0.0.1:8088`，就能看到 QwenPaw 的控制台页面。

### 第四步：替换人设文件（非必须）

初始化完成后，QwenPaw 会自动创建配置目录：

- Windows 默认在 `C:\Users\你的用户名\.qwenpaw`
- macOS 默认在 `~/.qwenpaw`

因为 `.qwenpaw` 是隐藏文件夹：

- Windows 用户可以在资源管理器中打开“查看 > 显示”，再勾选“隐藏的项目”
- macOS 用户可以在访达中进入用户目录后按 `Command + Shift + .`

如果你希望 QwenPaw 以“纯执行器”身份配合 N.E.K.O，可以下载并替换这份文件包：

- [替换文件.zip](assets/openclaw_guide/替换文件.zip)

把压缩包中的 `SOUL.md`、`AGENTS.md`、`PROFILE.md` 复制到 `.qwenpaw/workspaces/default` 目录下覆盖原文件，并删除该目录中的 `BOOTSTRAP.md`。

替换完成后，在终端按 `CTRL+C` 停掉当前 QwenPaw，再重新执行：

```bash
qwenpaw app
```

## 基础配置：模型设置

打开 QwenPaw 控制台后，进入“模型”页面，选择你要使用的模型提供商。新手最常见的是 `DashScope`，当然也可以按自己的 API Key 选择别的提供商。

点击对应卡片中的“设置”，填入 API Key 后保存。

![QwenPaw 模型配置页面](assets/openclaw_guide/image2.png)

保存后，回到聊天页面，就可以选择刚配置好的模型了。

## 在 N.E.K.O 中启用 OpenClaw

N.E.K.O 内部仍然沿用 `openclaw` 这个名字，所以界面里的 `OpenClaw` 开关实际对应的就是 QwenPaw。

按下面顺序操作：

1. 打开 N.E.K.O 的 Agent 面板
2. 先打开 `Agent` 总开关
3. 确认 `openclawUrl` 指向 `http://127.0.0.1:8088`
4. 再打开 `OpenClaw` 子开关
5. 等待可用性检查通过

N.E.K.O 会优先尝试 QwenPaw 的兼容端点；如果当前实例只暴露主 `process` 端点，也会自动回退，不需要额外手动配置频道文件。
