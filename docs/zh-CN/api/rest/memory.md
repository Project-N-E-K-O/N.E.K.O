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
  "chat": [
    { "role": "哥哥", "text": "Hello!" },
    { "role": "小天", "text": "Hi there!" }
  ]
}
```

角色名称由 `filename` 推导得出（通过 `extract_catgirl_name_from_recent_filename`），而不是从请求体读取。每个聊天条目都需要一个 `role` 字符串；消息文本从 `text` 字段读取。

注意：`role` 是 `recent.json` 中存储的说话者名字——人类发言用主人配置的名字，AI 发言用角色的名字——而不是 `"user"`/`"assistant"`。处理程序会原样写入每个条目的 `role`。

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
