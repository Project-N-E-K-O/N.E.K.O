# 记忆服务器 API

**端口：** 48912（内部）

记忆服务器作为独立进程运行，处理所有持久化记忆操作。它不面向外部直接访问 — 主服务器代理记忆相关的请求。

## REST 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 服务健康检查（N.E.K.O. 签名） |
| `/cache/{lanlan_name}` | POST | 轻量追加（无 LLM 处理） |
| `/process/{lanlan_name}` | POST | 完整处理（近期 + 时间 + 语义 + 审阅） |
| `/renew/{lanlan_name}` | POST | 热切换续期 |
| `/get_recent_history/{lanlan_name}` | GET | 格式化的 LLM 提示词上下文 |
| `/search_for_memory/{lanlan_name}/{query}` | GET | 语义搜索 |
| `/get_settings/{lanlan_name}` | GET | 角色设置 JSON |
| `/new_dialog/{lanlan_name}` | GET | 新对话上下文（清理括号） |
| `/reload` | POST | 重新加载所有组件 |
| `/cancel_correction/{lanlan_name}` | POST | 中止审阅任务 |

## 存储层级

| 层级 | 用途 |
|------|------|
| 近期记忆 | 每个角色最近 N 条消息（JSON 文件） |
| `time_indexed_original` | 完整对话历史（SQLite） |
| `time_indexed_compressed` | 压缩后的对话历史（SQLite） |
| 向量嵌入 | 用于语义搜索的 Embedding 存储 |
| 重要设置 | LLM 提取的角色偏好（JSON 文件） |

## 关键组件

| 组件 | 用途 |
|------|------|
| `RecentHistoryManager` | 滑动窗口近期记忆管理 |
| `CompressedRecentHistoryManager` | LLM 压缩和摘要 |
| `SemanticMemory` | 向量嵌入和混合搜索 |
| `TimeIndexManager` | SQLite 时间索引存储 |
| `ImportantSettingsManager` | LLM 提议/验证角色设置 |

## 使用的模型

| 任务 | 默认模型 |
|------|----------|
| 嵌入 | `text-embedding-v4` |
| 摘要 | `qwen-plus` (SUMMARY_MODEL) |
| 路由 | `qwen-plus` (ROUTER_MODEL) |
| 重排序 | `qwen-plus` (RERANKER_MODEL) |

## 通信方式

主服务器通过 HTTP 请求和持久化同步连接线程（`cross_server.py`）与记忆服务器通信。
