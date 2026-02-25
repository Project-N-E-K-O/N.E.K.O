# 手动搭建

适用于在任何平台上进行开发和自定义。

## 前置条件

- Python 3.11（必须是此版本，不支持 3.12+）
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 包管理器
- Git

## 安装

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

## 运行

在不同终端中启动所需的服务器：

```bash
# Terminal 1 — Memory server (required)
uv run python memory_server.py

# Terminal 2 — Main server (required)
uv run python main_server.py

# Terminal 3 — Agent server (optional)
uv run python agent_server.py
```

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
