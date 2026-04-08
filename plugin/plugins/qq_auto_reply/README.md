# QQ 自动回复插件

通过 OneBot 协议连接 QQ，实现基于权限的智能自动回复功能。支持私聊和群聊消息处理，集成 AI 对话能力。

## 功能特性

- **OneBot 协议支持**：利用 NapCat 的 OneBot 实现
- **多级权限管理**：支持 admin、trusted、normal 三级用户权限
- **群聊权限控制**：支持 trusted、normal 两级群聊权限
- **AI 对话集成**：使用 OmniOfflineClient 生成智能回复
- **记忆系统同步**：管理员对话自动同步到 Memory Server
- **转述功能**：普通用户消息可概率转述给管理员
- **昵称管理**：支持为用户设置自定义称呼
- **断线自动重连**：WebSocket 断开后指数退避自动重连（1s → 2s → … 最长 30s）

## 安装依赖

插件依赖以下 Python 包（已在 `pyproject.toml` 中定义）：

```toml
dependencies = [
  "N.E.K.O",
  "websockets>=12.0",
  "httpx>=0.27.0",
  "tomli>=2.0.0",
  "tomli-w>=1.0.0",
]
```

使用 uv 或 pip 安装：

```bash
# 使用 uv（推荐）
uv pip install -e .

# 或使用 pip
pip install -e .
```

## 配置说明

编辑 `plugin.toml` 文件进行配置：

```toml
[qq_auto_reply]
# OneBot 服务地址（WebSocket）
onebot_url = "ws://127.0.0.1:3001"

# OneBot 访问令牌（可选）
token = "your_token_here"

# 信任用户列表
trusted_users = [
    { qq = "123455555", level = "admin" },
    { qq = "123456789", level = "trusted", nickname = "狗狗" },
    { qq = "987654321", level = "normal" },
]

# 信任群聊列表
trusted_groups = [
    { group_id = "146678866", level = "trusted" },
    { group_id = "123456789", level = "normal" },
]

# Normal 权限转述概率（0.0-1.0）
normal_relay_probability = 0.1
```

### 配置项说明

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `onebot_url` | string | OneBot 服务的 WebSocket 地址 |
| `token` | string | OneBot 访问令牌（如果服务端需要） |
| `trusted_users` | array | 信任用户列表，包含 QQ 号、权限等级和昵称 |
| `trusted_groups` | array | 信任群聊列表，包含群号和权限等级 |
| `normal_relay_probability` | float | 普通用户消息转述给管理员的概率 |

## 权限等级

### 用户权限

| 等级 | 说明 | 行为 |
|------|------|------|
| `admin` | 管理员 | 私聊直接回复，对话同步到记忆系统，称呼为"主人" |
| `trusted` | 信任用户 | 私聊直接回复，对话不同步记忆，可设置昵称 |
| `normal` | 普通用户 | 不直接回复，概率转述给管理员 |
| `none` | 未授权 | 忽略消息 |

### 群聊权限

| 等级 | 说明 | 行为 |
|------|------|------|
| `trusted` | 信任群聊 | 响应 @ 机器人的消息，生成 AI 回复 |
| `normal` | 普通群聊 | 不响应 @，概率转述给管理员 |
| `none` | 未授权 | 忽略消息 |

## 使用教程

### 1. 启动方式

启动顺序如下：

1. 在插件管理面板手动启动 `qq_auto_reply`
2. 插件初始化权限管理器和 QQ 客户端
3. 插件自动在前台启动 NapCat
4. 在NapCat的网络配置处设置`WS服务器`并把数据写入`plugin.toml`
5. 调用“启动自动回复”开始监听 QQ 消息

**注意事项：**
- 登录成功后，确保 NapCat 的 OneBot 服务已启动，再启用自动回复

### 2. 管理信任用户

#### 添加用户

对话栏输入"添加信任用户 \"123455555\",\"normal\""

```python
# 添加管理员
await plugin.add_trusted_user(qq_number="820040531", level="admin")

# 添加信任用户（带昵称）
await plugin.add_trusted_user(qq_number="123456789", level="trusted", nickname="小明")

# 添加普通用户
await plugin.add_trusted_user(qq_number="987654321", level="normal")
```

#### 移除用户

```python
await plugin.remove_trusted_user(qq_number="123456789")
```

#### 设置用户昵称

```python
# 设置昵称
await plugin.set_user_nickname(qq_number="123456789", nickname="小明")

# 清除昵称
await plugin.set_user_nickname(qq_number="123456789", nickname="")
```

### 3. 管理信任群聊

#### 添加群聊

对话栏输入"添加信任群聊 \"123455555\",\"normal\""

```python
# 添加信任群聊（响应 @）
await plugin.add_trusted_group(group_id="985066274", level="trusted")

# 添加普通群聊（仅转述）
await plugin.add_trusted_group(group_id="123456789", level="normal")
```

#### 移除群聊

```python
await plugin.remove_trusted_group(group_id="985066274")
```

### 4. 停止插件

```python
await plugin.stop_auto_reply()
```

**插件停止时会自动：**
- 断开 WebSocket 连接
- 执行 `NapCat.Shell/KillQQ.bat` 停止 NapCat 进程
- 清理所有资源

## 日志位置

```
...\N.E.K.O\log\plugins\qq_auto_reply
```

日志文件命名格式：`qq_auto_reply_YYYYMMDD_HHMMSS.log`

看到以下日志说明插件已正常启动并开始监听：

```
INFO | [qq_auto_reply] | Auto reply started
INFO | [Plugin-qq_auto_reply] Auto reply started
```

## 工作流程

### 私聊消息处理

```
接收消息 → 检查用户权限 → 根据权限处理
├─ admin:   生成 AI 回复 + 同步记忆
├─ trusted: 生成 AI 回复
├─ normal:  概率转述给管理员
└─ none:    忽略
```

### 群聊消息处理

```
接收消息 → 检查群聊权限 → 根据权限处理
├─ trusted: 检查是否 @ 机器人 → 生成 AI 回复
├─ normal:  概率转述给管理员
└─ none:    忽略
```

## 技术架构

### 核心模块

| 模块 | 文件 | 说明 |
|------|------|------|
| 插件主体 | `__init__.py` | 插件入口，消息处理逻辑，NapCat 生命周期管理 |
| QQ 客户端 | `qq_client.py` | OneBot 协议封装，WebSocket 通信，断线重连 |
| 用户权限 | `permission.py` | 用户权限管理，昵称管理 |
| 群聊权限 | `group_permission.py` | 群聊权限管理 |

### 依赖关系

```
QQAutoReplyPlugin
├─ QQClient (OneBot 通信 + 断线重连)
├─ PermissionManager (用户权限)
├─ GroupPermissionManager (群聊权限)
├─ OmniOfflineClient (AI 对话，per-user session)
└─ Memory Server (记忆同步，仅 admin 私聊)
```

## 常见问题

### 1. 无法连接到 OneBot 服务

**问题**：日志显示 `Failed to connect to OneBot`

**解决方案**：
- 检查 NapCat 是否正常运行（端口 3001）
- 确认 `onebot_url` 配置正确
- 验证 `token` 是否正确

### 2. 机器人不回复消息

**问题**：发送消息后没有回复

**解决方案**：
- 检查用户是否在 `trusted_users` 列表中
- 确认权限等级（normal 用户不会直接回复）
- 查看日志确认消息是否被接收
- 群聊中确保 @ 了机器人（trusted 群聊）

### 3. 记忆系统同步失败

**问题**：日志显示 `记忆同步失败`

**解决方案**：
- 确认 Memory Server 正在运行
- 注意：只有管理员的私聊对话才会同步记忆

### 4. 转述功能不工作

**问题**：普通用户消息没有转述给管理员

**解决方案**：
- 检查是否配置了管理员（level = "admin"）
- 确认 `normal_relay_probability` 设置（默认 0.1，即 10% 概率）
- 查看日志确认转述是否被触发

## 开发信息

- **作者**：ZhaiJiu
- **版本**：0.3.0
- **SDK 版本**：>=0.1.0,<0.3.0

## 许可证

本插件遵循 N.E.K.O 项目的许可证。
