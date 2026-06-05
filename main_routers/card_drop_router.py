"""对话掉落卡片 —— NEKO 本地后端 → 云端 N.E.K.O.Servers 的代理。

前端（浏览器）没有云端身份（X-Client-Id 存在后端 cloudsave 本地状态里），所以由后端
代理转发并补上 X-Client-Id：

- GET  /api/card-drop/candidates?lanlan_name=...&size=5  → 云端 GET /api/cards/draw-candidates
- POST /api/card-drop/draw   {lanlan_name, fact_id|preset_id, prefer_tags?}
                                                          → 云端 POST /api/cards/draw

需 ``NEKO_SOCIAL_BASE_URL``（默认 http://localhost:8080）+ 本地已注册 client_id
（由 facts_sync 启动时 /api/clients/register 注册）。云端契约见 N.E.K.O.Servers
app/modules/cards/router.py。
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Body, HTTPException, Query

logger = logging.getLogger("neko.card_drop")

router = APIRouter(prefix="/api/card-drop", tags=["card-drop"])

_HTTP_TIMEOUT_SEC = 60.0
_DEFAULT_SOCIAL_BASE_URL = "http://localhost:8080"


def _social_base_url() -> str:
    """云端 base url；未配则用 dev 默认 localhost:8080。"""
    raw = (os.environ.get("NEKO_SOCIAL_BASE_URL", "") or "").strip().rstrip("/")
    return raw or _DEFAULT_SOCIAL_BASE_URL


def _get_client_id() -> str | None:
    """从 cloudsave 本地状态读 client_id（与 facts_sync / puller 同一来源）。"""
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        state = cm.load_cloudsave_local_state()
        if isinstance(state, dict):
            cid = state.get("client_id")
            if isinstance(cid, str) and cid:
                return cid
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: client_id read failed: %s", exc)
    return None


def _require_ctx() -> tuple[str, str]:
    cid = _get_client_id()
    if not cid:
        raise HTTPException(status_code=409, detail="client_not_registered")
    return _social_base_url(), cid


def _relay(r: httpx.Response):
    """透传云端响应：成功返 JSON；4xx/5xx 透传状态码 + detail。"""
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


@router.get("/candidates", summary="代理云端开卡候选（5选1）")
async def candidates_endpoint(
    lanlan_name: str = Query(..., min_length=1, max_length=64),
    size: int = Query(5, ge=1, le=10),
):
    base, cid = _require_ctx()
    url = f"{base}/api/cards/draw-candidates"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(
                url,
                headers={"X-Client-Id": cid},
                params={"lanlan_name": lanlan_name, "size": size},
            )
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)


@router.get("/collection", summary="代理云端「我的卡片」：收集册（含 rarity / 编号）")
async def collection_endpoint(
    limit: int = Query(100, ge=1, le=200),
):
    base, cid = _require_ctx()
    url = f"{base}/api/cards/mine"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, headers={"X-Client-Id": cid}, params={"limit": limit})
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)


@router.post("/test-trigger", summary="（调试）手动广播一次 card_drop_available，触发前端开卡演出")
async def test_trigger_endpoint(
    lanlan_name: str = Query("test", min_length=1, max_length=64),
):
    try:
        from app.main_server import _broadcast_to_all_connected
        n = await _broadcast_to_all_connected({
            "type": "card_drop_available",
            "lanlan_name": lanlan_name,
            "trigger_type": "manual_test",
        })
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"broadcast_failed: {exc}") from exc
    return {"broadcast_to": n, "lanlan_name": lanlan_name}


@router.post("/draw", summary="代理云端开卡：roll 稀有度 + 建卡（含唯一编号）")
async def draw_endpoint(payload: dict = Body(...)):
    base, cid = _require_ctx()
    url = f"{base}/api/cards/draw"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(
                url,
                headers={"X-Client-Id": cid, "Content-Type": "application/json"},
                json=payload,
            )
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)
