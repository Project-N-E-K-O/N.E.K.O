# QQ 自动回复插件

通过 OneBot 协议连接 QQ，实现基于权限的智能自动回复功能。支持私聊和群聊消息处理，集成 AI 对话能力。

## 启动与引导

1. 下载onebot（推荐NapCat），以下用NapCat说明
打开
``` text 
https://github.com/NapNeko/NapCatQQ/releases
```
选择一个下载（推荐下载NapCat.Shell.zip）
2. 启动NapCat，打开NapCat配置页
3. 再左侧工具栏选择网络配置
4. 添加WS服务器，并点击启用，之后保存
5. 启动插件
6. 返回一次
7. 再打开本插件的面板
8. 按引导顺序完成

当前流程说明：
- 插件 `startup` 时会自动生成业务配置文件（如果还不存在）
- NapCat 会优先从你配置的执行目录启动；若未配置则回退到默认目录
- 登录二维码会在 NapCat 生成后复制到插件静态目录，前端点击“刷新二维码”即可重新同步并显示
- 前端 UI 内可直接管理：
  - OneBot 地址 / Token / PATH
  - NapCat 前台/后台启动
  - 信任用户 / 信任群聊
  - 自动回复启停


## 功能特性

- OneBot 协议支持：利用 NapCat 的 OneBot 实现
- 多级权限管理：支持 admin、trusted、normal 三级用户权限
- 群聊权限控制：支持 trusted、open、normal 三级群聊权限
- AI 对话集成：使用 OmniOfflineClient 生成智能回复
- 记忆系统同步：管理员对话自动同步到 Memory Server
- 转述功能：普通用户消息可概率转述给管理员
- 开放群模式：可在未 @ 机器人的情况下直接接话，复用临时上下文和人设卡
- 昵称管理：支持为用户设置自定义称呼
- 断线自动重连：WebSocket 断开后指数退避自动重连（1s → 2s → … 最长 30s）

## 配置说明

推荐直接在插件 UI 的 **QQ OneBot 服务配置** 区域完成设置；首次启动插件时会自动生成业务配置文件。当前常用项包括：

- `onebot_url`：OneBot 的 WebSocket 地址，默认 `ws://127.0.0.1:3001`
- `token`：OneBot 访问令牌
- `napcat_directory`：NapCat 执行目录
- `show_napcat_window`：`true` 为前台启动（显示 NapCat 控制台），`false` 为后台启动
- `trusted_users`：信任用户列表
- `trusted_groups`：信任群聊列表
- `normal_relay_probability`：普通消息转发给主人概率
- `truth_reply_probability`：开放群主动回复概率

### 配置项说明

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `onebot_url` | string | OneBot 服务的 WebSocket 地址 |
| `token` | string | OneBot 访问令牌（如果服务端需要） |
| `trusted_users` | array | 信任用户列表，包含 QQ 号、权限等级和昵称 |
| `trusted_groups` | array | 信任群聊列表，包含群号和权限等级 |
| `normal_relay_probability` | float | 普通用户/普通群聊消息转述给管理员的概率 |
| `truth_reply_probability` | float | `open` 群聊在未 @ 机器人的情况下触发直接回复的概率 |

### 群聊里的概率字段

`trusted_groups` 里的每个群除了 `group_id` 和 `level` 之外，还可以带两个可选概率字段：

- `normal_relay_probability`：只对 `level = normal` 的群生效。群消息命中“转述给主人”路径时，会用这个值覆盖全局默认转述概率。
- `open_reply_probability`：只对 `level = open` 的群生效。开放群里在没有 @ 机器人的情况下，会用这个值覆盖全局默认主动回复概率。

说明：
- 这两个字段都是 `0 ~ 1` 之间的小数。
- 留空时会沿用全局配置里的默认值。
- `trusted` 群不会使用这两个字段，因为 `trusted` 群只在 @ 机器人时直接进入正常回复逻辑。

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
| `trusted` | 信任群聊 | 仅响应 @ 机器人的消息，生成 AI 回复 |
| `open` | 开放群聊 | 无需 @ 即可直接回复；复用临时会话记忆与角色卡；不写入记忆库；不称呼发言人 |
| `normal` | 普通群聊 | 不响应 @，概率转述给管理员 |
| `none` | 未授权 | 忽略消息 |

## 额外补充

### 1. 主动发送消息

可以直接通过插件面板调用新增入口，让机器人先用 AI 生成人设化内容，再主动发给指定对象

说明：
- `message` 现在表示“给 AI 的提示内容”，不是最终原样直发文本
- 会复用现有角色人设与模型配置
- 私聊主动发送会读取记忆库上下文辅助生成，但不会把这次主动发送写回记忆库
- 群聊主动发送不会写入记忆库
- 私聊入口使用 `target` 参数：可以填写纯数字 QQ 号，或填写已配置在信任用户列表中的昵称
- `group_id` 必须是纯数字字符串
- `message` 不能为空
- 使用前需先启动自动回复并确保 OneBot 已连接，否则入口会直接报错

### 2. 停止插件

- 断开 WebSocket 连接
- 清理所有资源

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
- `trusted` 群聊中确保 @ 了机器人
- `open` 群聊无需 @，配置正确时会直接回复

### 3. 记忆系统同步失败

**问题**：日志显示 `记忆同步失败`

**解决方案**：
- 确认 Memory Server 正在运行
- 注意：只有管理员的私聊对话才会同步记忆
- 群聊（包括 `open`）只保留临时上下文，不会写入记忆库

### 4. 转述功能不工作

**问题**：普通用户消息没有转述给管理员

**解决方案**：
- 检查是否配置了管理员（level = "admin"）
- 确认 `normal_relay_probability` 设置（默认 0.1，即 10% 概率）
- 查看日志确认转述是否被触发

## 联系人

有任何问题请提交issue 或发送邮件到 zhaijiunknown@outlook.com

## 许可证

本插件遵循 N.E.K.O 项目的许可证。
