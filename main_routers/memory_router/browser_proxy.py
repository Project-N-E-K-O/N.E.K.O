# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Memory browser proxy endpoints forwarding to memory_server."""

from fastapi import Request
from utils.character_name import validate_character_name
from utils.cloudsave_runtime import MaintenanceModeError
from fastapi.responses import JSONResponse, Response

from main_routers import memory_router as _mr
from ._shared import (
    logger,
    router,
)

async def _proxy_memory_browser(
    method: str,
    internal_path: str,
    *,
    json_body: dict | None = None,
    timeout: float = 30.0,
) -> JSONResponse:
    """Forward a memory-browser API request to memory_server and preserve status codes."""
    from config import MEMORY_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}{internal_path}"
    try:
        client = get_internal_http_client()
        if method == "GET":
            resp = await client.get(url, timeout=timeout)
        else:
            resp = await client.post(url, json=json_body or {}, timeout=timeout)
        try:
            body = resp.json()
        except Exception:
            body = {"success": resp.status_code < 400, "error": resp.text}
        if resp.status_code >= 400:
            detail = body.get("detail") or body.get("error") or f"HTTP {resp.status_code}"
            return JSONResponse(
                {"success": False, "error": str(detail)},
                status_code=resp.status_code,
            )
        return JSONResponse(body, status_code=resp.status_code)
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.warning("Memory browser proxy %s %s failed: %s", method, internal_path, exc)
        return JSONResponse(
            {"success": False, "error": "Memory service unavailable"},
            status_code=503,
        )


def _validate_browser_character_or_400(character_name: str) -> JSONResponse | None:
    validation = validate_character_name(character_name, allow_dots=True, max_length=50)
    if not validation.ok and validation.code != "reserved_route_name":
        return JSONResponse(
            {"success": False, "error": validation.code or "Invalid character name"},
            status_code=400,
        )
    return None


_MANUAL_LLM_COOLDOWN_SECONDS = 15.0


_manual_llm_last_trigger: dict[tuple[str, str], float] = {}


def _check_manual_llm_cooldown(action: str, character_name: str) -> JSONResponse | None:
    import time
    now = time.monotonic()
    key = (action, character_name)
    last = _manual_llm_last_trigger.get(key)
    if last is not None and (now - last) < _MANUAL_LLM_COOLDOWN_SECONDS:
        retry_after = max(1, int(_MANUAL_LLM_COOLDOWN_SECONDS - (now - last) + 0.999))
        return JSONResponse(
            {
                "success": False,
                "error": f"Please wait {retry_after}s before triggering this action again.",
                "retry_after": retry_after,
            },
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )
    if len(_manual_llm_last_trigger) > 256:
        cutoff = now - _MANUAL_LLM_COOLDOWN_SECONDS * 2
        for k in [k for k, ts in _manual_llm_last_trigger.items() if ts < cutoff]:
            _manual_llm_last_trigger.pop(k, None)
    _manual_llm_last_trigger[key] = now
    return None


async def _parse_and_validate_browser_post(
    request: Request,
) -> tuple[dict | None, JSONResponse | None]:
    """Require JSON content-type and valid local CSRF/Origin on memory-browser POSTs."""
    from main_routers.system_router._shared import _validate_local_mutation_request

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        return None, JSONResponse(
            {"success": False, "error": "Content-Type must be application/json"},
            status_code=415,
        )
    try:
        payload = await request.json()
    except Exception:
        return None, JSONResponse(
            {"success": False, "error": "Invalid JSON body"},
            status_code=400,
        )
    if not isinstance(payload, dict):
        return None, JSONResponse(
            {"success": False, "error": "Payload must be an object"},
            status_code=400,
        )
    csrf_err = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"success": False},
    )
    if csrf_err is not None:
        return None, csrf_err
    return payload, None


@router.get('/overview/{catgirl_name}')
async def get_memory_overview(catgirl_name: str):
    """Return Imprint stats/warm summary, long-term correction groups, and 6-axis health diagnostics."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "GET",
        f"/internal/memory/browser/overview/{quote(catgirl_name, safe='')}",
        timeout=25.0,
    )


@router.post('/imprint_note/{catgirl_name}')
async def generate_memory_imprint_note(catgirl_name: str, request: Request):
    """Manually trigger warm relationship summary/commentary/blessing generation."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    cooldown_err = _check_manual_llm_cooldown("imprint_note", catgirl_name)
    if cooldown_err is not None:
        return cooldown_err
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/imprint_note/{quote(catgirl_name, safe='')}",
        json_body={"language": payload.get("language") if isinstance(payload, dict) else None},
        timeout=60.0,
    )


@router.post('/search/{catgirl_name}')
async def search_character_memory(catgirl_name: str, request: Request):
    """Search long-term persona, reflections, and facts for one character."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/search/{quote(catgirl_name, safe='')}",
        json_body=payload or {},
        timeout=20.0,
    )


def _sync_local_directives_cache(character_name: str, *, mark_pending: bool = False) -> None:
    """Refresh this process's UserDirectivesManager after a memory_server mutation."""
    try:
        from memory.user_directives import get_user_directives_manager
        dm = get_user_directives_manager()
        dm.invalidate_cache(character_name)
        if mark_pending:
            dm.mark_pending_injection(character_name)
    except Exception as exc:
        logger.debug("Local directives cache sync skipped for %s: %s", character_name, exc)


async def _proxy_character_mutation(
    request: Request,
    internal_path: str,
    *,
    on_success=None,
) -> JSONResponse:
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    character_name = str((payload or {}).get("character_name") or "").strip()
    err_resp = _mr._validate_browser_character_or_400(character_name)
    if err_resp is not None:
        return err_resp
    resp = await _mr._proxy_memory_browser("POST", internal_path, json_body=payload, timeout=25.0)
    if on_success is not None and 200 <= resp.status_code < 300:
        on_success(character_name)
    return resp


@router.post('/persona/edit')
async def edit_persona_memory_entry(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/persona/edit")


@router.post('/persona/flags')
async def set_persona_memory_flags(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/persona/flags")


@router.post('/persona/archive')
async def archive_persona_memory_entry(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/persona/archive")


@router.post('/persona/restore_version')
async def restore_persona_memory_version(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/persona/restore_version")


@router.post('/correction/resolve')
async def resolve_memory_correction(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/correction/resolve")


@router.post('/reflection/review')
async def review_memory_reflection(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/reflection/review")


@router.post('/fact/edit')
async def edit_memory_fact(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/fact/edit")


@router.post('/fact/archive')
async def archive_memory_fact(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/fact/archive")


@router.post('/duplicate/merge')
async def merge_duplicate_memory(request: Request):
    return await _proxy_character_mutation(request, "/internal/memory/browser/duplicate/merge")


@router.post('/directive/add')
async def add_memory_directive(request: Request):
    return await _proxy_character_mutation(
        request,
        "/internal/memory/browser/directive/add",
        on_success=lambda name: _sync_local_directives_cache(name, mark_pending=True),
    )


@router.post('/directive/remove')
async def remove_memory_directive(request: Request):
    return await _proxy_character_mutation(
        request,
        "/internal/memory/browser/directive/remove",
        on_success=lambda name: _sync_local_directives_cache(name, mark_pending=False),
    )


@router.get('/star_map/{catgirl_name}')
async def get_memory_star_map(catgirl_name: str):
    """Return read-only 2D Memory Star Map and cached nebula theme labels."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "GET",
        f"/internal/memory/browser/star_map/{quote(catgirl_name, safe='')}",
        timeout=25.0,
    )


@router.post('/star_map/label_clusters/{catgirl_name}')
async def label_memory_star_map_clusters(catgirl_name: str, request: Request):
    """Manually trigger summary-tier cluster theme labeling for the Star Map."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    cooldown_err = _check_manual_llm_cooldown("star_map_labels", catgirl_name)
    if cooldown_err is not None:
        return cooldown_err
    cooldown_key = ("star_map_labels", catgirl_name)
    cooldown_stamp = _manual_llm_last_trigger.get(cooldown_key)
    from urllib.parse import quote
    resp = await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/star_map/label_clusters/{quote(catgirl_name, safe='')}",
        json_body={"language": payload.get("language") if isinstance(payload, dict) else None},
        timeout=60.0,
    )
    if getattr(resp, "status_code", None) == 409 and _manual_llm_last_trigger.get(cooldown_key) == cooldown_stamp:
        # No summary model configured: nothing was sent, so the user may retry
        # right after setting one up. Only this request's own mark is dropped;
        # a newer request's cooldown stays.
        _manual_llm_last_trigger.pop(cooldown_key, None)
    return resp


@router.get('/lineage/{catgirl_name}')
async def get_memory_lineage_story(catgirl_name: str, kind: str, entry_id: str):
    """Return 3-step lineage story for a single memory entry."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    if kind not in ("persona", "reflection", "fact") or not entry_id:
        return JSONResponse({"success": False, "error": "Invalid kind or entry_id"}, status_code=400)
    from urllib.parse import quote, urlencode
    qs = urlencode({"kind": kind, "entry_id": entry_id})
    return await _mr._proxy_memory_browser(
        "GET",
        f"/internal/memory/browser/lineage/{quote(catgirl_name, safe='')}?{qs}",
        timeout=20.0,
    )


@router.post('/related/{catgirl_name}')
async def get_related_memories(catgirl_name: str, request: Request):
    """Return closely related memories for cascade review after editing/forgetting."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/related/{quote(catgirl_name, safe='')}",
        json_body=payload or {},
        timeout=20.0,
    )


@router.get('/snapshots/{catgirl_name}')
async def list_memory_snapshots(catgirl_name: str):
    """List safety checkpoints for one character."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "GET",
        f"/internal/memory/browser/snapshots/{quote(catgirl_name, safe='')}",
        timeout=20.0,
    )


@router.post('/snapshots/create/{catgirl_name}')
async def create_memory_snapshot(catgirl_name: str, request: Request):
    """Create a safety checkpoint for one character."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/snapshots/create/{quote(catgirl_name, safe='')}",
        json_body=payload or {},
        timeout=25.0,
    )


@router.post('/snapshots/restore/{catgirl_name}')
async def restore_memory_snapshot(catgirl_name: str, request: Request):
    """Restore a safety checkpoint with automatic pre-restore backup."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    if not (payload or {}).get("snapshot_id"):
        return JSONResponse({"success": False, "error": "snapshot_id is required"}, status_code=400)
    from urllib.parse import quote
    resp = await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/snapshots/restore/{quote(catgirl_name, safe='')}",
        json_body=payload,
        timeout=30.0,
    )
    if 200 <= resp.status_code < 300:
        _sync_local_directives_cache(catgirl_name, mark_pending=False)
    return resp


@router.get('/export/{catgirl_name}')
async def export_memory_archive(catgirl_name: str, mode: str = "keepsake"):
    """Download keepsake or redacted developer diagnostics ZIP archive."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    norm_mode = str(mode or "keepsake").strip().lower()
    if norm_mode not in ("keepsake", "diagnostics"):
        return JSONResponse({"success": False, "error": "Invalid export mode"}, status_code=400)
    from urllib.parse import quote, urlencode
    from config import MEMORY_SERVER_PORT
    from utils.internal_http_client import get_internal_http_client

    qs = urlencode({"mode": norm_mode})
    url = f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/browser/export/{quote(catgirl_name, safe='')}?{qs}"
    try:
        client = get_internal_http_client()
        resp = await client.get(url, timeout=45.0)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail") or body.get("error") or f"HTTP {resp.status_code}"
            except Exception:
                detail = f"HTTP {resp.status_code}"
            return JSONResponse({"success": False, "error": str(detail)}, status_code=resp.status_code)
        headers = {}
        cd = resp.headers.get("content-disposition")
        if cd:
            headers["Content-Disposition"] = cd
        return Response(
            content=resp.content,
            media_type="application/zip",
            headers=headers,
        )
    except MaintenanceModeError:
        raise
    except Exception as exc:
        logger.warning("Memory browser export proxy failed: %s", exc)
        return JSONResponse(
            {"success": False, "error": "Memory service unavailable"},
            status_code=503,
        )


@router.get('/lineage_graph/{catgirl_name}')
async def get_memory_lineage_graph(catgirl_name: str):
    """Return Developer Mode 5-lane causal DAG."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "GET",
        f"/internal/memory/browser/lineage_graph/{quote(catgirl_name, safe='')}",
        timeout=25.0,
    )


@router.post('/deep_conflicts/{catgirl_name}')
async def run_memory_deep_conflict_check(catgirl_name: str, request: Request):
    """Manually trigger Developer Mode summary-tier deep conflict audit."""
    err_resp = _mr._validate_browser_character_or_400(catgirl_name)
    if err_resp is not None:
        return err_resp
    payload, post_err = await _mr._parse_and_validate_browser_post(request)
    if post_err is not None:
        return post_err
    cooldown_err = _check_manual_llm_cooldown("deep_conflicts", catgirl_name)
    if cooldown_err is not None:
        return cooldown_err
    from urllib.parse import quote
    return await _mr._proxy_memory_browser(
        "POST",
        f"/internal/memory/browser/deep_conflicts/{quote(catgirl_name, safe='')}",
        json_body={"language": payload.get("language") if isinstance(payload, dict) else None},
        timeout=75.0,
    )
