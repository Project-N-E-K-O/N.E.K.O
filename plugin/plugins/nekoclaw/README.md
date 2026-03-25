# NekoClaw Plugin

连接 N.E.K.O 与 NekoClaw 的多模态桥接插件。

## 功能

- 发送文本消息到 NekoClaw
- 发送图文混合消息到 NekoClaw
- 支持多模态消息（图片、视频、音频、文件）
- 检查 NekoClaw 连接状态

## 安装步骤

### 1. 安装 NekoClaw 自定义渠道

将 `neko_channel.py` 复制到运行时的自定义渠道目录：

```bash
mkdir -p ~/.copaw/custom_channels
cp neko_channel.py ~/.copaw/custom_channels/
```

### 2. 配置 NekoClaw 运行时

编辑 `~/.copaw/config.json`，在 `channels` 部分添加：

```json
{
  "channels": {
    "neko": {
      "enabled": true,
      "bot_prefix": "",
      "host": "127.0.0.1",
      "port": 8089
    }
  }
}
```

> **说明**：运行时主服务运行在 8088 端口，`neko` 通信渠道单独运行在 8089 端口。
> 默认使用 `127.0.0.1` 仅监听本机，更安全。
> 只有在你确实需要局域网其他设备访问时，才手动改成 `0.0.0.0`，并确认当前网络环境可信，因为 `/neko/send` 默认没有额外鉴权。

### 3. 安装 aiohttp（如果尚未安装）

```bash
pip install aiohttp
```

### 4. 重启运行时服务

```bash
copaw app
```

### 5. N.E.K.O 插件已就位

插件位于 `plugin/plugins/nekoclaw/`，启动 N.E.K.O 后会自动加载。

## 插件入口点

| 入口点 | 说明 |
|--------|------|
| `chat` | 发送文本消息 |
| `chat_with_image` | 发送图文消息 |
| `chat_multimodal` | 发送多模态消息 |
| `check_connection` | 检查连接状态 |

## 配置选项

在 N.E.K.O 的插件配置中可以设置：

```json
{
  "nekoclaw": {
    "url": "http://127.0.0.1:8089",
    "timeout": 60.0,
    "default_sender_id": "neko_user"
  }
}
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `url` | NekoClaw neko 渠道地址 | `http://127.0.0.1:8089` |
| `timeout` | 请求超时（秒） | `60.0` |
| `default_sender_id` | 默认发送者 ID | `neko_user` |

## 测试

### 检查连接

```bash
curl http://127.0.0.1:8089/health
# 期望返回: {"status": "healthy", "channel": "neko"}
```

### 发送消息

```bash
curl -X POST http://127.0.0.1:8089/neko/send \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好",
    "sender_id": "test_user",
    "session_id": "test_session"
  }'
```
