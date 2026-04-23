"""Settings workspace backend — model config / providers / api keys / ping.

Scope (PLAN §Workspace 5 + §Settings workspace):

- ``GET  /api/config/model_config``         return the active session's 4
                                             group configs (masked).
- ``PUT  /api/config/model_config``          replace the whole bundle.
- ``PUT  /api/config/model_config/{group}``  update a single group in place.
- ``GET  /api/config/providers``             flattened view of
                                             ``config/api_providers.json``
                                             assist_api_providers (for UI
                                             preset dropdown).
- ``GET  /api/config/api_keys_status``       masked status of
                                             ``tests/api_keys.json``.
- ``POST /api/config/api_keys/reload``       re-read api_keys.json from disk.
- ``POST /api/config/test_connection/{group}`` short ping through
                                             :class:`ChatOpenAI` to see if
                                             the config actually talks.

All mutating endpoints take the per-session lock so concurrent edits can't
leak into a mid-turn /chat/send pipeline (see ``SessionStore.session_operation``).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tests.testbench.api_keys_registry import (
    PROVIDER_TO_KEY_FIELD,
    get_api_keys_registry,
    preset_has_bundled_api_key,
)
from tests.testbench.logger import python_logger
from tests.testbench.model_config import (
    GROUP_KEYS,
    GroupKey,
    ModelConfigBundle,
    ModelGroupConfig,
)
from tests.testbench.pipeline.chat_runner import (
    ChatConfigError,
    resolve_group_config,
)
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)

router = APIRouter(prefix="/api/config", tags=["config"])


# ── helpers ─────────────────────────────────────────────────────────


def _require_session():
    """Return active session, HTTP 404 when none."""
    session = get_session_store().get()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_type": "NoActiveSession",
                "message": "No active session. POST /api/session first.",
            },
        )
    return session


def _load_bundle(session) -> ModelConfigBundle:
    """Session carries ``model_config`` as a dict (future-proof for snapshots).

    We lift it into :class:`ModelConfigBundle` on demand and write the serialized
    dict back. Callers mutate through the bundle and then call :func:`_store_bundle`.
    """
    return ModelConfigBundle.from_session_value(session.model_config)


def _store_bundle(session, bundle: ModelConfigBundle) -> None:
    session.model_config = bundle.model_dump()


# ── request models ──────────────────────────────────────────────────


class _UpdateGroupRequest(BaseModel):
    """Body for ``PUT /api/config/model_config/{group}``.

    Accepts a partial payload: unspecified fields retain their current value.
    """

    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None


class _TestConnectionRequest(BaseModel):
    """Optional overrides for the ping call.

    ``prompt`` defaults to a short Chinese smoke test because the testbench
    fleet is zh-CN first; swap through the body if you need an English ping.
    """

    prompt: str = "用一个字回复我: 好"


# ── model_config endpoints ──────────────────────────────────────────


@router.get("/model_config")
async def get_model_config() -> dict[str, Any]:
    """Return the full 4-group config (api_key fields masked)."""
    session = _require_session()
    bundle = _load_bundle(session)
    return {"groups": bundle.summary()}


@router.put("/model_config")
async def replace_model_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the whole bundle. Body shape: ``{chat: {...}, simuser: {...}, ...}``."""
    try:
        new_bundle = ModelConfigBundle.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError etc.
        raise HTTPException(
            status_code=422,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
        ) from exc

    store = get_session_store()
    try:
        async with store.session_operation("config.model_config.replace"):
            session = get_session_store().require()
            _store_bundle(session, new_bundle)
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc

    return {"groups": new_bundle.summary()}


@router.put("/model_config/{group}")
async def update_model_group(group: GroupKey, body: _UpdateGroupRequest) -> dict[str, Any]:
    """Patch one group. Unspecified fields keep their current value."""
    if group not in GROUP_KEYS:
        raise HTTPException(
            status_code=400,
            detail={"message": f"Unknown group: {group}"},
        )

    store = get_session_store()
    try:
        async with store.session_operation(f"config.model_config.update:{group}"):
            session = get_session_store().require()
            bundle = _load_bundle(session)
            current = bundle.get(group).model_dump()
            patch = body.model_dump(exclude_unset=True)
            merged = {**current, **patch}
            try:
                bundle.set(group, ModelGroupConfig.model_validate(merged))
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"error_type": type(exc).__name__, "message": str(exc)},
                ) from exc
            _store_bundle(session, bundle)
            updated = bundle.get(group).summary()
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc

    return {"group": group, "config": updated}


# ── providers / api_keys ────────────────────────────────────────────


@router.get("/providers")
async def list_providers() -> dict[str, Any]:
    """Return ``config/api_providers.json → assist_api_providers`` for UI.

    The UI uses the preset dropdown to auto-fill ``base_url`` / ``model`` /
    ``api_key`` (the latter comes from ``tests/api_keys.json`` when available).
    Fields are passed through as-is so adding a new provider only requires
    editing the JSON file.
    """
    try:
        from utils.api_config_loader import get_config
    except Exception as exc:  # noqa: BLE001 - defensive: loader crash shouldn't 500 whole route
        python_logger().exception("Failed to import api_config_loader")
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": type(exc).__name__,
                "message": f"api_config_loader import failed: {exc}",
            },
        ) from exc

    raw = get_config()
    assist = raw.get("assist_api_providers", {})
    registry = get_api_keys_registry()

    providers: list[dict[str, Any]] = []
    for key, profile in assist.items():
        key_field = PROVIDER_TO_KEY_FIELD.get(key)
        # ``preset_has_bundled_api_key`` is True when the preset ships its
        # own api_key (the ``free`` preset carries ``openrouter_api_key:
        # "free-access"``). UI uses this to display "此预设内置 API Key,
        # 无需填写" instead of the generic "未配置" warning for free tier.
        # Value itself is never echoed — only the boolean — to keep the
        # endpoint plaintext-free.
        providers.append({
            "key": profile.get("key", key),
            "name": profile.get("name", key),
            "description": profile.get("description", ""),
            "base_url": profile.get("openrouter_url", ""),
            "suggested_models": {
                "conversation": profile.get("conversation_model"),
                "summary": profile.get("summary_model"),
                "correction": profile.get("correction_model"),
                "emotion": profile.get("emotion_model"),
                "vision": profile.get("vision_model"),
                "agent": profile.get("agent_model"),
            },
            "is_free_version": bool(profile.get("is_free_version", False)),
            "api_key_field": key_field,
            "api_key_configured": registry.is_present(key_field) if key_field else False,
            "preset_api_key_bundled": preset_has_bundled_api_key(key),
        })
    return {"providers": providers}


@router.get("/api_keys_status")
async def api_keys_status() -> dict[str, Any]:
    """Report which known key fields are filled in ``tests/api_keys.json``.

    No plaintext ever leaves this endpoint.
    """
    return get_api_keys_registry().status_report()


@router.post("/api_keys/reload")
async def api_keys_reload() -> dict[str, Any]:
    """Force re-read api_keys.json from disk and return fresh status."""
    return get_api_keys_registry().reload()


# ── connectivity test ───────────────────────────────────────────────


@router.post("/test_connection/{group}")
async def test_connection(
    group: GroupKey,
    body: _TestConnectionRequest | None = None,
) -> dict[str, Any]:
    """Actually call the configured LLM with a short prompt.

    Returns a JSON shape the UI can render directly::

        {
          "ok": true,
          "latency_ms": 843,
          "model": "qwen-plus",
          "response_preview": "好",
          "token_usage": {...},
          "error": null
        }

    The op runs under the session lock to avoid racing with a /chat/send;
    state is labeled ``busy`` so the UI can disable unrelated buttons.
    """
    if group not in GROUP_KEYS:
        raise HTTPException(status_code=400, detail={"message": f"Unknown group: {group}"})
    payload = body or _TestConnectionRequest()

    store = get_session_store()
    try:
        async with store.session_operation(
            f"config.test_connection:{group}",
            state=SessionState.BUSY,
        ):
            session = get_session_store().require()
            # 走 chat_runner.resolve_group_config 统一三层兜底:
            #   1) 用户显式填 api_key
            #   2) 预设自带 api_key (free → "free-access")
            #   3) tests/api_keys.json 里的 provider 映射
            # 任一层命中即可. 只有三层都失败 (真的没 key) 才提示 MissingApiKey.
            # 过去这里手写了 `if not cfg.api_key: return MissingApiKey`, 导致免
            # 费预设即使后端能连也被前端拒绝.
            try:
                cfg = resolve_group_config(session, group)
            except ChatConfigError as exc:
                # 把 chat_runner 的错误码映射到 UI 期望的字段名 — 前端现有
                # toast 逻辑认 NotConfigured / MissingApiKey 这两个标签.
                error_type = {
                    "ChatModelNotConfigured": "NotConfigured",
                    "ChatApiKeyMissing": "MissingApiKey",
                }.get(exc.code, exc.code)
                return {
                    "ok": False,
                    "error": {
                        "type": error_type,
                        "message": exc.message,
                    },
                    "latency_ms": 0,
                }

            result = await _ping_chat(cfg, payload.prompt)
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"message": str(exc)}) from exc

    return result


async def _ping_chat(cfg: ModelGroupConfig, prompt: str) -> dict[str, Any]:
    """Single-turn sanity call through :class:`ChatOpenAI`.

    Catches everything and maps to a structured error dict so the UI can
    render the same shape regardless of SDK-level exception type.
    """
    try:
        from utils.llm_client import ChatOpenAI, HumanMessage
    except Exception as exc:  # noqa: BLE001
        python_logger().exception("llm_client import failed")
        return {
            "ok": False,
            "error": {
                "type": "ImportError",
                "message": f"utils.llm_client import failed: {exc}",
            },
            "latency_ms": 0,
        }

    started = time.perf_counter()
    try:
        client = ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            timeout=cfg.timeout or 30.0,
            max_retries=0,
        )
        try:
            # NOSTAMP(wire_tracker): connectivity ping, not a conversation
            # turn — stamping this would overwrite the real "last LLM wire"
            # snapshot with a 1-word credentials probe, polluting Prompt
            # Preview. The coverage smoke
            # (tests/testbench/smoke/p25_llm_call_site_stamp_coverage_smoke.py)
            # recognizes this sentinel and skips the call site.
            resp = await client.ainvoke([HumanMessage(content=prompt)])
        finally:
            await client.aclose()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - ping is best-effort
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        python_logger().info(
            "test_connection failed after %dms: %s: %s",
            elapsed_ms, type(exc).__name__, exc,
        )
        return {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "latency_ms": elapsed_ms,
            "model": cfg.model,
        }

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    content = (resp.content or "").strip()
    return {
        "ok": True,
        "latency_ms": elapsed_ms,
        "model": cfg.model,
        "response_preview": content[:200],
        "token_usage": (resp.response_metadata or {}).get("token_usage", {}),
        "error": None,
    }
