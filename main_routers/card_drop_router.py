"""Conversation card-drop thin-client routes for NEKO.

Responsibility split:
- NEKO handles local drop triggering, local memory candidate selection, and the
  card reveal animation.
- N.E.K.O.Servers generates and stores the final card from the selected memory
  text, including rarity, number, story, and art.
- Card collection browsing lives in the community web app, not in NEKO.

Endpoints:
- ``GET /api/card-drop/candidates`` reads local ``memory/<character>/facts.json``
  and returns weighted candidates with presets as fallback.
- ``POST /api/card-drop/draw`` proxies the selected source text to the cloud
  ``/api/cards/draw`` endpoint with ``X-Client-Id``.

The cloud contract lives in N.E.K.O.Servers ``app/modules/cards/router.py``.
"""

from __future__ import annotations

import html
import json
import logging
import os
import random
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger("neko.card_drop")

router = APIRouter(prefix="/api/card-drop", tags=["card-drop"])

_HTTP_TIMEOUT_SEC = 60.0
_DEFAULT_SOCIAL_BASE_URL = "http://localhost:8080"

# 真实记忆不足 size 时的预设补满文案（通用占位，与云端 memory_presets.yaml 同风格）。
_PRESETS = [
    "某个普通的傍晚，你们一起看着天色一点点暗下来，谁都没说话。",
    "TA 悄悄记下了你随口提过的一个小小愿望。",
    "下雨那天，你们躲在同一处屋檐下，听了很久的雨声。",
    "你第一次轻轻喊出 TA 名字的那一刻。",
    "深夜里一句没头没尾、却让人安心的晚安。",
    "一起沉默着，却一点也不觉得尴尬的那段时光。",
    "TA 学着你的口头禅，把你自己都逗笑了。",
    "你说过的一个小秘密，TA 一直替你好好守着。",
]


def _social_base_url() -> str:
    """Return the cloud base URL, falling back to the local dev default."""
    raw = (os.environ.get("NEKO_SOCIAL_BASE_URL", "") or "").strip().rstrip("/")
    return raw or _DEFAULT_SOCIAL_BASE_URL


def _get_client_id() -> str | None:
    """Read ``client_id`` from the same local cloudsave state as sync workers."""
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
    """Relay a cloud response, returning JSON or raising an HTTP error."""
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


# ---- 社区账号登录：JWT 存本地 community_auth.json；draw 时带 Authorization ----
_AUTH_FILENAME = "community_auth.json"


def _auth_path() -> Path | None:
    try:
        from utils.config_manager import get_config_manager
        return Path(get_config_manager().memory_dir).parent / _AUTH_FILENAME
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: auth path resolve failed: %s", exc)
        return None


def _load_auth() -> dict | None:
    p = _auth_path()
    if not p or not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _save_auth(data: dict) -> None:
    p = _auth_path()
    if not p:
        return
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("card_drop: save auth failed: %s", exc)


def _clear_auth() -> None:
    p = _auth_path()
    if p and p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _access_token() -> str | None:
    a = _load_auth()
    return a.get("access_token") if a else None


async def _store_session(base: str, access: str | None, refresh: str | None, user: dict) -> dict:
    """Store JWTs and bind the local client so guest cards migrate to the user.

    Email/password and Steam login share this path. The bind result is persisted
    and exposed through auth-status so client-binding conflicts are visible.
    """
    bind: dict = {"bound": False, "error": None}
    cid = _get_client_id()
    if not cid:
        bind["error"] = "client_not_registered"
    elif access:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
                r = await client.post(
                    f"{base}/api/auth/bind-client",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"client_id": cid},
                )
            if r.status_code < 400:
                bind["bound"] = True
            else:
                try:
                    bind["error"] = r.json().get("detail") or f"http_{r.status_code}"
                except (ValueError, KeyError, AttributeError):
                    bind["error"] = f"http_{r.status_code}"
                logger.info("card_drop: bind-client returned %s: %s", r.status_code, bind["error"])
        except (httpx.HTTPError, OSError) as exc:
            bind["error"] = "cloud_unreachable"
            logger.info("card_drop: bind-client after login failed: %s", exc)
    _save_auth({
        "access_token": access,
        "refresh_token": refresh,
        "user": {"display_name": user.get("display_name"), "email": user.get("email")},
        "bind": bind,
    })
    return bind


async def _finish_login(base: str, login_out: dict) -> tuple[dict, dict]:
    """Complete email/password login by storing JWTs and binding the client."""
    tokens = login_out.get("tokens") or {}
    user = login_out.get("user") or {}
    bind = await _store_session(base, tokens.get("access_token"), tokens.get("refresh_token"), user)
    return user, bind


# ---- Steam 登录：开浏览器到云端 OpenID → 云端验完重定向回本地 /steam-callback ----
# CSRF/会话固定防护：/steam-callback 用 access_token query 参数落地，是个本机端点，恶意网页
# 可能跨源 GET 它塞入攻击者 token（把用户游客卡 bind 到攻击者账号）。用一次性 pending 标记
# 把回调限定在「用户刚点过 Steam 登录」的短窗口内，挡掉无端调用。
_STEAM_PENDING_FILENAME = "community_steam_pending.json"
_STEAM_PENDING_TTL_SEC = 600  # 点登录后 10 分钟内必须完成回调


def _steam_pending_path() -> Path | None:
    p = _auth_path()
    return (p.parent / _STEAM_PENDING_FILENAME) if p else None


def _mark_steam_pending() -> None:
    p = _steam_pending_path()
    if not p:
        return
    try:
        p.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    except OSError as exc:
        logger.debug("card_drop: mark steam pending failed: %s", exc)


def _consume_steam_pending() -> bool:
    """Consume the one-time Steam pending marker if it exists and is fresh."""
    p = _steam_pending_path()
    if not p or not p.exists():
        return False
    ts = 0.0
    try:
        ts = float(json.loads(p.read_text(encoding="utf-8")).get("ts", 0))
    except (OSError, ValueError, TypeError):
        ts = 0.0
    try:
        p.unlink()
    except OSError:
        pass
    return bool(ts) and (time.time() - ts) <= _STEAM_PENDING_TTL_SEC


def _local_facts(lanlan_name: str) -> list[dict]:
    """Read local facts for one character, filtering private or empty entries."""
    try:
        from utils.config_manager import get_config_manager
        mem = Path(get_config_manager().memory_dir)
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: memory_dir read failed: %s", exc)
        return []
    fp = mem / lanlan_name / "facts.json"
    # 路径穿越防护：解析后必须仍在 memory_dir 下
    try:
        if mem.resolve() not in fp.resolve().parents:
            return []
    except OSError:
        return []
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("card_drop: facts.json read failed: %s", exc)
        return []
    out: list[dict] = []
    for f in (data if isinstance(data, list) else []):
        if not isinstance(f, dict):
            continue
        if f.get("private") is True or f.get("redacted") is True:
            continue
        text = f.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            imp = float(f.get("importance") or 0.0)
        except (TypeError, ValueError):
            imp = 0.0
        out.append({"text": text.strip(), "importance": imp})
    return out


def _weighted_sample(items: list[dict], k: int) -> list[dict]:
    """Sample up to ``k`` items without replacement, weighted by importance."""
    if k <= 0 or not items:
        return []
    if len(items) <= k:
        out = list(items)
        random.shuffle(out)
        return out
    scored: list[tuple[float, dict]] = []
    for it in items:
        w = max(float(it.get("importance") or 0.0) + 0.1, 1e-4)
        scored.append((random.random() ** (1.0 / w), it))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in scored[:k]]


@router.get("/candidates", summary="本地记忆 5选1 候选（不足用预设补满，不走云端）")
async def candidates_endpoint(
    lanlan_name: str = Query(..., min_length=1, max_length=64),
    size: int = Query(5, ge=1, le=10),
):
    facts = _local_facts(lanlan_name)
    chosen = _weighted_sample(facts, size)
    candidates = [
        {"kind": "fact", "text": f["text"], "importance": f["importance"], "is_preset": False}
        for f in chosen
    ]
    if len(candidates) < size:
        need = size - len(candidates)
        for text in random.sample(_PRESETS, min(need, len(_PRESETS))):
            candidates.append({"kind": "preset", "text": text, "is_preset": True})
    return {"lanlan_name": lanlan_name, "size": len(candidates), "candidates": candidates}


@router.post("/test-trigger", summary="（调试）手动广播一次 card_drop_available，触发前端开卡演出")
async def test_trigger_endpoint(
    lanlan_name: str = Query("test", min_length=1, max_length=64),
):
    try:
        from main_logic.agent_event_bus import broadcast_ws_event
        n = await broadcast_ws_event({
            "type": "card_drop_available",
            "lanlan_name": lanlan_name,
            "trigger_type": "manual_test",
        })
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"broadcast_failed: {exc}") from exc
    return {"broadcast_to": n, "lanlan_name": lanlan_name}


@router.get("/auth-status", summary="社区登录状态")
async def auth_status_endpoint():
    a = _load_auth()
    if a and a.get("access_token"):
        u = a.get("user") or {}
        # 老会话没存 bind 字段 → 视为已绑（向后兼容，正常单账号场景成立）
        bind = a.get("bind") or {"bound": True, "error": None}
        return {
            "logged_in": True,
            "user": {"display_name": u.get("display_name"), "email": u.get("email")},
            "bind": bind,
        }
    return {"logged_in": False, "user": None, "bind": None}


@router.post("/login", summary="邮箱密码登录社区账号（存 JWT + 迁移游客卡）")
async def login_endpoint(payload: dict = Body(...)):
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="missing_email_or_password")
    base = _social_base_url()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(f"{base}/api/auth/login", json={"email": email, "password": password})
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    user, bind = await _finish_login(base, _relay(r))
    return {"user": user, "bind": bind}


@router.post("/register", summary="邮箱密码注册社区账号（存 JWT + 迁移游客卡）")
async def register_endpoint(payload: dict = Body(...)):
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    display_name = (payload.get("display_name") or "").strip() or None
    if not email or not password:
        raise HTTPException(status_code=400, detail="missing_email_or_password")
    body = {"email": email, "password": password}
    if display_name:
        body["display_name"] = display_name
    base = _social_base_url()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(f"{base}/api/auth/register", json=body)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    user, bind = await _finish_login(base, _relay(r))
    return {"user": user, "bind": bind}


@router.post("/logout", summary="登出（清本地 JWT）")
async def logout_endpoint():
    _clear_auth()
    return {"logged_in": False}


def _neko_steam_callback_url(request: Request) -> str:
    """Return the local Steam callback URL using the request origin."""
    return f"{str(request.base_url).rstrip('/')}/api/card-drop/steam-callback"


_STEAM_CALLBACK_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录成功</title><style>
html,body{{margin:0;height:100%;background:#0f1020;color:#eef;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
.wrap{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center;padding:24px}}
.ok{{font-size:46px}}.t{{font-size:20px;font-weight:600}}.s{{font-size:14px;color:#9aa;max-width:360px;line-height:1.6}}
</style></head><body><div class="wrap">
<div class="ok">✦</div><div class="t">{title}</div>
<div class="s">{sub}</div></div>
<script>setTimeout(function(){{try{{window.close();}}catch(e){{}}}},1200);</script>
</body></html>"""


def _steam_callback_html(title: str, sub: str) -> HTMLResponse:
    return HTMLResponse(_STEAM_CALLBACK_PAGE.format(title=html.escape(title), sub=html.escape(sub)))


@router.get("/steam-login", summary="返回 Steam 登录授权 URL（前端用浏览器打开）")
async def steam_login_endpoint(request: Request):
    base = _social_base_url()
    callback = _neko_steam_callback_url(request)
    _mark_steam_pending()
    authorize_url = (
        f"{base}/api/auth/oauth/steam/authorize?redirect_to={quote(callback, safe='')}"
    )
    return {"authorize_url": authorize_url, "callback": callback}


@router.get(
    "/steam-callback",
    summary="Steam 登录回调：存 JWT + 迁移游客卡，返回提示页",
    response_class=HTMLResponse,
)
async def steam_callback_endpoint(
    access_token: str = Query(..., min_length=1),
    refresh_token: str | None = Query(None),
):
    # 只接受「用户刚发起过 Steam 登录」窗口内的回调，挡掉无端/重放调用（会话固定防护）。
    if not _consume_steam_pending():
        return _steam_callback_html("登录会话已失效", "请回到 NEKO 重新点一次「Steam 登录」。")
    base = _social_base_url()
    user: dict = {}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(
                f"{base}/api/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code == 200:
            user = (r.json() or {}).get("user") or {}
        else:
            logger.info("card_drop: steam-callback /me returned %s", r.status_code)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.info("card_drop: steam-callback fetch /me failed: %s", exc)
    bind = await _store_session(base, access_token, refresh_token, user)
    name = user.get("display_name") or "你"
    if bind.get("bound"):
        sub = "卡片会存进你的卡册了，可以关掉本页回到 NEKO。"
    elif bind.get("error") == "client_already_bound_to_other_user":
        sub = "已登录，但这台设备早先绑过别的社区账号，这次的卡留在原账号里。可关掉本页回到 NEKO。"
    else:
        sub = "已登录，但游客卡迁移没完成（稍后可重试）。可关掉本页回到 NEKO。"
    return _steam_callback_html(f"已登录，欢迎 {name}", sub)


@router.post("/draw", summary="代理云端用券铸造：消耗 payload.credit_id 那张券 → 用券稀有度建卡")
async def draw_endpoint(payload: dict = Body(...)):
    # 统一券经济：payload 须带 credit_id（云端 DrawRequest 必填）。本端点纯透传 json=payload，
    # 由铸造 UI 把选中候选的 source_text + credit_id 一并塞进来；稀有度继承券、不再 roll。
    base, cid = _require_ctx()
    headers = {"X-Client-Id": cid, "Content-Type": "application/json"}
    token = _access_token()  # 登录了就带 JWT → 云端把卡归到 user 账号
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base}/api/cards/draw"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(url, headers=headers, json=payload)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)


@router.get("/credits", summary="代理云端：列当前有效铸造券（供铸造 UI 显示数量/稀有度/到期）")
async def credits_endpoint():
    base, cid = _require_ctx()
    headers = {"X-Client-Id": cid}
    token = _access_token()  # 登录则按 user 账号查；否则按游客 client
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base}/api/forge/credits"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)
