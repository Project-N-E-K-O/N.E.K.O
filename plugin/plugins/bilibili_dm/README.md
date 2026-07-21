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

## 配置

在插件管理器中打开「B站私信」面板完成配置。面板可以：

- 保存或更新 B站 Cookie
- 选择白名单、黑名单或开放回复模式
- 管理信任用户和管理员
- 开始或停止私信监听
- 查看脱敏后的凭据状态

配置保存在运行时插件数据目录的 `business_config.json`，不会写入仓库中的
`plugin.toml`。Windows 默认路径为：

```text
%LOCALAPPDATA%\N.E.K.O\plugins\bilibili_dm\data\business_config.json
```

该文件与微信集成插件采用相同的放置方式，使用原子写入避免文件损坏。Cookie
仍以明文保存在本机，请勿分享该文件，并确保系统账号和磁盘受到妥善保护。

### B站 Cookie

| 字段 | 类型 | 说明 |
|------|------|------|
| `sesdata` | string | B站 Cookie 中的 `SESSDATA`（必填，配置文件内部字段） |
| `bili_jct` | string | B站 Cookie 中的 `bili_jct`（CSRF Token，必填） |
| `buvid3` | string | B站 Cookie 中的 `buvid3` |
| `dedeuserid` | string | B站 Cookie 中的 `DedeUserID` |
| `ac_time_value` | string | B站 Cookie 中的 `ac_time_value` |

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

## 首次使用

1. 使用浏览器登录 [bilibili.com](https://www.bilibili.com)
2. 打开浏览器开发者工具（F12）→ Application → Cookies
3. 找到并复制以下字段的值：
   - `SESSDATA`（必填）
   - `bili_jct`（必填）
   - `buvid3`
   - `DedeUserID`
   - `ac_time_value`
4. 打开 B站私信插件面板并保存这些字段
5. 在面板中至少添加一个信任用户，或将权限模式改为 `open`
6. 点击「开始监听」

> Cookie 有效期有限，过期后需重新获取并在面板中更新。已保存的字段不会回显；
> 输入框留空表示保留原值。

## 依赖

- `bilibili_api` — B站 API 封装库
- `httpx` — 异步 HTTP 客户端（用于图片下载）

## 文件结构

```text
bilibili_dm/
├── __init__.py        # 插件主实现与面板入口
├── config_store.py    # 运行时业务配置存储
├── plugin.toml        # 插件清单（不保存凭据）
├── bili_client.py     # B站私信客户端封装
├── permission.py      # 权限管理模块
├── static/            # 插件前端面板
└── README.md          # 本文件
```

## 注意事项

- 图片下载需要携带 B站 Cookie 才能正常访问，客户端内部已自动处理
- 使用 `Session` 轮询机制，约每 6 秒检查一次新消息
- 管理员对话会自动同步到 Memory Server，用于构建连续对话上下文
- 超过 5 分钟空闲的会话会自动回收并结算记忆
