# 记忆 API

**前缀：** `/api/memory`

这是主服务器为记忆浏览器和设置页面提供的 API，涵盖近期记忆编辑、记忆功能开关、角色记忆重命名，以及由用户主动触发的遗留存储清理。它不是进程内 [记忆服务器 API](/zh-CN/api/memory-server) 的通用代理。

所有路由都不带末尾斜杠。云存储处于维护态或只读态时，写操作可能返回 `409`。

## 端点一览

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/memory/recent_files` | 列出近期记忆的逻辑文件名 |
| `GET` | `/api/memory/recent_file` | 读取一个近期记忆文件 |
| `POST` | `/api/memory/recent_file/save` | 替换一个角色的近期记忆历史 |
| `POST` | `/api/memory/update_catgirl_name` | 重命名角色的记忆存储 |
| `GET` | `/api/memory/review_config` | 读取近期记忆自动复核开关 |
| `POST` | `/api/memory/review_config` | 更新近期记忆自动复核开关 |
| `GET` | `/api/memory/powerful_memory_config` | 读取强力记忆开关 |
| `POST` | `/api/memory/powerful_memory_config` | 更新强力记忆开关，并执行必要迁移 |
| `GET` | `/api/memory/legacy/scan` | 只读扫描用户可见的遗留记忆根目录 |
| `POST` | `/api/memory/legacy/purge` | 删除从遗留根目录中明确选中的条目 |

## 近期记忆文件

为兼容旧版记忆浏览器，API 仍使用逻辑文件名 `recent_<角色>.json`。当前存储会把它解析到 `memory/<角色>/recent.json`；迁移期间仍可读取旧的扁平文件。

### `GET /api/memory/recent_files`

无参数。

该路由会搜索活动记忆根目录和项目记忆根目录，去重逻辑文件名并排序返回。

```json
{
  "files": ["recent_小天.json", "recent_小夜.json"]
}
```

### `GET /api/memory/recent_file`

**查询参数**

| 名称 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | 字符串 | 是 | 例如 `recent_小天.json`；拒绝路径分隔符和 `..` |

`content` 是文件中的 UTF-8 JSON 文本，不是已经解析的消息数组。

```json
{
  "content": "[{\"type\":\"human\",\"data\":{...}}]"
}
```

错误响应为 `{"success": false, "error": "..."}`：文件名非法返回 `400`，逻辑文件无法解析返回 `404`。

### `POST /api/memory/recent_file/save`

替换所选角色的近期历史，并取消该角色正在执行的复核任务，使手动编辑立即生效。

**请求体**

```json
{
  "filename": "recent_小天.json",
  "chat": [
    { "role": "human", "text": "你好！" },
    { "role": "ai", "text": "晚上好！" }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `filename` | 字符串 | 是 | 必须匹配 `recent_<角色>.json`；角色名从该字段推导 |
| `chat` | 数组 | 是 | 替换后的历史，最多 10,000 条 |
| `chat[].role` | 字符串 | 是 | 写入的消息类型，通常为 `human`、`ai` 或 `system` |
| `chat[].text` | 字符串 | 否 | 消息文本；默认为空字符串 |

单条消息文本上限为 32,768 个字符，请求中全部消息文本合计上限为 2,097,152 个字符。聊天条目中的未知字段不会持久化。

成功响应：

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天"
}
```

校验失败返回 `400` 和 `success: false`。存储失败返回 `success: false` 及 `error`；云存储维护态返回 `409`。

## 角色记忆重命名

### `POST /api/memory/update_catgirl_name`

通过共享的角色记忆迁移工具重命名角色的完整记忆存储，而不只是近期历史文件。

```json
{
  "old_name": "旧名字",
  "new_name": "新名字"
}
```

两个字段都是必填字符串。历史 `old_name` 可以包含点号；`new_name` 使用当前角色名规则，且不能是保留路由名。

```json
{
  "success": true,
  "changed": true,
  "exists_after": true
}
```

名称缺失或非法返回 `400`。存储不可写时可能返回 `409`。

## 记忆功能开关

### `GET /api/memory/review_config`

返回是否启用近期记忆的自动复核和修正。配置不存在时默认为 `true`。

```json
{ "enabled": true }
```

### `POST /api/memory/review_config`

```json
{ "enabled": false }
```

该路由把 `recent_memory_auto_review` 写入 `core_config.json`。

```json
{ "success": true, "enabled": false }
```

失败响应为 `{"success": false, "error": "..."}`。存储维护态可能返回 `409`。

### `GET /api/memory/powerful_memory_config`

返回 `powerful_memory_enabled` 设置。已有安装没有显式配置时默认为 `true`。

```json
{ "enabled": true }
```

### `POST /api/memory/powerful_memory_config`

```json
{ "enabled": false }
```

强力记忆开关控制证据驱动的 LLM 路径，包括信号分析、晋升时合并、反驳检查、负向目标检查、事实去重和 persona 修正。关闭强力记忆后，轻量反馈路径仍可用。

从 `ON` 切换到 `OFF` 时，会先请求记忆服务器进程重置已确认反思的计时锚点，避免旧的 confirmed 条目被时间驱动的降级路径立即晋升。只有迁移成功后才保存配置。

成功响应：

```json
{ "success": true, "enabled": false }
```

迁移或持久化失败：

```json
{ "success": false, "error": "migration HTTP 409" }
```

## 遗留记忆清理

遗留清理是明确的两步操作：先扫描，再提交选中的绝对路径。扫描不会迁移或删除任何数据。

### `GET /api/memory/legacy/scan`

无参数。

响应会标识活动运行时记忆目录之外的候选根目录，并描述每个根目录的直接子项。无法计算大小或超过安全扫描上限时，`size_bytes` 为 `-1`。

```json
{
  "success": true,
  "runtime_memory_dir": "C:\\...\\memory",
  "legacy_roots": [
    {
      "root": "C:\\...\\old-root\\memory",
      "source": "legacy_app_root",
      "exists": true,
      "entries": [
        {
          "name": "小天",
          "path": "C:\\...\\old-root\\memory\\小天",
          "is_dir": true,
          "size_bytes": 12345,
          "is_unlinked": false,
          "runtime_has_same_name": true
        }
      ]
    }
  ],
  "total_entries": 1,
  "total_size_bytes": 12345
}
```

意外的扫描失败返回 `500` 和 `success: false`。

### `POST /api/memory/legacy/purge`

这是破坏性操作。只应提交最近一次扫描作为条目返回的路径。

```json
{
  "paths": [
    "C:\\...\\old-root\\memory\\小天"
  ]
}
```

`paths` 必须是非空的绝对路径数组。每个路径都必须严格位于当前识别出的遗留根目录之下。该路由拒绝相对路径、`..` 段、根目录本身和活动运行时记忆目录。目标不存在会被视为已删除，因此重试是幂等的。

删除按条目尽力执行，因此成功请求可以同时包含 `removed` 和 `errors`：

```json
{
  "success": true,
  "removed": ["C:\\...\\old-root\\memory\\小天"],
  "errors": [
    { "path": "C:\\...\\not-allowed", "error": "..." }
  ]
}
```

请求体非法返回 `400`；没有可识别的遗留根目录返回 `409`；初始化失败返回 `500`。
