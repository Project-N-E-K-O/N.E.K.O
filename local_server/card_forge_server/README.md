# NEKO 奇遇铸造机服务器

为 `card-forge` 前端提供 facts 抽取与卡牌故事生成两个接口。本目录是 NEKO 的独立副产物：可单独迭代；**不修改** `main_server`、`memory_server`、`memory/` 等核心模块。读取 facts 时仅 **只读** 本机 JSON 或可选 HTTP，与 FactStore 落盘的 `facts.json` schema 一致。

## 启动

直接复用 N.E.K.O 项目的 `.venv`（已含 fastapi / uvicorn）：

```powershell
# 从项目根目录运行（推荐）
uv run local_server/card_forge_server/server.py

# 或者直接指定当前项目 .venv 的 Python
.\.venv\Scripts\python.exe local_server\card_forge_server\server.py
```

> 仍依赖 `httpx`（可选 HTTP facts 源）；N.E.K.O 根目录的 `.venv` 中通常已带。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/forge/facts` | 从 NEKO 当前猫娘的 active facts 抽取候选事实，可排除已铸造来源 |
| POST | `/forge/card-story` | 用 NEKO 核心 LLM 配置把 `storyLead` 生成卡牌专属小故事 |
| GET  | `/health` | 健康检查 |

### `/forge/facts`

**查询参数**

| 参数 | 默认 | 说明 |
|------|------|------|
| `character` | — | 调试兼容参数；默认会被忽略，实际猫娘由 NEKO 当前选择解析。仅设置 `NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE=1` 时才允许覆盖 |
| `runtime_character_hint` | — | NEKO 本体运行态同步的当前猫娘名 |
| `min_importance` | `5` | 最低 importance |
| `include_absorbed` | `true` | 是否包含已 `absorbed` 的事实 |
| `limit` | `5` | 本轮抽取候选事实数量 |
| `exclude_fact_ids` | — | 逗号分隔，排除已铸造过的 fact id |
| `exclude_hashes` | — | 逗号分隔，排除已铸造过的 fact hash |

**环境变量**

| 变量 | 说明 |
|------|------|
| `NEKO_FACTS_JSON` | 指向单个 `facts.json` 的绝对路径（导出文件） |
| `NEKO_MEMORY_DIR` | 调试/迁移用记忆根目录；正常情况下优先使用 NEKO 配置管理器当前 memory_dir |
| `NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE` | 调试开关；设为 `1` 时才允许 `character` 覆盖当前猫娘 |
| `NEKO_FORGE_FACTS_URL` | （可选）优先 `GET` 该 URL；支持 `{character}` 占位 |

正常铸造流程不要传 `character`。NEKO 本体运行时的当前猫娘才是记忆归属来源；前端展示名只用于 UI，不用于选择 facts 目录。

**私密性**：facts 含个人化内容；请勿把本服务暴露到公网；日志不打印完整 `text`。

### `/forge/card-story`

该接口的 LLM 调用集中在 `forge_story_generator.py`，`server.py` 只做路由转发。复用 NEKO 主服务的模型配置与 `utils.llm_client.create_chat_llm()`，不在铸造机内硬编码 OpenAI、Gemini、DeepSeek 等服务商差异。生成时会重新解析 NEKO 当前猫娘，并用这只猫娘的人格 prompt 与主人名写入故事提示词。

**请求** `application/json`：

```json
{
  "character": "N.E.K.O",
  "storyLead": "从 active fact 抽取出的故事引子",
  "sourceFactId": "fact_xxx",
  "card": {
    "attrName": "热情"
  }
}
```

**响应**：

```json
{
  "success": true,
  "story": "生成后的卡牌专属小故事",
  "storyGenerationStatus": "ready",
  "provider": "neko-core-summary",
  "model": "当前 summary 模型",
  "sourceFactId": "fact_xxx"
}
```

`character` 只作为旧调用兼容字段；正常情况下故事生成仍以 NEKO 当前猫娘为准。如果模型未配置、超时或生成失败，会返回 `success: false`；前端会把该卡标记为 `failed`，成功时才把 LLM 返回内容写入 `story` / `summary` 作为真正卡牌故事。
