"""Bounded desktop community API relay for remote Electron instances.

Remote clients use an instance session, never a Linux credential file or cloud
refresh token. The relay exposes only explicitly listed desktop features.
"""

import asyncio
import re
import time
import uuid
from urllib.parse import urljoin

import anyio

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from main_routers import card_drop_router as C, community_oauth as O
from utils.visit_wire import VISIT_ID_RE

router = APIRouter(tags=["community-remote-proxy"])


async def _community_access(request: Request):
    if await O._account_request_identity(request) is None or not C._local_request_source_allowed(request):
        return None, JSONResponse({"detail": "instance_authorization_required"}, status_code=403)
    status = await O.resolve_saved_oauth_status()
    snapshot = status.get("snapshot") or {}
    access = snapshot.get("access_token")
    if not access or status.get("rejected") or not status.get("logged_in"):
        return None, JSONResponse({"detail": "community_login_required"}, status_code=401)
    return {
        "access": access,
        "base_url": C._social_base_url().rstrip("/"),
    }, None


async def _community_json(client: httpx.AsyncClient, session: dict, method: str, path: str, **kwargs):
    response = await client.request(
        method,
        session["base_url"] + path,
        headers={"Authorization": f"Bearer {session['access']}", "Accept": "application/json"},
        follow_redirects=False,
        **kwargs,
    )
    try:
        body = response.json()
    except ValueError:
        body = {"detail": "community_invalid_response"}
    return response.status_code, body


async def _community_following(client: httpx.AsyncClient, session: dict, user_id: str):
    """Read every following page so mutual friends are not truncated at 100."""
    items = []
    offset = 0
    # The community API caps a page at 100. Keep a generous finite ceiling so
    # a malformed upstream cannot make this desktop relay loop forever.
    for _ in range(100):
        status, body = await _community_json(
            client,
            session,
            "GET",
            f"/api/users/{user_id}/following?limit=100&offset={offset}",
        )
        if status != 200:
            return status, body
        if not isinstance(body, dict):
            return 502, {"detail": "community_invalid_response"}
        page = body.get("items")
        if not isinstance(page, list):
            return 502, {"detail": "community_invalid_response"}
        items.extend(page)
        offset += len(page)
        total = body.get("total")
        if not page or len(page) < 100 or (isinstance(total, int) and offset >= total):
            return 200, {**body, "items": items}
    return 502, {"detail": "community_invalid_response"}


async def _community_notifications(client: httpx.AsyncClient, session: dict):
    """Read every unread page so invitation reconciliation is authoritative."""
    items = []
    offset = 0
    for _ in range(100):
        status, body = await _community_json(
            client,
            session,
            "GET",
            f"/api/notifications?unread=true&limit=100&offset={offset}",
        )
        if status != 200 or not isinstance(body, dict):
            return status, body
        page = body.get("items")
        if not isinstance(page, list):
            return 502, {"detail": "community_invalid_response"}
        items.extend(page)
        offset += len(page)
        total = body.get("total")
        if not page or len(page) < 100 or (isinstance(total, int) and offset >= total):
            return 200, {**body, "items": items}
    return 502, {"detail": "community_invalid_response"}


def _absolute_community_url(base_url: str, value) -> str | None:
    text = str(value or "").strip()
    return urljoin(base_url + "/", text) if text else None


@router.get("/api/community/visit-hub")
async def visit_hub(request: Request):
    """Return only the signed-in account's real mutuals and live invitations."""
    session, error = await _community_access(request)
    if error is not None:
        return error
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
            me_status, me = await _community_json(client, session, "GET", "/api/users/me")
            profile = me.get("user") if isinstance(me, dict) and isinstance(me.get("user"), dict) else me
            if me_status != 200 or not isinstance(profile, dict) or not profile.get("id"):
                return JSONResponse(me, status_code=me_status)
            user_id = str(profile["id"])
            (following_status, following), (notification_status, notifications), (presence_status_code, presence_body) = await asyncio.gather(
                _community_following(client, session, user_id),
                _community_notifications(client, session),
                _community_json(client, session, "GET", "/api/users/me/presence"),
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)
    if following_status != 200:
        return JSONResponse(following, status_code=following_status)

    friends = []
    for item in following.get("items", []) if isinstance(following, dict) else []:
        if not isinstance(item, dict) or item.get("is_mutual") is not True or not item.get("id"):
            continue
        friends.append({
            "id": str(item["id"]),
            "display_name": item.get("display_name") or item.get("username") or "喵友",
            "username": item.get("username"),
            "avatar_url": _absolute_community_url(session["base_url"], item.get("avatar_url")),
            "online": item.get("online") is True,
            "presence_status": (
                item.get("presence_status")
                if item.get("presence_status") in {"online", "away", "offline"}
                else "online" if item.get("online") is True else "offline"
            ),
            "last_seen_at": item.get("last_seen_at"),
        })

    incoming = None
    incoming_invite_ids = []
    invite_responses = []
    now = time.time()
    notifications_ok = (
        notification_status == 200
        and isinstance(notifications, dict)
        and isinstance(notifications.get("items"), list)
    )
    if notifications_ok:
        for item in notifications.get("items", []):
            payload = item.get("payload") if isinstance(item, dict) else None
            if not isinstance(payload, dict):
                continue
            if payload.get("type") == "visit_invite_response":
                invite_responses.append({
                    "id": str(item.get("id") or ""),
                    "actor_id": str(payload.get("actor_id") or ""),
                    "actor_display_name": payload.get("actor_display_name") or "喵友",
                    "visit_id": str(payload.get("visit_id") or ""),
                    "accepted": payload.get("accepted") is True,
                })
                continue
            if payload.get("type") != "visit_invite":
                continue
            try:
                expires_at = float(payload.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at and expires_at <= now:
                continue
            notification_id = str(item.get("id") or "")
            if notification_id:
                incoming_invite_ids.append(notification_id)
            if incoming is not None:
                continue
            incoming = {
                "id": notification_id,
                "actor_id": str(payload.get("actor_id") or ""),
                "actor_display_name": payload.get("actor_display_name") or "喵友",
                "actor_avatar_url": _absolute_community_url(session["base_url"], payload.get("actor_avatar_url")),
                "visit_id": str(payload.get("visit_id") or ""),
                # Upgrade compatibility only; new directed invitations never carry a code.
                "invite_code": str(payload.get("invite_code") or ""),
                "expires_at": expires_at or None,
            }

    return {
        "logged_in": True,
        "account": {
            "id": user_id,
            "display_name": profile.get("display_name") or profile.get("username") or "我",
            "username": profile.get("username"),
            "avatar_url": _absolute_community_url(session["base_url"], profile.get("avatar_url")),
            "presence_status": (
                presence_body.get("status")
                if presence_status_code == 200
                and isinstance(presence_body, dict)
                and presence_body.get("status") in {"online", "away", "invisible"}
                else "online"
            ),
        },
        "friends": friends,
        "notifications_ok": notifications_ok,
        "incoming_invite_ids": incoming_invite_ids,
        "incoming_invite": incoming,
        "invite_responses": invite_responses,
    }


@router.put("/api/community/presence")
async def update_community_presence(request: Request):
    """Set the signed-in account's explicit friend-visible community state."""
    session, error = await _community_access(request)
    if error is not None:
        return error
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"detail": "invalid_payload"}, status_code=400)
    selected = payload.get("status") if isinstance(payload, dict) else None
    if selected not in {"online", "away", "invisible"}:
        return JSONResponse({"detail": "invalid_presence_status"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
            status_code, body = await _community_json(
                client,
                session,
                "PUT",
                "/api/users/me/presence",
                json={"status": selected},
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)
    return JSONResponse(body, status_code=status_code)


@router.post("/api/community/visit-invitations")
async def send_visit_invitation(request: Request):
    session, error = await _community_access(request)
    if error is not None:
        return error
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"detail": "invalid_payload"}, status_code=400)
    target = str(payload.get("target_user_id") or "") if isinstance(payload, dict) else ""
    visit_id = str(payload.get("visit_id") or "") if isinstance(payload, dict) else ""
    try:
        uuid.UUID(target)
    except ValueError:
        return JSONResponse({"detail": "invalid_target_user"}, status_code=400)
    if VISIT_ID_RE.fullmatch(visit_id) is None:
        return JSONResponse({"detail": "invalid_visit_id"}, status_code=400)
    upstream_payload = {"visit_id": visit_id}
    expires_at = payload.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        upstream_payload["expires_at"] = expires_at
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
            status_code, body = await _community_json(
                client, session, "POST", f"/api/users/{target}/visit-invite", json=upstream_payload,
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)
    return JSONResponse(body, status_code=status_code)


@router.post("/api/community/visit-invitations/{notification_id}/read")
async def read_visit_invitation(request: Request, notification_id: str):
    session, error = await _community_access(request)
    if error is not None:
        return error
    try:
        uuid.UUID(notification_id)
    except ValueError:
        return JSONResponse({"detail": "invalid_notification"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
            status_code, body = await _community_json(
                client, session, "POST", f"/api/notifications/{notification_id}/read",
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)
    if status_code == 204:
        return JSONResponse({}, status_code=200)
    return JSONResponse(body, status_code=status_code)


@router.post("/api/community/visit-invitations/{notification_id}/respond")
async def respond_visit_invitation(request: Request, notification_id: str):
    """Relay one explicit accept/decline result to the invitation sender."""
    session, error = await _community_access(request)
    if error is not None:
        return error
    try:
        uuid.UUID(notification_id)
    except ValueError:
        return JSONResponse({"detail": "invalid_notification"}, status_code=400)
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"detail": "invalid_payload"}, status_code=400)
    accepted = payload.get("accepted") if isinstance(payload, dict) else None
    if not isinstance(accepted, bool):
        return JSONResponse({"detail": "accepted_required"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
            status_code, body = await _community_json(
                client,
                session,
                "POST",
                f"/api/notifications/{notification_id}/visit-response",
                json={"accepted": accepted},
            )
    except httpx.HTTPError:
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)
    if status_code == 204:
        return JSONResponse({}, status_code=200)
    return JSONResponse(body, status_code=status_code)


async def _relay(request: Request):
    if await O._account_request_identity(request) is None or not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "instance_authorization_required"}, status_code=403)
    status = await O.resolve_saved_oauth_status()
    snapshot = status.get("snapshot") or {}
    access = snapshot.get("access_token")
    if not access or status.get("rejected"):
        return JSONResponse({"detail": "community_login_required"}, status_code=401)
    path = request.url.path
    stream = path == "/api/notifications/stream"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30), follow_redirects=False)
    try:
        # Query/body fields cannot change the fixed upstream origin or endpoint.
        upstream = client.build_request(
            request.method, C._social_base_url().rstrip("/") + path,
            params=request.query_params,
            content=await request.body(),
            headers={"Authorization": f"Bearer {access}",
                     "Accept": "text/event-stream" if stream else "application/json",
                     "Content-Type": request.headers.get("content-type", "application/json")},
        )
        response = await client.send(upstream, stream=True)
        if stream:
            upstream.extensions["timeout"]["read"] = None
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"detail": "community_unavailable"}, status_code=502)

    async def chunks():
        current = snapshot
        next_account_check = 0
        try:
            async for chunk in response.aiter_bytes():
                if time.monotonic() >= next_account_check:
                    current = await asyncio.to_thread(C._desktop_session_snapshot)
                    next_account_check = time.monotonic() + 1
                same_account = bool(current) and (
                    current.get("local_user_id") == snapshot.get("local_user_id")
                    if snapshot.get("local_user_id")
                    else current.get("access_token") == access
                )
                if not same_account:
                    break
                yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await response.aclose()
                await client.aclose()

    return StreamingResponse(chunks(), status_code=response.status_code,
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                             media_type="text/event-stream" if stream else "application/json")


@router.get("/api/notifications/stream")
async def notifications(request: Request):
    return await _relay(request)


@router.get("/api/forge/credits")
async def credits(request: Request):
    return await _relay(request)


@router.post("/api/forge/credits/grant")
@router.post("/api/forge/credits/drop-events/claim")
async def credit_change(request: Request):
    return await _relay(request)


@router.post("/api/forge/credits/drop-events/{event_id}/ack")
async def credit_ack(request: Request, event_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id):
        return JSONResponse({"detail": "invalid_drop_event"}, status_code=400)
    return await _relay(request)
