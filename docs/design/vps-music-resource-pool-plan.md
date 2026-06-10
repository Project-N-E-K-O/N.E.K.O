# VPS Music Resource Pool Plan

## 1. 背景与目标

当前音乐分享链路依赖本地 `utils/music_crawlers.py` 搜索音乐资源。主动对话会先根据上下文生成音乐关键词，再搜索候选曲目，最后通过 `[MUSIC]` 标签和 APlayer 完成分享与播放。

本计划将音乐分享作为 VPS 资源池替换的第一个测试点：VPS 负责音乐资源持久化和查询，N.E.K.O. 本地只作为资源消费者，不再默认直接爬取音乐源。

第一阶段成功标准：主动搭话生成音乐关键词后，N.E.K.O. 能从 VPS 获取音乐候选，并通过现有 APlayer 链路播放。

## 2. VPS 职责

VPS 的定位是远端音乐资源池和持久化数据库，不是 N.E.K.O. 主程序运行环境。

- 资源持久化：保存曲名、歌手、播放 URL、封面、来源平台、标签、语言、地区、可播放状态和更新时间。
- 资源查询：支持按 keyword、tag、region、language 查询，也支持无关键词随机推荐。
- 资源维护：负责去重、失效检测、来源记录、原始数据留存和资源刷新。
- 使用记录：记录 N.E.K.O. 请求、分享、播放完成等事件，用于短期避重和后续推荐优化。

## 3. 非目标

- 第一阶段不迁移新闻、视频、个人动态等其他外部内容通道。
- 不迁移本地 SQLite 记忆库；本地 SQLite 继续负责角色记忆和时间索引。
- 不让 N.E.K.O. 本地直接连接 PostgreSQL；本地只通过 HTTPS API 访问 VPS。
- 第一版不实现复杂个性化推荐算法，先完成可存储、可查询、可播放、可降级的闭环。

## 4. 架构

```text
音乐来源 / 人工导入 / 后续采集任务
        ↓
      VPS API
        ↓
 PostgreSQL 音乐资源池
        ↓
 /v1/music/search 或 /v1/music/random
        ↓
 N.E.K.O. 主动对话
        ↓
 [MUSIC] + APlayer
```

VPS 负责资源池和数据库能力。N.E.K.O. 负责根据对话上下文决定是否分享音乐，以及如何自然地把音乐推荐给用户。

## 5. 数据库设计

VPS 使用 PostgreSQL 作为正式资源池数据库。

### `music_tracks`

保存音乐资源本体。

- `id`: 主键。
- `name`: 曲名。
- `artist`: 歌手或作者。
- `url`: 可播放音频 URL。
- `cover`: 封面 URL。
- `source`: 来源平台，例如 `netease`、`qqmusic`、`manual`。
- `source_id`: 平台原始 ID，可为空。
- `dedupe_key`: 去重键，数据库层加唯一约束。
- `language`: 语言，例如 `zh-CN`、`en`、`ja`。
- `region`: 地区，例如 `china`、`global`。
- `tags`: 标签数组，例如 `chill`、`night`、`anime`。
- `playable`: 是否确认可播放。
- `quality`: 音质或资源质量标记。
- `score`: 排序分数，可由热度、人工权重、播放反馈综合生成。
- `fetched_at`: 首次获取时间。
- `updated_at`: 最近更新时间。
- `raw_json`: 来源原始数据，便于排查。

### `music_usage_events`

保存 N.E.K.O. 对资源池的使用记录。

- `id`: 主键。
- `track_id`: 对应曲目 ID。
- `event_type`: `requested`、`shared`、`played`、`skipped` 等。
- `keyword`: 当次查询关键词。
- `character_name`: 触发分享的角色名。
- `created_at`: 事件时间。

### `music_source_runs`

保存采集、导入或维护任务记录。

- `id`: 主键。
- `source`: 来源或任务名。
- `status`: `success`、`partial`、`failed`。
- `started_at`: 开始时间。
- `finished_at`: 结束时间。
- `items_found`: 发现数量。
- `items_inserted`: 新增数量。
- `error`: 错误信息。

### 去重规则

优先使用 `source + source_id` 生成 `dedupe_key`。如果来源没有稳定 ID，则使用标准化后的 `name + artist + url`。标准化应至少包括 trim、大小写归一、空白归一和常见全半角差异处理。

## 6. VPS API

### Health

```http
GET /health
```

返回服务和数据库状态。

### Search Music

```http
GET /v1/music/search?keyword=lofi&limit=5&lang=zh-CN&region=china
```

按关键词查询音乐资源。`keyword` 为空时可以降级为热门或随机候选，但建议客户端优先调用 `/v1/music/random` 表达无关键词意图。

### Random Music

```http
GET /v1/music/random?limit=5&lang=zh-CN&region=china
```

返回可播放的随机候选，适用于主动对话没有明确关键词但仍允许音乐分享的场景。

### Usage Event

```http
POST /v1/music/usage
```

记录请求、分享、播放完成或跳过事件。

### Response Shape

API 返回保持 APlayer 兼容，便于 N.E.K.O. 复用现有前端播放链路。

```json
{
  "success": true,
  "data": [
    {
      "name": "Song Name",
      "artist": "Artist",
      "url": "https://example.com/audio.mp3",
      "cover": "https://example.com/cover.jpg",
      "theme": "#44b7fe",
      "source": "netease",
      "tags": ["chill", "night"],
      "dedupe_key": "netease:123456",
      "reason": "keyword match"
    }
  ]
}
```

## 7. N.E.K.O. 本地接入

本地新增远端音乐资源客户端，例如 `utils/remote_music_source.py`。该客户端负责请求 VPS、处理鉴权、超时、错误降级和返回结构校验。

建议配置项：

- `REMOTE_MUSIC_ENABLED`: 是否启用 VPS 音乐资源池。
- `REMOTE_RESOURCE_BASE_URL`: VPS API 根地址。
- `REMOTE_RESOURCE_API_KEY`: VPS API 鉴权密钥。
- `REMOTE_RESOURCE_TIMEOUT_SECONDS`: 请求超时时间。
- `REMOTE_MUSIC_FALLBACK_LOCAL`: 是否允许远端失败后回退本地音乐爬虫；默认关闭。

接入方式：

- 保留现有 Phase 1 逻辑，由模型根据上下文生成音乐关键词。
- 将主动对话中的 `fetch_music_content(keyword=..., limit=5)` 默认替换为远端资源池查询。
- 保持 `_format_music_content()`、`[MUSIC]` 标签、`selected_music_link`、source decay、音乐冷却和 APlayer 前端链路不变。
- VPS 不可用时，本轮音乐通道降级为空，不影响普通主动搭话和手动对话。

## 8. 迁移阶段

### Phase 1: 种子资源池

在 VPS 中先导入 20 到 50 首测试曲目。资源可以来自人工整理的 JSON/CSV，先不接真实爬虫。目标是验证数据库、API 和 APlayer 播放格式。

### Phase 2: N.E.K.O. 读取 VPS

新增本地远端音乐客户端，并让主动对话音乐路径读取 VPS。完成一次端到端验证：生成关键词、请求 VPS、模型输出 `[MUSIC]`、前端播放。

### Phase 3: 资源维护能力

增加去重、失效检测、usage event 和基础排序。播放完成事件回写 VPS，用于避免短时间重复推荐。

### Phase 4: 本地爬虫退出默认路径

主动对话默认不再调用 `utils/music_crawlers.py`。本地爬虫可以暂时保留为调试 fallback 或迁移参考，待稳定后再决定是否删除入口。

### Phase 5: 扩展通用资源池

音乐链路稳定后，用同样模式扩展到新闻、视频、个人动态等外部信息源。VPS 最终成为统一外部资源池。

## 9. 降级策略

- VPS 超时、401、500 或返回格式错误时，本轮音乐通道降级为空。
- 空资源池返回 `success=true, data=[]`，由 N.E.K.O. 视作无可用音乐。
- 默认不启用本地 fallback，避免重新依赖本地爬虫；调试或过渡期可显式开启。
- 降级不得中断普通主动对话、手动对话或本地记忆系统。

## 10. 测试计划

### VPS API

- `GET /health` 能返回服务和数据库状态。
- keyword 查询能返回相关曲目。
- random 查询能返回可播放曲目。
- 空资源池返回 `success=true, data=[]`。
- 鉴权失败返回明确错误。
- 数据库不可用时返回明确错误，不泄露连接密钥。

### 数据库

- 同一首歌重复导入不会产生重复记录。
- `playable=false` 的曲目不会被推荐。
- `dedupe_key` 唯一约束生效。
- usage event 能正确关联曲目。

### N.E.K.O. 本地

- mock VPS 返回 5 首歌时，Phase 2 能看到音乐候选。
- 模型输出 `[MUSIC]` 后，`source_links` 包含选中的音乐资源。
- 前端 APlayer 能播放 VPS 返回的音频 URL。
- VPS 超时或错误时，主动对话不中断。
- `REMOTE_MUSIC_ENABLED=false` 时不请求 VPS。

## 11. 验收标准

- 本地关闭音乐爬虫默认路径后，仍能通过 VPS 推荐并播放音乐。
- 音乐资源能在 VPS 中持久保存、重复查询和去重。
- 播放完成或分享事件能回写 VPS usage event。
- 普通对话、本地 SQLite 记忆系统、APlayer UI 不受影响。

## 12. 后续扩展

音乐资源池跑通后，可以把同样模式抽象为通用资源池：

- `/v1/items/search` 用于新闻、视频、个人动态等结构化外部资源。
- 各资源类型共享来源、去重、可用性、usage event 和排序能力。
- N.E.K.O. 保持消费者身份，只决定如何把资源自然地用于对话。
