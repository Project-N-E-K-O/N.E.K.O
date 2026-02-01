# Moltbot Bridge Plugin

N.E.K.O 插件,用于与 Moltbot Gateway 集成,实现双向消息转发和协议适配。

## 功能特性

- ✅ 接收来自 Moltbot 的消息请求
- ✅ 转发消息到 N.E.K.O Main Server
- ✅ 接收 N.E.K.O 的实时响应
- ✅ 推送响应回 Moltbot Gateway
- ✅ 支持多角色切换
- ✅ 会话管理和追踪

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                  Moltbot Gateway (18789)                     │
│                  WebSocket JSON-RPC Server                   │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │       Moltbot N.E.K.O Integration Plugin           │    │
│  │       (TypeScript, Moltbot 协议)                    │    │
│  │                                                      │    │
│  │  调用 N.E.K.O Plugin HTTP API                       │    │
│  │  POST http://localhost:48915/api/plugins/           │    │
│  │       moltbot_bridge/entries/send_to_neko/invoke    │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ HTTP POST (调用插件入口)
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              N.E.K.O Plugin Server (48915)                   │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │       Moltbot Bridge Plugin (Python)                │    │
│  │       (N.E.K.O 插件系统)                             │    │
│  │                                                      │    │
│  │  @plugin_entry("send_to_neko")                      │    │
│  │  - 接收 Moltbot 消息                                 │    │
│  │  - 转发到 N.E.K.O Main Server                       │    │
│  │  - 接收响应并推送到消息总线                          │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          │ HTTP/WebSocket
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                N.E.K.O Main Server (48911)                   │
│                                                              │
│  - ws://localhost:48911/ws/{character}                      │
│  - POST http://localhost:48911/send_message                 │
└─────────────────────────────────────────────────────────────┘
```

## 安装

### 1. 确保 N.E.K.O 插件系统已启动

```bash
cd /home/yun_wan/python_programe/N.E.K.O
python plugin/user_plugin_server.py
```

### 2. 插件会自动加载

插件配置文件 `plugin.toml` 中设置了 `auto_start = true`,插件会在服务器启动时自动加载。

## 配置

编辑 `plugin.toml` 文件:

```toml
[moltbot]
# Moltbot Gateway 地址
gateway_url = "http://localhost:18789"
gateway_token = "dev-test-token"

# N.E.K.O Main Server 地址
neko_main_url = "http://localhost:48911"
neko_main_ws = "ws://localhost:48911/ws"

# 启用调试日志
debug = false
```

## API 接口

### 1. `send_to_neko` - 发送消息到 N.E.K.O

**HTTP 调用**:
```bash
curl -X POST http://localhost:48915/api/plugins/moltbot_bridge/entries/send_to_neko/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好",
    "character": "小天",
    "session_key": "telegram:123456"
  }'
```

**参数**:
- `message` (string, 必需): 消息内容
- `character` (string, 可选): 角色名称,默认 "小天"
- `session_key` (string, 可选): 会话标识符

**返回**:
```json
{
  "ok": true,
  "data": {
    "success": true,
    "message": "Message forwarded to N.E.K.O",
    "character": "小天",
    "session_key": "telegram:123456",
    "response": "..."
  }
}
```

### 2. `get_status` - 获取插件状态

**HTTP 调用**:
```bash
curl http://localhost:48915/api/plugins/moltbot_bridge/entries/get_status/invoke \
  -H "Content-Type: application/json" \
  -d '{}'
```

**返回**:
```json
{
  "ok": true,
  "data": {
    "plugin_id": "moltbot_bridge",
    "status": "running",
    "config": {
      "gateway_url": "http://localhost:18789",
      "neko_main_url": "http://localhost:48911",
      "debug": false
    }
  }
}
```

## 开发计划

### 阶段 1: 基础消息转发 (当前)
- [x] 创建插件骨架
- [x] 实现基本的入口点
- [ ] 实现 HTTP 消息发送到 N.E.K.O
- [ ] 实现响应接收

### 阶段 2: WebSocket 实时通信
- [ ] 建立到 N.E.K.O Main Server 的 WebSocket 连接
- [ ] 实现实时消息接收
- [ ] 实现流式响应处理

### 阶段 3: 高级功能
- [ ] 多角色管理
- [ ] 会话状态追踪
- [ ] 音频响应支持
- [ ] 错误重试和断线重连

## 调试

### 查看插件日志

```bash
# 查看插件日志文件
tail -f /home/yun_wan/python_programe/N.E.K.O/plugin/plugins/moltbot_bridge/logs/*.log
```

### 启用调试模式

在 `plugin.toml` 中设置:
```toml
[moltbot]
debug = true
```

### 测试插件

```bash
# 测试发送消息
curl -X POST http://localhost:48915/api/plugins/moltbot_bridge/entries/send_to_neko/invoke \
  -H "Content-Type: application/json" \
  -d '{"message": "测试消息", "session_key": "telegram:123456"}'

# 查看插件状态
curl http://localhost:48915/api/plugins/moltbot_bridge/entries/get_status/invoke \
  -H "Content-Type: application/json" \
  -d '{}'
```

## 协议说明

### Moltbot → N.E.K.O

1. Moltbot 插件调用 N.E.K.O Plugin HTTP API
2. N.E.K.O 插件接收请求
3. 转发到 N.E.K.O Main Server (HTTP/WebSocket)
4. 接收 N.E.K.O 响应
5. 推送到 N.E.K.O 消息总线
6. 返回响应给 Moltbot

### N.E.K.O → Moltbot

1. N.E.K.O 通过 WebSocket 推送实时响应
2. 插件接收并解析响应
3. 推送到 N.E.K.O 消息总线
4. Moltbot 插件通过 WebSocket 订阅消息总线
5. 转发到 Moltbot Gateway
6. Moltbot 发送到最终用户

## 许可证

MIT License
