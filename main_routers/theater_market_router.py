"""Market package imports; the local theater selector owns Session entry."""

import asyncio
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Request

from main_routers import numeric_theater_router as theater
from services.theater.market_selection import market_story
from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_registry import MAX_PACKAGE_BYTES, NumericV2PackageError
from services.theater.numeric_v2_storage_transaction import run_storage_mutation
from services.theater.numeric_v2_store import numeric_v2_story_session_guard
from utils.character_memory import character_config_mutation_lock


router = APIRouter(prefix="/api/theater-market", tags=["theater-market"])
_launches: dict[str, dict] = {}
_TTL = 30 * 60
_MAX_LAUNCHES = 128


def _prune():
    now = time.monotonic()
    for key in list(_launches):
        if _launches[key]["expires"] < now:
            del _launches[key]


def _launch(request_id):
    _prune()
    value = _launches.get(request_id)
    if value is None:
        raise ValueError("theater_market_request_expired")
    binding = theater._current_catgirl_binding(theater.get_config_manager())
    if value["character_id"] != binding["character_id"]:
        raise ValueError("catgirl_changed_requires_new_session")
    return value


def _public(value):
    return {key: value.get(key) for key in (
        "request_id", "phase", "reason", "story_id", "character_id",
    )}


def _mutation(request, payload=None):
    return theater._validate_local_mutation_request(
        request, payload=payload, error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )


@router.post("/launch")
async def create_launch(request: Request):
    payload = await theater._json_object(request)
    refused = _mutation(request, payload)
    if refused is not None:
        return refused
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return theater._error("theater_market_local_required", 400)
    _prune()
    if len(_launches) >= _MAX_LAUNCHES:
        return theater._error("theater_market_busy", 429)
    binding = theater._current_catgirl_binding(theater.get_config_manager())
    selection = None
    try:
        if payload.get("share_id") is not None:
            selection = await market_story(int(payload["share_id"]))
            if (selection["revision"] != payload.get("revision")
                    or selection["file_sha256"] != payload.get("file_sha256")):
                raise ValueError("theater_market_file_changed")
        elif payload.get("random") is True:
            selection = await market_story()
        from utils.market_config import get_market_web_url

        # Pairing is issued by the running Plugin Server, never its re-imported module.
        from config.network import resolve_user_plugin_base
        import httpx
        plugin_base = resolve_user_plugin_base()
        async with httpx.AsyncClient(timeout=8.0, proxy=None, trust_env=False) as client:
            pair = await client.post(
                f"{plugin_base}/market/pair-code",
                headers={"Origin": plugin_base},
            )
            pair.raise_for_status()
            code = pair.json()["one_time_code"]
        request_id = secrets.token_urlsafe(24)
        value = {
            "request_id": request_id, "character_id": binding["character_id"],
            "phase": "browsing", "selection": selection, "lock": asyncio.Lock(),
            "expires": time.monotonic() + _TTL,
        }
        _launches[request_id] = value
        path = f"/shares/{selection['id']}?kind=theater&neko_auto=1" if selection else "/theater"
        fragment = urlencode({
            "neko_origin": str(request.base_url).rstrip("/"), "neko_code": code,
            "neko_request": request_id,
        })
        # Market currently uses HashRouter; pairing lives in that route's query.
        separator = "&" if "?" in path else "?"
        return {"ok": True, **_public(value), "url": get_market_web_url().rstrip("/") + "/#" + path + separator + fragment}
    except Exception as error:
        if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        return theater._error(str(error) if isinstance(error, ValueError) else "theater_market_unavailable", 502)


@router.get("/launch/{request_id}")
async def launch_status(request_id: str):
    try:
        return {"ok": True, **_public(_launch(request_id))}
    except ValueError as error:
        return theater._error(str(error), 409)


@router.post("/launch/{request_id}/import")
async def import_market_story(request_id: str, request: Request, share_id: int, revision: int, file_sha256: str):
    refused = _mutation(request)
    if refused is not None:
        return refused
    try:
        value = _launch(request_id)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_PACKAGE_BYTES:
                return theater._error("numeric_story_package_too_large", 413)
            raw.extend(chunk)
        if hashlib.sha256(raw).hexdigest() != file_sha256:
            return theater._error("theater_market_hash_mismatch", 422)
        selected = await market_story(share_id)
        if selected["revision"] != revision or selected["file_sha256"] != file_sha256 or selected["byte_size"] != len(raw):
            return theater._error("theater_market_file_changed", 409)
        async with value["lock"]:
            _launch(request_id)
            if value["selection"] and value["selection"] != selected:
                return theater._error("theater_market_selection_changed", 409)
            if value["phase"] != "browsing":
                return {"ok": True, **_public(value)}
            config = theater.get_config_manager()
            registry = await theater._registry(config)
            try:
                package = json.loads(raw)
            except (ValueError, RecursionError):
                return theater._error("numeric_story_package_invalid_json", 422)
            compiled = await asyncio.to_thread(registry.compile_for_import, package)
            async with character_config_mutation_lock, numeric_v2_story_session_guard(theater._numeric_root(config), compiled.story_id):
                _launch(request_id)
                theater._assert_story_recovered(registry, compiled.story_id)
                if await asyncio.to_thread(registry.package_file_exists, compiled.story_id):
                    existing = await asyncio.to_thread(registry.package_path(compiled.story_id).read_text, encoding="utf-8")
                    if registry.compile_for_import(json.loads(existing)).package_hash != compiled.package_hash:
                        return theater._error("numeric_story_exists", 409)
                else:
                    await theater._assert_numeric_writable(config, "packages")
                    await run_storage_mutation(
                        theater._numeric_write_transaction(config, registry.root.parent.parent),
                        registry.import_package, compiled.story,
                    )
            value.update(selection=selected, story_id=compiled.story_id, phase="imported")
            return {"ok": True, **_public(value)}
    except NumericV2CompileError as error:
        return theater.JSONResponse({"ok": False, "reason": "numeric_v2_contract_invalid",
                                     "issues": [issue.__dict__ for issue in error.issues]}, status_code=422)
    except (ValueError, NumericV2PackageError) as error:
        return theater._error(str(error), 409)
    except Exception:
        return theater._error("theater_market_unavailable", 502)
