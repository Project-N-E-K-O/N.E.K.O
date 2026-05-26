# 猫娘大乱斗匹配服务器

本地对战匹配服务，供 `battle-arena` 前端调用。本目录为 **Battle Arena 副产物**：可单独迭代；**不修改** N.E.K.O 的 `main_server`、`memory_server`、`memory/` 等核心模块。奇遇铸造机用 facts 时仅 **只读** 本机 JSON 或可选 HTTP，与 FactStore 落盘的 `facts.json` schema 一致。

## 启动

直接复用 N.E.K.O 项目的 `.venv`（已含 fastapi / uvicorn，无需额外安装）：

```powershell
# 从项目根目录运行（推荐）
uv run local_server/battle_arena_server/server.py

# 或者直接指定当前项目 .venv 的 Python
.\.venv\Scripts\python.exe local_server\battle_arena_server\server.py
```

> ⚠️ 不要用 base conda 的 `python` 直接运行，那个环境没有 uvicorn。

> 铸造机 facts 接口依赖 `httpx`：若 `.venv` 中尚未安装，请在 N.E.K.O 根目录执行 `uv sync` 或 `uv pip install -r local_server/battle_arena_server/requirements.txt`。

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/arena/join` | 上传羁绊列表，加入匹配队列 |
| GET  | `/arena/status/{player_id}` | 轮询匹配结果 |
| POST | `/arena/leave/{player_id}` | 离开房间 |
| GET  | `/arena/forge-facts` | 奇遇铸造机：从 NEKO 当前猫娘的 active facts 随机抽取 5 条候选（`id`/`hash` 去重，可排除已铸造来源） |
| POST | `/arena/forge-card-story` | 奇遇铸造机：用 NEKO 核心 LLM 配置把 `storyLead` 生成卡牌专属小故事 |
| GET  | `/health` | 健康检查 |

### `/arena/forge-facts`（奇遇铸造机卡池）

**查询参数**

| 参数 | 默认 | 说明 |
|------|------|------|
| `character` | — | 调试兼容参数；默认会被忽略，实际猫娘由 NEKO 当前选择解析。仅设置 `NEKO_BRAWL_ALLOW_CHARACTER_OVERRIDE=1` 时才允许覆盖 |
| `min_importance` | `5` | 最低 importance |
| `include_absorbed` | `true` | 是否包含已 `absorbed` 的事实；active `facts.json` 中的 absorbed 不等于归档 |
| `limit` | `5` | 本轮抽取候选事实数量，奇遇铸造机默认固定使用 5 条 |
| `exclude_fact_ids` | — | 逗号分隔，排除已铸造过的 fact id，避免重复来源 |
| `exclude_hashes` | — | 逗号分隔，排除已铸造过的 fact hash，避免不同 id 但内容重复 |

**环境变量（二选一配置数据源；均未设置且无 URL 时返回空 `facts` + `error`）**

| 变量 | 说明 |
|------|------|
| `NEKO_FACTS_JSON` | 指向单个 `facts.json` 的绝对路径（例如导出文件） |
| `NEKO_MEMORY_DIR` | 调试/迁移用记忆根目录；正常情况下优先使用 NEKO 配置管理器当前 memory_dir |
| `NEKO_BRAWL_ALLOW_CHARACTER_OVERRIDE` | 调试开关；设为 `1` 时才允许 `character` 覆盖当前猫娘 |
| `NEKO_FORGE_FACTS_URL` | （可选）优先 `GET` 该 URL；支持 `{character}` 占位，占位值为解析后的当前猫娘；失败或空则回退读盘 |

**响应** `application/json`：`{ "character": "当前猫娘", "factsSource": "neko-config", "facts": [ { "id", "text", "importance", "entity", "tags", "created_at", "hash" } ], "requestedLimit": 5, "returnedCount": 5 }`。无数据时可能含 `"error": "facts_source_not_configured"` 等。

**curl 示例**

```powershell
$env:NEKO_FACTS_JSON="C:\Users\你\Downloads\facts.json"
uv run local_server/battle_arena_server/server.py
# 另开终端
curl "http://127.0.0.1:3001/arena/forge-facts?limit=5"
```

正常铸造流程不要传 `character`。NEKO 本体运行时的当前猫娘才是记忆归属来源，因为大乱斗邀请来自这只猫娘；前端展示名只用于 UI，不用于选择 facts 目录。

**私密性**：facts 含个人化内容；请勿把本服务暴露到公网；日志不打印完整 `text`。

### `/arena/forge-card-story`（卡牌故事生成）

该接口的 LLM 调用逻辑集中在 `forge_story_generator.py`，`server.py` 只做路由转发。它复用 NEKO 主服务的模型配置与 `utils.llm_client.create_chat_llm()`，不在大乱斗内硬编码 OpenAI、Gemini、DeepSeek 等服务商差异。生成时会重新解析 NEKO 当前猫娘，并用这只猫娘的人格 prompt 与主人名写入故事提示词。

**请求** `application/json`：

```json
{
  "character": "N.E.K.O",
  "storyLead": "从 active fact 抽取出的故事引子",
  "sourceFactId": "fact_xxx",
  "card": {
    "baseCode": "C005",
    "name": "还没认输呢(Forged)",
    "type": "攻击",
    "cost": 2,
    "attrName": "热情",
    "comboAttrName": "温柔",
    "mainText": "对Boss造成2点伤害",
    "comboText": "额外造成1点伤害"
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

### 真实奇遇铸卡接入说明

当前 `/arena/forge-facts` 只负责把可用的 facts / 自身故事候选项读出来，返回给前端作为“奇遇铸造机”的事件卡槽；真正的 Forged 战斗卡仍由前端 `createForgedBrawlCard()` 根据选中的事件生成。

现阶段的铸卡规则是原型实现：

- 奇遇事件优先来自 `/arena/forge-facts` 返回的 `text`，前端会把它映射成卡牌的 `storyLead`，即“故事引子”，不会直接当作最终 `story`。
- 当前已经接入 NEKO 核心 LLM：使用 `storyLead` + 基础卡信息（卡名、行动力、主属性、主效果、Combo 效果）生成卡牌专属小故事，再写入 `story` / `summary`。
- 如果 facts 未配置、匹配服务未启动或没有可用数据，前端会回退到硬编码的临时事件池。
- 战斗卡效果暂时从 C001-C013 基础卡中选择；卡名、行动力、主属性、主效果和 Combo 效果跟随基础卡编号。
- Combo 属性目前随机；后续可以改成由 facts 内容、规则表或 LLM 评估决定。
- Forged 卡名必须保留 `(Forged)` 后缀，方便在组卡、战斗和日志中识别。
- 当前 Forged 卡保存在浏览器 localStorage，后续需要确认是否改为角色/账户级持久化，并决定同一 fact 是否允许重复铸造。

facts 的真实位置已经由后端统一解析：默认读取 NEKO 配置管理器里的 memory_dir 和当前猫娘，组合为 `{memory_dir}/{当前猫娘}/facts.json`。NEKO 本体需要运行或至少保持当前配置准确，是因为“当前猫娘”代表这次邀请玩家进入大乱斗的主体；铸造机抽取记忆时必须跟随这只猫娘，而不是跟随前端临时展示名或硬编码路径。

## 本地调试

单人运行时，3 秒后自动匹配内置虚拟对手，无需等待第二个玩家。

## 待办

- `PLACEHOLDER_BONDS` — 替换为真实羁绊数据（来源：N.E.K.O 主应用羁绊记录系统）
- `DUMMY_OPPONENT` — 真实联机时可移除或保留为 Bot 对手

## 方案变体记录

### 当前方案（方案 A，含 httpx）

`server.py` 保留 `_fetch_facts_from_url`，`requirements.txt` 含 `httpx`。  
N.E.K.O `.venv` 已有 httpx，正常 `uv sync` 后无需额外操作。  
`NEKO_FORGE_FACTS_URL` **不设置时该函数不会被调用**，不影响只读本机 JSON 的主路径。

### 回退方案（方案 B，无 httpx，最小化依赖）

若要去掉 httpx 完全依赖本机文件，需做以下三处改动：

**`requirements.txt`** — 删除：
```
httpx>=0.27.0
```

**`server.py`** — 删除 `_fetch_facts_from_url` 整个函数，以及 `arena_forge_facts` 路由开头的 URL 段：
```python
# 删除这整段
url_template = os.environ.get("NEKO_FORGE_FACTS_URL", "").strip()
if url_template:
    try:
        url = url_template.format(character=character or "")
    except (KeyError, IndexError, ValueError):
        url = url_template
    fetched = await _fetch_facts_from_url(url)
    if fetched is not None:
        raw = fetched
```

顶部 import 可简化：`from typing import Any, Optional` 中 `Any` 仍需保留（`_select_forge_facts` 有用到）；`import logging` 可保留（`_load_facts_json` 有 `logger.warning`）或一并删掉改为 `print`。

**效果**：`/arena/forge-facts` 只走本机 `NEKO_FACTS_JSON` / `NEKO_MEMORY_DIR` 两条路径，无任何网络依赖，无额外 requirement，文件更轻。
