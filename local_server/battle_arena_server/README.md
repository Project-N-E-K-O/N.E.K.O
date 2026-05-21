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
| GET  | `/arena/forge-facts` | 奇遇铸造机：从 facts 随机抽取最多 5 条（`id` 唯一、`hash` 去重） |
| GET  | `/health` | 健康检查 |

### `/arena/forge-facts`（奇遇铸造机卡池）

**查询参数**

| 参数 | 默认 | 说明 |
|------|------|------|
| `character` | — | 猫娘名，与 NEKO 记忆目录 `{NEKO_MEMORY_DIR}/{character}/facts.json` 一致 |
| `min_importance` | `5` | 最低 importance |
| `include_absorbed` | `false` | 是否包含已 `absorbed` 的事实 |

**环境变量（二选一配置数据源；均未设置且无 URL 时返回空 `facts` + `error`）**

| 变量 | 说明 |
|------|------|
| `NEKO_FACTS_JSON` | 指向单个 `facts.json` 的绝对路径（例如导出文件） |
| `NEKO_MEMORY_DIR` | 记忆根目录；与 `character` 组合为 `{dir}/{character}/facts.json` |
| `NEKO_FORGE_FACTS_URL` | （可选）优先 `GET` 该 URL；支持 `{character}` 占位；失败或空则回退读盘 |

**响应** `application/json`：`{ "facts": [ { "id", "text", "importance", "entity" } ] }`，最多 5 条。无数据时可能含 `"error": "facts_source_not_configured"` 等。

**curl 示例**

```powershell
$env:NEKO_FACTS_JSON="C:\Users\你\Downloads\facts.json"
uv run local_server/battle_arena_server/server.py
# 另开终端
curl "http://127.0.0.1:3001/arena/forge-facts?character=Yui"
```

**私密性**：facts 含个人化内容；请勿把本服务暴露到公网；日志不打印完整 `text`。

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
