# 开发环境搭建

## 克隆仓库

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

## 安装依赖

```bash
uv sync
```

这会将所有 Python 依赖安装到托管的虚拟环境中。项目要求 Python 3.11。

## 启动服务器

N.E.K.O. 以多个协作服务器的形式运行。至少需要启动**主服务器**和**记忆服务器**：

```bash
# 终端 1 — 记忆服务器
uv run python memory_server.py

# 终端 2 — 主服务器
uv run python main_server.py
```

可选地，启动智能体服务器以执行后台任务：

```bash
# 终端 3 — 智能体服务器（可选）
uv run python agent_server.py
```

## 配置 API 密钥

主服务器启动后，打开 Web UI 配置 API 密钥：

```
http://localhost:48911/api_key
```

选择你偏好的核心 API 提供商并输入 API 密钥。各提供商的详细信息请参阅 [API 提供商](/zh-CN/config/api-providers)。

## 验证安装

打开主界面：

```
http://localhost:48911
```

你应该能看到带有 Live2D 模型的角色界面。尝试发送一条文字消息或开启语音会话，以验证一切正常运行。

## 默认端口

| 服务器 | 端口 | 用途 |
|--------|------|------|
| 主服务器 | 48911 | Web UI、REST API、WebSocket |
| 记忆服务器 | 48912 | 记忆存储与检索 |
| 监控服务器 | 48913 | 状态监控 |
| 智能体/工具服务器 | 48915 | 智能体任务执行 |
| 插件服务器 | 48916 | 用户插件 |

## 运行测试

```bash
uv run pytest
```

测试套件的结构和运行特定测试类别的方法请参阅 `tests/README.md`。
