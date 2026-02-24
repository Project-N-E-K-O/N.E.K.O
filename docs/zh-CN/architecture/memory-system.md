# 记忆系统

N.E.K.O. 的记忆系统提供跨会话的持久化上下文，使角色能够记住过去的对话、用户偏好和不断发展的关系。

## 存储层级

| 层级 | 存储方式 | 保留策略 | 访问模式 |
|------|---------|---------|---------|
| **近期记忆** | JSON 文件（`recent_*.json`） | 滑动窗口 | 直接读取，按角色分离 |
| **时间索引原文** | SQLite（`time_indexed_original`） | 永久保留 | 时间范围查询 |
| **时间索引压缩** | SQLite（`time_indexed_compressed`） | 永久保留 | 时间范围查询 |
| **语义记忆** | 向量嵌入（`text-embedding-v4`） | 永久保留 | 相似度搜索 |

## 记忆如何融入对话

1. 新会话开始时，系统加载**近期记忆**（最近 N 条消息）作为即时上下文。
2. **语义搜索**根据当前话题检索相关的历史对话。
3. **时间索引查询**为时间引用提供时序上下文（"昨天"、"上周"）。
4. 所有检索到的记忆作为上下文注入到 LLM 系统提示词中。

## 压缩流水线

旧对话会被定期压缩以节省上下文窗口空间：

```
Raw conversation ──> Summary model (qwen-plus) ──> Compressed summary
                                                        │
                                                   Stored in time_indexed_compressed
```

`ROUTER_MODEL`（默认：`qwen-plus`）决定哪些记忆值得完整保留、哪些应该压缩。

## 记忆审阅

用户可以在 `http://localhost:48911/memory_browser` 浏览和修正已存储的记忆。这有助于解决：

- 被当作"记忆"存储的模型幻觉
- 角色内化的错误事实
- 对话摘要中的重复模式

## API 端点

完整的端点参考请参阅[记忆 REST API](/zh-CN/api/rest/memory)。

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/memory/recent_files` | GET | 列出所有记忆文件 |
| `/api/memory/recent_file` | GET | 获取特定记忆文件内容 |
| `/api/memory/recent_file/save` | POST | 保存更新后的记忆 |
| `/api/memory/update_catgirl_name` | POST | 跨记忆重命名角色 |
| `/api/memory/review_config` | GET/POST | 记忆审阅设置 |
