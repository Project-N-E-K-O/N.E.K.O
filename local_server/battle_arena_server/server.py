# -*- coding: utf-8 -*-
"""
猫娘大乱斗 — 本地对战匹配服务器
端口: 3001
启动: uvicorn server:app --host 0.0.0.0 --port 3001 --reload
      或直接: python server.py
"""

import asyncio
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("battle_arena_server")

# ---------------------------------------------------------------------------
# 占位数据
# ---------------------------------------------------------------------------

# TODO: [羁绊列表接入] 以下为占位内容，待羁绊数据结构和 API 确定后替换为真实数据
PLACEHOLDER_BONDS: list[str] = [
    "与主人愉快的第一天",
    "主人和我陪伴的100小时",
    "主人夸我的第一次",
    "和主人一起看的第一次日出",
    "主人生病时我在身旁的那个夜晚",
]

# TODO: [虚拟对手] 本地单人调试用，真实对战由匹配队列填充
DUMMY_OPPONENTS: list[dict] = [
    {"nekoName": "迷路的猫娘",  "ownerName": "不知道主人在哪"},
    {"nekoName": "傲娇大猫猫",  "ownerName": "才、才不是为了你"},
    {"nekoName": "困困小猫咪",  "ownerName": "打盹中的铲屎官"},
    {"nekoName": "社恐猫猫",    "ownerName": "躲在角落的主人"},
]

# ---------------------------------------------------------------------------
# 应用与 CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="猫娘大乱斗匹配服务器", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 内存存储
# ---------------------------------------------------------------------------

# 等待匹配的玩家: player_id -> PlayerEntry dict
waiting_room: dict[str, dict] = {}
# 已匹配结果: player_id -> opponent snapshot dict
matched: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class JoinRequest(BaseModel):
    nekoName: str = "未知猫娘"
    ownerName: str = "未知主人"
    avatar: Optional[str] = None
    # TODO: [羁绊列表接入] bonds 目前接受任意字符串列表，待数据结构确定后添加校验
    bonds: list[str] = PLACEHOLDER_BONDS


# ---------------------------------------------------------------------------
# 匹配逻辑
# ---------------------------------------------------------------------------

# 记录上一次虚拟对手，确保连续两次不重复
# 使用 dict 包装避免 global 声明问题
_state: dict = {"last_dummy_name": None}


def try_match() -> None:
    """若等待室有 ≥2 人则立即配对。"""
    ids = list(waiting_room.keys())
    if len(ids) < 2:
        return
    id_a, id_b = ids[0], ids[1]
    player_a = waiting_room.pop(id_a)
    player_b = waiting_room.pop(id_b)
    matched[id_a] = dict(player_b)
    matched[id_b] = dict(player_a)


async def schedule_dummy_match(player_id: str) -> None:
    """单人调试：3 秒后若未匹配则随机分配一个虚拟对手（确保不连续重复）。"""
    await asyncio.sleep(3)
    if player_id in waiting_room and player_id not in matched:
        player = waiting_room.pop(player_id)
        
        last_name = _state["last_dummy_name"]
        # 确保连续两次不重复
        available = [d for d in DUMMY_OPPONENTS if d["nekoName"] != last_name]
        if not available:  # 如果全部都被排除（理论上不会发生），则回退到全部
            available = DUMMY_OPPONENTS
        
        dummy_base = random.choice(available)
        _state["last_dummy_name"] = dummy_base["nekoName"]
        
        dummy = {**dummy_base, "avatar": None, "bonds": PLACEHOLDER_BONDS}
        matched[player_id] = dummy
        print(f"[调试] {player['nekoName']} 匹配虚拟对手：{dummy['nekoName']}")


# ---------------------------------------------------------------------------
# 奇遇铸造机 — facts.json 只读（与 FactStore 同 schema，不改 NEKO 核心）
# ---------------------------------------------------------------------------


def _safe_character_segment(name: Optional[str]) -> Optional[str]:
    if not name or not isinstance(name, str):
        return None
    s = name.strip()
    if not s or len(s) > 80:
        return None
    if any(x in s for x in ("/", "\\", "..", "\x00")):
        return None
    return s


def _resolve_facts_path(character: Optional[str]) -> Optional[Path]:
    direct = os.environ.get("NEKO_FACTS_JSON", "").strip()
    if direct:
        return Path(direct)
    base = os.environ.get("NEKO_MEMORY_DIR", "").strip()
    if not base or not character:
        return None
    safe = _safe_character_segment(character)
    if not safe:
        return None
    return Path(base) / safe / "facts.json"


def _load_facts_json(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("forge-facts: failed to read %s: %s", path, type(e).__name__)
        return []


# 方案 B（最小化，无 httpx）回退说明：
#   若不需要远程 URL 功能，可删除下方整个 _fetch_facts_from_url 函数，
#   同时删除 requirements.txt 中的 httpx 行，
#   以及 arena_forge_facts 路由中读取 NEKO_FORGE_FACTS_URL 的片段。
#   详见 README.md「方案变体记录」。
async def _fetch_facts_from_url(url: str) -> Optional[list[dict[str, Any]]]:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning("forge-facts: URL %s returned %s", url[:80], r.status_code)
                return None
            data = r.json()
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
            if isinstance(data, dict) and isinstance(data.get("facts"), list):
                return [x for x in data["facts"] if isinstance(x, dict)]
            return None
    except Exception:
        logger.exception("forge-facts: URL fetch failed for %s", url[:80])
        return None


def _select_forge_facts(
    raw: list[dict[str, Any]],
    *,
    min_importance: int = 5,
    include_absorbed: bool = False,
    limit: int = 5,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        if not fid:
            continue
        if not include_absorbed and item.get("absorbed"):
            continue
        try:
            imp = int(item.get("importance") or 0)
        except (TypeError, ValueError):
            imp = 0
        if imp < min_importance:
            continue
        filtered.append(item)

    random.shuffle(filtered)
    deduped: list[dict[str, Any]] = []
    seen_hash: set[str] = set()
    for item in filtered:
        h = item.get("hash")
        key = str(h) if h else str(item.get("id", ""))
        if key in seen_hash:
            continue
        seen_hash.add(key)
        deduped.append(item)

    random.shuffle(deduped)
    picked = deduped[:limit]
    out: list[dict[str, Any]] = []
    for x in picked:
        out.append(
            {
                "id": str(x.get("id", "")),
                "text": str(x.get("text", "")),
                "importance": int(x.get("importance") or 0),
                "entity": str(x.get("entity", "")),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@app.get("/arena/forge-facts")
async def arena_forge_facts(
    character: Optional[str] = Query(None, description="猫娘名，与 NEKO memory 子目录名一致"),
    min_importance: int = Query(5, ge=0, le=10),
    include_absorbed: bool = Query(False),
):
    """从本机 facts.json（或可选 HTTP）抽取最多 5 条事实，按 hash 去重后随机返回。"""
    error: Optional[str] = None
    raw: list[dict[str, Any]] = []

    url_template = os.environ.get("NEKO_FORGE_FACTS_URL", "").strip()
    if url_template:
        try:
            url = url_template.format(character=character or "")
        except (KeyError, IndexError, ValueError):
            url = url_template
        fetched = await _fetch_facts_from_url(url)
        if fetched is not None:
            raw = fetched

    if not raw:
        path = _resolve_facts_path(character)
        if path is None:
            error = "facts_source_not_configured"
        else:
            raw = _load_facts_json(path)
            if not raw and error is None:
                error = "facts_file_empty_or_missing"

    facts = _select_forge_facts(
        raw,
        min_importance=min_importance,
        include_absorbed=include_absorbed,
        limit=5,
    )

    payload: dict[str, Any] = {"facts": facts}
    if error and not facts:
        payload["error"] = error
    return JSONResponse(payload)


@app.post("/arena/join")
async def join_arena(body: JoinRequest):
    """玩家加入大乱斗，上传羁绊列表，返回 playerId 及对手信息（若已匹配）。"""
    player_id = str(uuid.uuid4())
    entry = {
        "nekoName":  body.nekoName,
        "ownerName": body.ownerName,
        "avatar":    body.avatar,
        "bonds":     body.bonds,   # TODO: 替换为真实羁绊列表
        "joinedAt":  time.time(),
    }
    waiting_room[player_id] = entry
    print(f"[加入] {entry['nekoName']} ({player_id})  等待人数: {len(waiting_room)}")

    try_match()

    opponent = matched.get(player_id)
    if opponent is None:
        # 未立即匹配，安排虚拟对手兜底
        asyncio.create_task(schedule_dummy_match(player_id))

    return JSONResponse({"playerId": player_id, "opponent": opponent})


@app.get("/arena/status/{player_id}")
async def arena_status(player_id: str):
    """轮询匹配结果。"""
    opponent = matched.get(player_id)
    return JSONResponse({"opponent": opponent})


@app.post("/arena/leave/{player_id}")
async def arena_leave(player_id: str):
    """玩家离开，清理房间数据。"""
    waiting_room.pop(player_id, None)
    matched.pop(player_id, None)
    return JSONResponse({"ok": True})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "battle_arena_server"}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=3001, reload=True)
