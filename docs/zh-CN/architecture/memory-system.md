# 记忆系统

N.E.K.O. 的记忆系统提供跨会话的持久化上下文，使角色能够记住过去的对话、用户偏好和不断发展的关系。

## 存储层级

| 层级 | 存储方式 | 保留策略 | 访问模式 |
|------|---------|---------|---------|
| **近期记忆** | JSON 文件（`recent_*.json`） | 滑动窗口（最多 10 条消息） | 直接读取，按角色分离 |
| **时间索引原文** | SQLite（`time_indexed_original`） | 永久保留 | 时间范围查询 |
| **时间索引压缩** | SQLite（`time_indexed_compressed`） | 永久保留 | 时间范围查询 |
| **语义记忆** | 向量嵌入（`text-embedding-v4`） | 永久保留 | 混合搜索 + LLM 重排序 |
| **重要设置** | JSON 文件（按角色） | 永久保留 | LLM 提取的偏好 |

## 记忆如何融入对话

1. 新会话开始时，系统加载**近期记忆**（最近 N 条消息）作为即时上下文。
2. **语义搜索**（`hybrid_search`）根据当前话题检索相关的历史对话，合并原始和压缩结果并进行 LLM 重排序。
3. **时间索引查询**为时间引用提供时序上下文（"昨天"、"上周"）。
4. **重要设置**（角色偏好、关系、知识）被加载。
5. 所有检索到的记忆作为上下文注入到 LLM 系统提示词中，带括号清理以获得更干净的格式。

## 压缩流水线

当近期历史超过 `max_history_length`（默认：10 条消息）时，对话历史自动被压缩：

```
原始对话 ──> LLM 摘要（"对话摘要" JSON 格式）
                    │
               若 > 500 字符 ──> 进一步压缩
                    │
               合并为 SystemMessage(memorandum)
                    │
               存储到 time_indexed_compressed
```

压缩在失败时使用 3 次指数退避重试。`CompressedRecentHistoryManager` 处理完整生命周期。

## 重要设置

`ImportantSettingsManager` 从对话中提取和维护角色特定的知识：

1. **LLM 提议器**：分析对话以提取设置（性格特征、关系、偏好）
2. **LLM 验证器**：解决新旧设置之间的矛盾
3. **保留字段**自动被剥离：`system_prompt`、`live2d`、`voice_id`、创意工坊数据、渲染配置

## 记忆审阅

`review_history()` 方法运行异步 LLM 审计以检测：

- 已存储记忆中的矛盾和逻辑错误
- 被当作"记忆"存储的模型幻觉
- 角色内化的错误事实
- 对话摘要中的重复模式

审阅任务按角色进行，可通过 `cancel_correction/{lanlan_name}` 取消以防止过时的上下文修正。用户也可以在 `http://localhost:48911/memory_browser` 浏览记忆。

## API 端点

主服务器端点完整参考请参阅[记忆 REST API](/zh-CN/api/rest/memory)。

记忆服务器暴露的内部端点：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/cache/{lanlan_name}` | POST | 轻量追加（无 LLM） |
| `/process/{lanlan_name}` | POST | 完整处理（近期 + 时间 + 语义 + 审阅） |
| `/renew/{lanlan_name}` | POST | 热切换续期 |
| `/get_recent_history/{lanlan_name}` | GET | 格式化的 LLM 提示词上下文 |
| `/search_for_memory/{lanlan_name}/{query}` | GET | 语义搜索 |
| `/get_settings/{lanlan_name}` | GET | 角色设置 JSON |
| `/new_dialog/{lanlan_name}` | GET | 新对话上下文（清理括号） |
| `/reload` | POST | 重新加载所有组件 |
| `/cancel_correction/{lanlan_name}` | POST | 中止审阅任务 |
