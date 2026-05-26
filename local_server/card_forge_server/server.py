# -*- coding: utf-8 -*-
"""
奇遇铸造机 — 本地 facts 抽取 + 故事生成服务器
端口: 3001
启动:
    uv run local_server/card_forge_server/server.py
"""

import hashlib
import json
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("card_forge_server")
SERVER_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 应用与 CORS
# ---------------------------------------------------------------------------

app = FastAPI(title="NEKO 奇遇铸造机服务器", version="0.1.0")


def _resolve_cors_origins() -> list[str]:
    """允许跨域的来源白名单。

    默认只放行本机 card-forge 开发前端 (127.0.0.1:5173 / localhost:5173)。
    facts / story 接口会返回个人化的猫娘记忆，所以不能像旧版那样 allow_origins=["*"]
    —— 那样任何浏览器站点都能在用户登录态下读到响应。如需添加额外来源，
    可设置 NEKO_CARD_FORGE_ALLOWED_ORIGINS 环境变量（逗号分隔），或显式置
    为 "*" 以恢复旧行为（不推荐）。
    """
    raw = os.environ.get("NEKO_CARD_FORGE_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    return [item.strip() for item in raw.split(",") if item.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_cors_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# 奇遇铸造机 — facts.json 只读（与 FactStore 同 schema，不改 NEKO 核心）
# ---------------------------------------------------------------------------


async def _resolve_active_facts_context(
    character: Optional[str] = None,
    runtime_character_hint: Optional[str] = None,
):
    from active_neko_context import resolve_active_neko_context

    # 默认必须跟随 NEKO 当前猫娘。character 只作为显式调试开关保留，避免旧前端
    # 或大厅展示名误导铸造机读取另一只猫娘的 facts。
    allow_override = os.environ.get("NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE", "").strip() == "1"
    return await resolve_active_neko_context(
        character if allow_override else None,
        runtime_character_hint,
    )


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


def _select_forge_facts_with_stats(
    raw: list[dict[str, Any]],
    *,
    min_importance: int = 5,
    include_absorbed: bool = True,
    limit: int = 5,
    exclude_ids: Optional[set[str]] = None,
    exclude_hashes: Optional[set[str]] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    exclude_ids = exclude_ids or set()
    exclude_hashes = exclude_hashes or set()
    filtered: list[dict[str, Any]] = []
    missing_id_count = 0
    excluded_count = 0
    absorbed_count = 0
    low_importance_count = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        text_key = str(item.get("text") or "")
        raw_hash_key = str(item.get("hash") or "")
        if not fid:
            missing_id_count += 1
            if raw_hash_key:
                fid_key = f"hash:{raw_hash_key}"
            elif text_key:
                fid_key = f"text:{hashlib.sha1(text_key.encode('utf-8')).hexdigest()}"
            else:
                continue
        else:
            fid_key = str(fid)
        hash_key = raw_hash_key or (hashlib.sha1(text_key.encode("utf-8")).hexdigest() if text_key else "")
        if fid_key in exclude_ids or (hash_key and hash_key in exclude_hashes):
            excluded_count += 1
            continue
        if not include_absorbed and item.get("absorbed"):
            absorbed_count += 1
            continue
        try:
            imp = int(item.get("importance") or 0)
        except (TypeError, ValueError):
            imp = 0
        if imp < min_importance:
            low_importance_count += 1
            continue
        filtered.append({**item, "_forge_fid": fid_key, "_forge_hash": hash_key})

    random.shuffle(filtered)
    deduped: list[dict[str, Any]] = []
    seen_hash: set[str] = set()
    seen_id: set[str] = set()
    duplicate_count = 0
    for item in filtered:
        fid_key = str(item.get("_forge_fid") or item.get("id", ""))
        hash_key = str(item.get("_forge_hash") or item.get("hash") or "")
        if fid_key in seen_id or (hash_key and hash_key in seen_hash):
            duplicate_count += 1
            continue
        if fid_key:
            seen_id.add(fid_key)
        if hash_key:
            seen_hash.add(hash_key)
        deduped.append(item)

    def item_key(item: dict[str, Any]) -> str:
        return str(item.get("_forge_fid") or item.get("id", ""))

    def importance_weight(item: dict[str, Any]) -> float:
        try:
            importance = max(0, int(item.get("importance") or 0))
        except (TypeError, ValueError):
            importance = 0
        return 1.0 + importance

    def weighted_pick(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        pool = [item for item in items if item_key(item)]
        picked_items: list[dict[str, Any]] = []
        for _ in range(min(count, len(pool))):
            total = sum(importance_weight(item) for item in pool)
            roll = random.uniform(0, total)
            cursor = 0.0
            chosen_index = 0
            for index, item in enumerate(pool):
                cursor += importance_weight(item)
                if roll <= cursor:
                    chosen_index = index
                    break
            picked_items.append(pool.pop(chosen_index))
        return picked_items

    guaranteed_recent_count = 0
    guaranteed_distant_count = 0
    weighted_random_count = 0
    if len(deduped) >= limit and limit >= 5:
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=2)
        recent_candidates = [
            item
            for item in deduped
            if (dt := _parse_fact_datetime(item.get("created_at"))) is not None and dt >= recent_cutoff
        ]
        recent_candidates.sort(
            key=lambda item: (
                importance_weight(item),
                _parse_fact_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        guaranteed_recent = weighted_pick(recent_candidates, 2)
        guaranteed_recent.sort(
            key=lambda item: (
                importance_weight(item),
                _parse_fact_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        used_keys = {item_key(item) for item in guaranteed_recent}
        for item in guaranteed_recent:
            item["_forge_recent_guaranteed"] = True

        distant_candidates = [
            item
            for item in deduped
            if item_key(item) not in used_keys and _fact_memory_datetime(item) is not None
        ]
        distant_candidates.sort(
            key=lambda item: (
                _fact_memory_datetime(item) or datetime.max.replace(tzinfo=timezone.utc),
                -importance_weight(item),
            )
        )
        oldest_pool_size = max(1, min(len(distant_candidates), max(1, len(distant_candidates) // 4)))
        oldest_pool = distant_candidates[:oldest_pool_size]
        oldest_pool_keys = {item_key(item) for item in oldest_pool}
        guaranteed_distant = weighted_pick(oldest_pool, 1)
        for item in guaranteed_distant:
            item["_forge_distant_guaranteed"] = True
        used_keys.update(item_key(item) for item in guaranteed_distant)

        random_candidates = [
            item
            for item in deduped
            if item_key(item) not in used_keys and item_key(item) not in oldest_pool_keys
        ]
        weighted_random = weighted_pick(random_candidates, max(0, limit - len(guaranteed_recent) - len(guaranteed_distant)))
        if len(weighted_random) < max(0, limit - len(guaranteed_recent) - len(guaranteed_distant)):
            used_keys.update(item_key(item) for item in weighted_random)
            fallback_pool = [item for item in deduped if item_key(item) not in used_keys]
            random.shuffle(fallback_pool)
            weighted_random.extend(fallback_pool[: max(0, limit - len(guaranteed_recent) - len(guaranteed_distant) - len(weighted_random))])

        random.shuffle(weighted_random)
        picked = [*guaranteed_recent, *weighted_random, *guaranteed_distant][:limit]
        if len(picked) < limit:
            used_keys = {item_key(item) for item in picked}
            fallback_pool = [item for item in deduped if item_key(item) not in used_keys]
            random.shuffle(fallback_pool)
            picked.extend(fallback_pool[: limit - len(picked)])

        if len(picked) >= limit and guaranteed_distant:
            distant_item = guaranteed_distant[0]
            picked = [item for item in picked if item_key(item) != item_key(distant_item)]
            picked = [*picked[: limit - 1], distant_item]

        picked = picked[:limit]
        guaranteed_recent_count = len([item for item in picked if item.get("_forge_recent_guaranteed")])
        guaranteed_distant_count = len([item for item in picked if item.get("_forge_distant_guaranteed")])
        weighted_random_count = len(picked) - guaranteed_recent_count - guaranteed_distant_count
    else:
        random.shuffle(deduped)
        picked = deduped[:limit]

    out: list[dict[str, Any]] = []
    for x in picked:
        out.append(
            {
                "id": str(x.get("id") or x.get("_forge_fid") or ""),
                "text": str(x.get("text", "")),
                "importance": int(x.get("importance") or 0),
                "entity": str(x.get("entity", "")),
                "tags": x.get("tags") if isinstance(x.get("tags"), list) else [],
                "created_at": x.get("created_at"),
                "event_start_at": x.get("event_start_at"),
                "hash": str(x.get("hash") or x.get("_forge_hash") or ""),
                "recentGuaranteed": bool(x.get("_forge_recent_guaranteed")),
                "distantGuaranteed": bool(x.get("_forge_distant_guaranteed")),
                "sourceCollection": str(x.get("_forge_source_collection") or "facts"),
            }
        )
    return out, {
        "rawCount": len([item for item in raw if isinstance(item, dict)]),
        "filteredCount": len(filtered),
        "dedupedCount": len(deduped),
        "excludedCount": excluded_count,
        "lowImportanceCount": low_importance_count,
        "absorbedSkippedCount": absorbed_count,
        "missingIdCount": missing_id_count,
        "duplicateCount": duplicate_count,
        "recentGuaranteedCount": guaranteed_recent_count,
        "distantGuaranteedCount": guaranteed_distant_count,
        "weightedRandomCount": weighted_random_count,
    }


def _parse_csv_set(value: Optional[str]) -> set[str]:
    if not value or not isinstance(value, str):
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def _parse_fact_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fact_memory_datetime(item: dict[str, Any]) -> Optional[datetime]:
    return _parse_fact_datetime(item.get("event_start_at")) or _parse_fact_datetime(item.get("created_at"))


def _importance_weight(item: dict[str, Any]) -> float:
    try:
        importance = max(0, int(item.get("importance") or 0))
    except (TypeError, ValueError):
        importance = 0
    return 1.0 + importance


def _weighted_pick(items: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    pool = [item for item in items if item.get("id") or item.get("hash")]
    picked_items: list[dict[str, Any]] = []
    for _ in range(min(count, len(pool))):
        total = sum(_importance_weight(item) for item in pool)
        roll = random.uniform(0, total)
        cursor = 0.0
        chosen_index = 0
        for index, item in enumerate(pool):
            cursor += _importance_weight(item)
            if roll <= cursor:
                chosen_index = index
                break
        picked_items.append(pool.pop(chosen_index))
    return picked_items


def _select_archive_distant_fact(
    raw_archive: list[dict[str, Any]],
    *,
    min_importance: int = 0,
    include_absorbed: bool = True,
    exclude_ids: Optional[set[str]] = None,
    exclude_hashes: Optional[set[str]] = None,
) -> tuple[Optional[dict[str, Any]], dict[str, int]]:
    if not raw_archive:
        return None, {"archiveRawCount": 0, "archiveFilteredCount": 0}

    archive_candidates, archive_stats = _select_forge_facts_with_stats(
        raw_archive,
        min_importance=min_importance,
        include_absorbed=include_absorbed,
        limit=len(raw_archive) + 1,
        exclude_ids=exclude_ids,
        exclude_hashes=exclude_hashes,
    )
    dated_candidates = [item for item in archive_candidates if _fact_memory_datetime(item) is not None]
    if not dated_candidates:
        return None, {
            "archiveRawCount": archive_stats.get("rawCount", 0),
            "archiveFilteredCount": archive_stats.get("filteredCount", 0),
        }

    dated_candidates.sort(
        key=lambda item: (
            _fact_memory_datetime(item) or datetime.max.replace(tzinfo=timezone.utc),
            -_importance_weight(item),
        )
    )
    oldest_pool_size = max(1, min(len(dated_candidates), max(1, len(dated_candidates) // 4)))
    oldest_pool = dated_candidates[:oldest_pool_size]
    picked = _weighted_pick(oldest_pool, 1)
    if not picked:
        return None, {
            "archiveRawCount": archive_stats.get("rawCount", 0),
            "archiveFilteredCount": archive_stats.get("filteredCount", 0),
        }
    archive_fact = {
        **picked[0],
        "distantGuaranteed": True,
        "recentGuaranteed": False,
        "sourceCollection": "facts_archive",
    }
    return archive_fact, {
        "archiveRawCount": archive_stats.get("rawCount", 0),
        "archiveFilteredCount": archive_stats.get("filteredCount", 0),
    }


# 与 forge_story_generator._FORGE_SENSITIVE_FIELDS 对齐:route 层 log 里只有
# storyLead 来源敏感(其余是 id/provider/model/elapsedMs 之类的元数据)。
# 仍然单独维护一份,避免 server.py 反向依赖 generator 的内部实现细节。
_FORGE_ROUTE_SENSITIVE_FIELDS = frozenset({"storyLead"})
_FORGE_ROUTE_LOG_PREVIEW_CHARS = 40


def _mask_route_sensitive(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    clipped = text[:_FORGE_ROUTE_LOG_PREVIEW_CHARS]
    suffix = "…" if len(text) > _FORGE_ROUTE_LOG_PREVIEW_CHARS else ""
    return f"{clipped}{suffix}(len={len(text)})"


def _forge_route_log(request_id: str, event: str, **fields: Any) -> None:
    """Print forge-card diagnostics to the server console only; no local log file.

    敏感字段 (storyLead) 在打印前先脱敏 —— 个人化记忆不能整段进控制台日志,
    跟 forge_story_generator._forge_log 行为保持一致。
    """

    safe_fields = {
        key: (_mask_route_sensitive(value) if key in _FORGE_ROUTE_SENSITIVE_FIELDS else value)
        for key, value in fields.items()
    }
    print(
        f"[forge-card-story][{request_id}][route.{event}] "
        f"{json.dumps(safe_fields, ensure_ascii=False, default=str)}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@app.get("/arena/forge-facts")
async def arena_forge_facts(
    character: Optional[str] = Query(None, description="调试用猫娘名；默认忽略，实际读取 NEKO 当前猫娘"),
    runtime_character_hint: Optional[str] = Query(None, description="NEKO 本体运行态同步的当前猫娘名"),
    min_importance: int = Query(5, ge=0, le=10),
    include_absorbed: bool = Query(True),
    limit: int = Query(5, ge=1, le=10, description="抽取候选事实数量；奇遇铸造机默认使用 5 条"),
    exclude_fact_ids: Optional[str] = Query(None, description="逗号分隔，排除已经铸造过的 fact id"),
    exclude_hashes: Optional[str] = Query(None, description="逗号分隔，排除已经铸造过的 fact hash"),
):
    """从当前 active facts.json（或可选 HTTP）抽取事实，按 id/hash 去重后随机返回。"""
    runtime_hint = runtime_character_hint.strip() if isinstance(runtime_character_hint, str) else ""
    allow_override = os.environ.get("NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE", "").strip() == "1"
    if not runtime_hint and not (allow_override and character):
        return JSONResponse(
            {
                "character": "",
                "factsSource": "runtime-unlinked",
                "characterOverrideIgnored": bool(character),
                "runtimeCharacterHintUsed": False,
                "facts": [],
                "requestedLimit": limit,
                "returnedCount": 0,
                "fallbackReason": "runtime_character_hint_missing",
                "error": "active_neko_runtime_not_linked",
                "rawCount": 0,
                "filteredCount": 0,
                "dedupedCount": 0,
                "excludedCount": 0,
                "lowImportanceCount": 0,
                "absorbedSkippedCount": 0,
                "missingIdCount": 0,
                "duplicateCount": 0,
                "recentGuaranteedCount": 0,
                "distantGuaranteedCount": 0,
                "weightedRandomCount": 0,
            }
        )

    error: Optional[str] = None
    raw: list[dict[str, Any]] = []
    raw_archive: list[dict[str, Any]] = []
    try:
        context = await _resolve_active_facts_context(character, runtime_character_hint)
    except Exception as exc:
        logger.warning("forge-facts: failed to resolve active NEKO context: %s", type(exc).__name__)
        context = None
        error = "active_neko_context_unavailable"

    resolved_character = context.lanlan_name if context else ""
    facts_source = context.source if context else "unresolved"
    override_ignored = bool(
        character
        and character != resolved_character
        and os.environ.get("NEKO_CARD_FORGE_ALLOW_CHARACTER_OVERRIDE", "").strip() != "1"
    )
    runtime_hint_used = bool(runtime_character_hint and runtime_character_hint == resolved_character)

    url_template = os.environ.get("NEKO_FORGE_FACTS_URL", "").strip()
    if url_template:
        try:
            url = url_template.format(character=resolved_character or "")
        except (KeyError, IndexError, ValueError):
            url = url_template
        fetched = await _fetch_facts_from_url(url)
        if fetched is not None:
            raw = fetched

    if not raw:
        path = context.facts_path if context else None
        if path is None:
            error = "facts_source_not_configured"
        else:
            raw = _load_facts_json(path)
            if not raw and error is None:
                error = "facts_file_empty_or_missing"

    archive_path = context.facts_path.with_name("facts_archive.json") if context and context.facts_path else None
    if archive_path is not None:
        raw_archive = _load_facts_json(archive_path)

    parsed_exclude_ids = _parse_csv_set(exclude_fact_ids)
    parsed_exclude_hashes = _parse_csv_set(exclude_hashes)
    facts, fact_stats = _select_forge_facts_with_stats(
        raw,
        min_importance=min_importance,
        include_absorbed=include_absorbed,
        limit=limit,
        exclude_ids=parsed_exclude_ids,
        exclude_hashes=parsed_exclude_hashes,
    )
    archive_fact: Optional[dict[str, Any]] = None
    archive_stats = {"archiveRawCount": len([item for item in raw_archive if isinstance(item, dict)]), "archiveFilteredCount": 0}
    if limit >= 5 and raw_archive:
        active_ids = {str(item.get("id") or "") for item in facts if item.get("id")}
        active_hashes = {str(item.get("hash") or "") for item in facts if item.get("hash")}
        archive_fact, archive_stats = _select_archive_distant_fact(
            raw_archive,
            min_importance=min_importance,
            include_absorbed=include_absorbed,
            exclude_ids=parsed_exclude_ids | active_ids,
            exclude_hashes=parsed_exclude_hashes | active_hashes,
        )
        if archive_fact:
            if len(facts) >= limit:
                facts = [*facts[: limit - 1], archive_fact]
            else:
                facts = [*facts, archive_fact][:limit]
            recent_count = len([item for item in facts if item.get("recentGuaranteed")])
            distant_count = len([item for item in facts if item.get("distantGuaranteed")])
            fact_stats["recentGuaranteedCount"] = recent_count
            fact_stats["distantGuaranteedCount"] = distant_count
            fact_stats["weightedRandomCount"] = max(0, len(facts) - recent_count - distant_count)

    fallback_reason = ""
    if error and not facts:
        fallback_reason = error
    elif not raw:
        fallback_reason = "facts_file_empty_or_missing"
    elif fact_stats.get("excludedCount", 0) > 0 and fact_stats.get("filteredCount", 0) == 0:
        fallback_reason = "all_available_facts_excluded"
    elif fact_stats.get("filteredCount", 0) == 0:
        fallback_reason = "no_facts_after_filter"
    elif len(facts) < limit:
        fallback_reason = "insufficient_facts"

    payload: dict[str, Any] = {
        "character": resolved_character,
        "factsSource": facts_source,
        "characterOverrideIgnored": override_ignored,
        "runtimeCharacterHintUsed": runtime_hint_used,
        "facts": facts,
        "requestedLimit": limit,
        "returnedCount": len(facts),
        "fallbackReason": fallback_reason,
        "archiveDistantCount": 1 if archive_fact else 0,
        **archive_stats,
        **fact_stats,
    }
    if error and not facts:
        payload["error"] = error
    return JSONResponse(payload)


@app.post("/arena/forge-card-story")
async def arena_forge_card_story(body: dict[str, Any]):
    request_id = f"forge-{uuid.uuid4().hex[:10]}"
    safe_body = body if isinstance(body, dict) else {}
    runtime_hint = str(
        safe_body.get("runtimeCharacterHint")
        or safe_body.get("runtime_character_hint")
        or safe_body.get("character")
        or ""
    ).strip()
    if not runtime_hint:
        return JSONResponse(
            {
                "success": False,
                "requestId": request_id,
                "storyGenerationStatus": "failed",
                "error": "active_neko_runtime_not_linked",
            }
        )
    route_started_at = time.perf_counter()
    card = safe_body.get("card") if isinstance(safe_body.get("card"), dict) else {}
    _forge_route_log(
        request_id,
        "request",
        sourceFactId=safe_body.get("sourceFactId") or safe_body.get("factId"),
        storyLead=safe_body.get("storyLead"),
        card={
            "attrName": card.get("attrName"),
        },
    )
    """用 NEKO 核心 LLM 配置把故事引子生成 Forged 卡牌专属小故事。"""
    try:
        from forge_story_generator import ForgeStoryGenerationError, generate_forge_card_story

        result = await generate_forge_card_story({**safe_body, "_requestId": request_id})
        elapsed_ms = round((time.perf_counter() - route_started_at) * 1000, 1)
        _forge_route_log(
            request_id,
            "success",
            provider=result.provider,
            model=result.model,
            sourceFactId=result.source_fact_id,
            storyChars=len(result.story),
            elapsedMs=elapsed_ms,
        )
        return JSONResponse(
            {
                "success": True,
                "requestId": request_id,
                "story": result.story,
                "storyGenerationStatus": "ready",
                "provider": result.provider,
                "model": result.model,
                "sourceFactId": result.source_fact_id,
            }
        )
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        _forge_route_log(
            request_id,
            "failed",
            error=error,
            errorType=type(exc).__name__,
            elapsedMs=round((time.perf_counter() - route_started_at) * 1000, 1),
        )
        if exc.__class__.__name__ != "ForgeStoryGenerationError":
            logger.warning("forge-card-story: generation failed: %s", error)
        return JSONResponse(
            {
                "success": False,
                "requestId": request_id,
                "storyGenerationStatus": "failed",
                "error": error,
            }
        )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "card_forge_server"}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 默认只监听 loopback：facts 含个人化记忆，禁止开箱即用就被局域网读取。
    # 若确需在其他网卡监听，请显式设置 NEKO_CARD_FORGE_HOST="0.0.0.0"
    # （并相应收紧 NEKO_CARD_FORGE_ALLOWED_ORIGINS）。
    #
    # app_dir 必传：reload=True 时 uvicorn 会 fork worker 子进程，再用
    # `"server:app"` 字符串 import-by-name；worker 不会继承 __main__ 里给
    # sys.path 加的 SERVER_ROOT，所以从项目根 (`uv run local_server/card_forge_server/server.py`)
    # 启动时 worker 找不到 `server` 模块。显式 app_dir=SERVER_ROOT 把目录交给 uvicorn
    # 自己加到 worker 的 sys.path 上。
    uvicorn.run(
        "server:app",
        host=os.environ.get("NEKO_CARD_FORGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("NEKO_CARD_FORGE_PORT", "3001")),
        reload=True,
        app_dir=str(SERVER_ROOT),
    )
