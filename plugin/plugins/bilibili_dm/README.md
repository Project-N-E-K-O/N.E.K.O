# B站私信 N.E.K.O 插件

通过 `bilibili_api` 监听 B站私信，使用 N.E.K.O AI 自动回复。

## 功能特性

| 消息类型 | 接收 | 发送 |
|---------|------|------|
| 文本 (TEXT) | ✅ | ✅ |
| 图片 (PICTURE) | ✅ | ✅ |
| 分享视频 (SHARE_VIDEO) | ✅ | — |

### 详细说明

- **接收文本消息**：直接提取文本内容，交给 AI 生成回复
- **接收图片消息**：通过 Cookie 鉴权下载图片，转为 Base64 传递给 AI
- **接收分享视频**：获取视频标题、UP主、播放量等信息，拼接为富文本
- **发送文本消息**：通过 `send_msg` 发送纯文本回复
- **发送图片消息**：支持 URL 和 Base64 两种图片来源
- **用户昵称解析**：通过 `User.get_user_info()` API 获取真实昵称，带内存缓存
- **权限管理**：支持 admin / trusted / normal 三级权限控制
- **记忆同步**：管理员对话自动同步到 Memory Server

## 配置项

通过 WebUI 或 `plugin.toml` 配置以下字段：

### B站 Cookie

| 字段 | 类型 | 说明 |
|------|------|------|
| `sesdata` | string | B站 Cookie 中的 `SESSDATA`（必填） |
| `bili_jct` | string | B站 Cookie 中的 `bili_jct`（CSRF Token） |
| `buvid3` | string | B站 Cookie 中的 `buvid3` |
| `dedeuserid` | string | B站 Cookie 中的 `DedeUserID` |
| `ac_time_value` | string | B站 Cookie 中的 `ac_time_value` |

### 权限配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `trusted_users` | list | 信任用户列表，格式: `[{uid = "12345", level = "admin"}]` |

### 权限等级

| 等级 | 说明 |
|------|------|
| `admin` | 管理员，享有最高权限，使用完整记忆上下文 |
| `trusted` | 信任用户，可获得 AI 回复 |
| `normal` | 普通用户，不自动回复 |

## 插件入口

| Entry ID | 名称 | 说明 |
|----------|------|------|
| `start_listening` | 开始监听 | 启动 B站私信监听并自动回复 |
| `stop_listening` | 停止监听 | 停止监听 B站私信 |
| `send_message` | 发送私信 | 向指定 B站用户发送一条私信 |
| `add_trusted_user` | 添加信任用户 | 添加信任用户到白名单 |
| `remove_trusted_user` | 移除信任用户 | 从白名单中移除用户 |
| `set_user_nickname` | 设置用户昵称 | 为信任用户设置专属称呼 |
| `list_trusted_users` | 列出信任用户 | 列出所有信任用户 |

## 获取 Cookie

1. 使用浏览器登录 [bilibili.com](https://www.bilibili.com)
2. 打开浏览器开发者工具（F12）→ Application → Cookies
3. 找到并复制以下字段的值：
   - `SESSDATA`（必填）
   - `bili_jct`
   - `buvid3`
   - `DedeUserID`
   - `ac_time_value`

> ⚠️ **注意**：Cookie 有效期有限，过期后需重新获取并更新配置。

## 依赖

- `bilibili_api` — B站 API 封装库
- `httpx` — 异步 HTTP 客户端（用于图片下载）

## 文件结构

```
bilibili_dm/
├── __init__.py       # 插件主实现
├── plugin.toml       # 插件配置
├── bili_client.py    # B站私信客户端封装
├── permission.py     # 权限管理模块
├── README.md         # 本文件
└── KiraAI_bili_dm_adapter/  # KiraAI 参考适配器
```

## 注意事项

- 图片下载需要携带 B站 Cookie 才能正常访问，客户端内部已自动处理
- 使用 `Session` 轮询机制，约每 6 秒检查一次新消息
- 管理员对话会自动同步到 Memory Server，用于构建连续对话上下文
- 超过 5 分钟空闲的会话会自动回收并结算记忆
