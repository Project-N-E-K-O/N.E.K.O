# 手动搭建

适用于在任何平台上进行开发和自定义。

## 前置条件

- Python 3.11（必须是此版本，不支持 3.12+）
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 包管理器
- Node.js（>=20.19）
- Git

## 安装

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

## 可选：本地嵌入模型

向量记忆使用可选的本地 ONNX 模型资源。下载命令、目录布局以及 PyInstaller / Nuitka 打包说明见
[`embedding-models.md`](embedding-models.md)。

## 构建前端

项目在 `frontend/` 下有两个前端项目，运行前需要先构建。

**推荐** —— 从项目根目录使用一键脚本，这是官方支持的构建方式：

```bash
# Windows
build_frontend.bat

# Linux / macOS
./build_frontend.sh
```

如需手动执行，命令必须与脚本保持一致：

```bash
cd frontend/react-neko-chat && npm install && npm run build && cd ../..
cd frontend/plugin-manager && npm install && npm run build-only && cd ../..
```

## 运行

推荐优先使用统一启动器：

```bash
uv run python launcher.py
```

这样会先完成本地 `cloudsave/` bootstrap 与快照导入，再启动各个服务，更接近 Steam / 桌面版实际启动链路。

在不同终端中启动所需的服务器：

```bash
# 终端 1 — 记忆服务器（必需）
uv run python memory_server.py

# 终端 2 — 主服务器（必需）
uv run python main_server.py

# 终端 3 — 智能体服务器（可选）
uv run python agent_server.py
```

补充说明：

- 想验证生产态的 Steam Auto-Cloud 主路径，仍应优先通过 Steam 或桌面启动器启动。现在 Windows / macOS / Linux 的源码模式在 Steam 运行且已登录时，也可以走 RemoteStorage bundle helper 做跨设备联调；但这条链路仍是开发态兼容路径，不是打包版主同步路径。
- 手动三服务模式更适合开发调试；当前 `main_server` 会在需要时兜底导入快照，并尝试通知 `memory_server` reload。
- shutdown 不会再自动把运行时变化写回 `cloudsave/`。如果希望 Steam 上传新的角色数据，需要在退出前先到云存档管理页手动为对应角色生成或覆盖 staged snapshot。
- macOS 源码模式如果提示“Apple 无法验证 `SteamworksPy.dylib`”，通常是 Gatekeeper 在拦截未公证的本地动态库。先确认从项目根目录启动；如果仍被拦截，可在项目根目录执行：

```bash
xattr -dr com.apple.quarantine SteamworksPy.dylib libsteam_api.dylib
codesign --force --sign - libsteam_api.dylib
codesign --force --sign - SteamworksPy.dylib
```

- 重新签名后再执行 `uv run python launcher.py` 或 `uv run python main_server.py`。

## 配置

1. 在浏览器中打开 `http://localhost:48911/api_key`
2. 选择你的核心 API 服务商
3. 输入你的 API 密钥
4. 点击保存

或者，在启动前设置环境变量：

```bash
export NEKO_CORE_API_KEY="sk-your-key"
export NEKO_CORE_API="qwen"
uv run python main_server.py
```

## 替代方案：pip 安装

如果你更喜欢 pip 而非 uv：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python memory_server.py
python main_server.py
```

## 验证

打开 `http://localhost:48911`，你应该能看到角色界面。
