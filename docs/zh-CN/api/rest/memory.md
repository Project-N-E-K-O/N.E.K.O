# 记忆 API

**前缀：** `/api/memory`

管理对话记忆文件和回顾配置。

## 近期记忆文件

### `GET /api/memory/recent_files`

列出记忆存储中的所有 `recent_*.json` 文件。

### `GET /api/memory/recent_file`

获取特定记忆文件的内容。

**查询参数：** `filename` — 记忆文件名称。

### `POST /api/memory/recent_file/save`

保存更新后的记忆文件。

**请求体：**

```json
{
  "filename": "recent_character_name.json",
  "catgirl_name": "character_name",
  "chat_history": [
    { "role": "user", "content": "Hello!" },
    { "role": "assistant", "content": "Hi there!" }
  ]
}
```

::: info
角色名称通过支持 CJK 字符的正则表达式进行验证。聊天记录条目会验证必填字段。
:::

## 名称管理

### `POST /api/memory/update_catgirl_name`

在所有记忆文件中更新角色名称。

**请求体：**

```json
{
  "old_name": "old_character_name",
  "new_name": "new_character_name"
}
```

## 回顾配置

### `GET /api/memory/review_config`

获取记忆回顾配置（压缩计划、保留设置）。

### `POST /api/memory/review_config`

更新记忆回顾配置。
