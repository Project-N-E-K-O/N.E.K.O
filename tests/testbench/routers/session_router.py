"""Session lifecycle endpoints.

Exposes the surface the browser needs for the single-active-session
model:

- ``POST   /api/session``               create a fresh session (+ sandbox)
- ``GET    /api/session``               inspect the current session
- ``DELETE /api/session``               tear it down (ConfigManager restored)
- ``GET    /api/session/state``         compact state for UI polling
- ``POST   /api/session/reset``         three-tier reset (P20)
- ``GET    /api/session/saved``         list saved session archives (P21)
- ``POST   /api/session/save``          save current session (overwrite)
- ``POST   /api/session/save_as``       save current session under new name
- ``POST   /api/session/load/{name}``   load a saved archive (replaces session)
- ``DELETE /api/session/saved/{name}``  remove a saved archive
- ``POST   /api/session/import``        import an inline archive payload

P21 adds the Save / Load / Import / Delete-archive surface. All archive
work is backed by :mod:`tests.testbench.pipeline.persistence` (pure sync
module); the router only orchestrates asyncio locks + HTTP status mapping.
"""
from __future__ import annotations

import gc
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from tests.testbench.logger import python_logger
from tests.testbench.pipeline import (
    autosave,
    diagnostics_store,
    persistence,
    session_export,
)
from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
from tests.testbench.pipeline.reset_runner import reset_session
from tests.testbench.pipeline.snapshot_store import _dispose_all_sqlalchemy_caches
from tests.testbench.session_store import (
    SessionConflictError,
    SessionState,
    get_session_store,
)


def _verify_and_log_memory_hash(
    archive: persistence.SessionArchive,
    tarball_bytes: bytes,
    *,
    op: str,
    archive_ref: str,
) -> dict[str, Any]:
    """Run :func:`persistence.verify_memory_hash` and log the result.

    P21.1 G3/G10 wiring: diagnostic-only (never blocks the load). On a
    hash mismatch we emit both a ``python_logger().warning`` (so the
    JSONL session log captures it) and a
    :func:`diagnostics_store.record_internal` entry (so the Diagnostics →
    Errors subpage surfaces it with its ``integrity_check`` op tag). On
    the happy path we just log info for audit traceability. ``op`` is
    the logical operation name (``session.load`` / ``session.autosave_restore``);
    ``archive_ref`` is the user-visible identifier (archive name /
    autosave entry id) that the diagnostic detail should carry.

    Returns the raw verify dict for inclusion in the endpoint response
    so the UI can (optionally) show an integrity badge.
    """
    verify = persistence.verify_memory_hash(archive, tarball_bytes)
    if verify.get("legacy"):
        python_logger().info(
            "%s: archive %r has no memory_sha256 (legacy, pre-G3/G10); "
            "skipping integrity check",
            op, archive_ref,
        )
        return verify
    if verify.get("match"):
        python_logger().info(
            "%s: memory tarball hash OK for %r (%s)",
            op, archive_ref, verify.get("expected", "")[:12],
        )
        return verify
    # Mismatch path — log loud but do not fail the load. §3A F3 (report,
    # don't auto-wipe). User can see the warning banner + decide.
    msg = verify.get("message") or "memory tarball hash mismatch"
    python_logger().warning("%s: %s for %r", op, msg, archive_ref)
    try:
        diagnostics_store.record_internal(
            DiagnosticsOp.INTEGRITY_CHECK,
            f"{op}: {msg}",
            level="warning",
            detail={
                "op": op,
                "archive_ref": archive_ref,
                "expected": verify.get("expected"),
                "actual": verify.get("actual"),
            },
        )
    except Exception:  # noqa: BLE001 — diagnostics push must never re-raise
        python_logger().exception(
            "%s: diagnostics_store.record_internal failed", op,
        )
    return verify

router = APIRouter(prefix="/api/session", tags=["session"])


# ── request / response models ───────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Body for ``POST /api/session``. All fields optional."""

    name: str | None = Field(
        default=None,
        description="Human-friendly label; defaults to session-<id>.",
        max_length=200,
    )


class SessionStateResponse(BaseModel):
    """Compact state dict returned by ``GET /api/session/state``."""

    has_session: bool
    state: str
    busy_op: str | None = None
    session_id: str | None = None


# ── endpoints ───────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_session(body: CreateSessionRequest | None = None) -> dict[str, Any]:
    """Create (or replace) the single active session.

    Any previously active session is destroyed first — the underlying
    ``ConfigManager`` is a singleton so concurrent sessions are not
    possible. See ``PLAN.md §本期主动不做`` for rationale.
    """
    store = get_session_store()
    try:
        session = await store.create(name=(body.name if body else None))
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
    return session.describe()


@router.get("")
async def get_session() -> dict[str, Any]:
    """Return the current session description, or a ``has_session=False``
    marker when nothing is active (never 404 — the UI polls this on load).
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        return {"has_session": False}
    return {"has_session": True, **session.describe()}


@router.delete("", status_code=200)
async def delete_session(purge_sandbox: bool = True) -> dict[str, Any]:
    """Destroy the active session and (by default) its sandbox directory."""
    store = get_session_store()
    await store.destroy(purge_sandbox=purge_sandbox)
    return {"ok": True}


class ResetSessionRequest(BaseModel):
    """Body for ``POST /api/session/reset``.

    ``confirm`` must be ``True`` — the UI always sends the second-
    confirmation state explicitly, so the API surface rejects any
    accidental POST without it. This is an intentional 400 vs 200
    split rather than a quiet no-op.
    """

    level: Literal["soft", "medium", "hard"] = Field(
        ...,
        description=(
            "Reset tier. soft = clear messages + eval_results only; "
            "medium = soft + wipe memory files; "
            "hard = wipe sandbox + reset all session state (keeps "
            "model_config and backup snapshots)."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "UI-side second confirmation. Must be true; rejecting "
            "`confirm=false` prevents accidental reset via curl."
        ),
    )


@router.post("/reset")
async def reset_current_session(body: ResetSessionRequest) -> dict[str, Any]:
    """Perform a three-tier reset on the active session.

    Flow:
      1. Acquire ``session_operation("session.reset", state=RESETTING)``
         so any concurrent chat/send/script waits (and the UI disables
         risky buttons via polled ``/state``).
      2. Inside the lock, :func:`reset_runner.reset_session` captures a
         ``pre_reset_backup`` snapshot, then mutates state per level.
      3. Returns ``{ok: true, stats: {level, removed, preserved,
         pre_reset_backup_id}}`` so the UI can toast exactly what was
         wiped.

    Error codes:
      * 400 if ``confirm != true`` or ``level`` invalid (pydantic
        already covers the latter).
      * 404 if no session active.
      * 409 if another operation already holds the session lock.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "confirm must be true — reset is destructive; "
                "UI should gate behind an explicit second confirmation."
            ),
        )

    store = get_session_store()
    if store.get() is None:
        raise HTTPException(status_code=404, detail="no active session")

    try:
        async with store.session_operation(
            "session.reset", state=SessionState.RESETTING,
        ) as session:
            stats = reset_session(session, body.level)
            return {"ok": True, "stats": stats, **session.describe()}
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


@router.get("/state", response_model=SessionStateResponse)
async def get_session_state() -> SessionStateResponse:
    """Tiny state blob intended for frequent UI polling / top-bar updates."""
    state = get_session_store().get_state()
    # Make sure ``state`` looks like a valid ``SessionState`` value even
    # when no session is active.
    state_str = state.get("state", SessionState.IDLE.value)
    return SessionStateResponse(
        has_session=state["has_session"],
        state=state_str,
        busy_op=state.get("busy_op"),
        session_id=state.get("session_id"),
    )


# ── P21: persistence (save / load / list / delete / import) ────────


class SaveSessionRequest(BaseModel):
    """Body for ``POST /api/session/save`` and ``/save_as``.

    ``redact_api_keys`` defaults to ``True`` so the safe default is a
    shareable archive. UI exposes an "I know what I'm doing" checkbox
    only on the Save dialog when users opt in to plaintext keys (they
    still see a second confirmation before the request goes out).
    """

    name: str = Field(
        ...,
        description=(
            "Archive name on disk. 1-64 chars, letters/digits/_/-/., "
            "must start with a letter or digit."
        ),
    )
    redact_api_keys: bool = Field(
        default=True,
        description=(
            "Replace every ``api_key`` in ``model_config`` with "
            "``<redacted>``. Default true — override only for self-use."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "When false, refuse to overwrite an existing archive with "
            "the same name (409 ArchiveExists). ``/save`` (vs ``/save_as``) "
            "defaults to true via a separate endpoint alias below."
        ),
    )


class ImportSessionRequest(BaseModel):
    """Body for ``POST /api/session/import``.

    Accepts the :func:`persistence.export_to_payload` output — a dict
    containing both the archive JSON and the base64-encoded tarball.
    """

    payload: dict[str, Any] = Field(
        ...,
        description="Full export payload (archive + tarball_b64).",
    )
    name: str | None = Field(
        default=None,
        description=(
            "Optional archive name override. When omitted, the archive's "
            "own ``name`` field is used."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description="Allow replacing an existing saved archive.",
    )


def _persistence_error_to_http(exc: persistence.PersistenceError) -> HTTPException:
    """Map a :class:`persistence.PersistenceError` code to an HTTP status.

    The mapping mirrors the codes documented on the exception class so
    router behavior stays a single source of truth with the library.
    """
    code_to_status = {
        "InvalidName": 400,
        "InvalidArchive": 400,
        "SchemaVersionTooNew": 400,
        "TarballMissing": 400,
        "ArchiveNotFound": 404,
        "ArchiveExists": 409,
        "NameTaken": 409,
        "WriteFailed": 500,
    }
    status = code_to_status.get(exc.code, 500)
    return HTTPException(
        status_code=status,
        detail={
            "error_type": exc.code,
            "message": str(exc),
            **exc.detail,
        },
    )


@router.get("/saved")
async def list_saved_sessions() -> dict[str, Any]:
    """List every archive under ``DATA_DIR/saved_sessions/``.

    Safe to call without an active session (this is a disk-only query).
    Corrupt files come back with ``error`` set so the UI can show them
    greyed-out rather than disappearing silently.
    """
    items = [m.to_dict() for m in persistence.list_saved()]
    return {"items": items, "count": len(items)}


@router.get("/archives/{name}/lint")
async def lint_saved_archive(name: str) -> dict[str, Any]:
    """H2 archive JSON lint — field-level validation of a saved archive.

    Scope: JSON schema only (not the tarball; use ``memory_hash_verify``
    via ``POST /session/load/{name}`` for tarball integrity). Intended
    to help a user diagnose "my archive won't load — what's wrong with
    it?" by showing missing / malformed fields rather than a cryptic
    InvalidArchive 400. Returns a structured ``{errors, warnings, ...}``
    report — see :func:`persistence.lint_archive_json` for details.

    See P24_BLUEPRINT §3.2.
    """
    try:
        return persistence.lint_archive_json(name)
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc


@router.post("/save")
async def save_current_session(body: SaveSessionRequest) -> dict[str, Any]:
    """Save (overwrite) the active session to ``<name>.json`` + tarball.

    Use ``/save_as`` (below) when you want to refuse overwriting.
    """
    body_dict = body.model_dump()
    body_dict["overwrite"] = True
    return await _do_save(SaveSessionRequest(**body_dict))


@router.post("/save_as")
async def save_current_session_as(body: SaveSessionRequest) -> dict[str, Any]:
    """Save the active session as a NEW archive; 409 if the name exists."""
    body_dict = body.model_dump()
    body_dict["overwrite"] = False
    return await _do_save(SaveSessionRequest(**body_dict))


async def _do_save(body: SaveSessionRequest) -> dict[str, Any]:
    """Common save flow shared by ``/save`` and ``/save_as``."""
    store = get_session_store()
    if store.get() is None:
        raise HTTPException(status_code=404, detail="no active session to save")

    # ``validate_name`` is also inside persistence.save_session but we
    # want to fail fast with a 400 before grabbing the session lock.
    try:
        persistence.validate_name(body.name)
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc

    try:
        async with store.session_operation(
            "session.save", state=SessionState.SAVING,
        ) as session:
            try:
                stats = persistence.save_session(
                    session,
                    name=body.name,
                    redact_api_keys=body.redact_api_keys,
                    overwrite=body.overwrite,
                )
            except persistence.PersistenceError as exc:
                raise _persistence_error_to_http(exc) from exc
            session.logger.log_sync(
                "session.save",
                payload={
                    "name": body.name,
                    "redact": body.redact_api_keys,
                    "overwrite": body.overwrite,
                    "json_bytes": stats["json_bytes"],
                    "tar_bytes": stats["tar_bytes"],
                },
            )
            return {"ok": True, "stats": stats, **session.describe()}
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


@router.post("/load/{name}")
async def load_saved_session(name: str) -> dict[str, Any]:
    """Load an archive by name, replacing the active session entirely.

    Flow (see ``persistence`` module docstring for rationale):
      1. Validate name + parse archive JSON first (fail fast on 400/404
         **before** we touch the current session).
      2. Grab ``session_operation("session.load", LOADING)`` on the old
         session so concurrent chat/send requests see 409.
      3. Inside the lock: write a JSON-only ``pre_load_backup`` under
         ``saved_sessions/_autosave/`` so the user can recover if they
         realise they picked the wrong archive.
      4. Exit the session_operation context (releases the old session's
         lock so ``destroy`` can proceed).
      5. ``_dispose_all_sqlalchemy_caches() + gc.collect()`` — drops
         SQLite handles that would block the upcoming ``rmtree`` on
         Windows (P20 hotfix 2 pattern).
      6. ``store.create(name=archive.session_name)`` — this internally
         destroys the old session and creates a fresh sandbox.
      7. Extract tarball into the new sandbox ``_app_docs``.
      8. Apply archive to the new session (messages / persona /
         model_config / stage / clock / snapshot timeline).
      9. Return new session describe — UI follows with
         ``window.location.reload()`` per §3A B13.

    Error codes:
      * 400 — InvalidName / InvalidArchive / SchemaVersionTooNew / TarballMissing
      * 404 — ArchiveNotFound
      * 409 — SessionConflict (another op still running)
      * 500 — WriteFailed or unexpected
    """
    try:
        persistence.validate_name(name)
        archive = persistence.load_archive(name)
        tarball_bytes = persistence.read_tarball_bytes(name)
    except persistence.PersistenceError as exc:
        # P21.1 G2 (persistence 可靠性加固 pass): previously we silently
        # let ``TarballMissing`` through with ``tarball_bytes = b""`` and
        # loaded the session with empty memory. That's sneakily bad
        # because the companion tarball could disappear in the narrow
        # window between ``list_saved`` rendering a healthy row and the
        # user clicking Load — user sees "load success" but their
        # memory is silently gone. Now we treat the missing tarball as
        # a hard failure (400) so the UI can surface a broken-archive
        # banner and the user can decide whether to try the ``.prev``
        # backup or accept an empty-memory load via an opt-in flag
        # (future work — see PLAN §11).
        raise _persistence_error_to_http(exc) from exc

    # P21.1 G3/G10: verify the on-disk tarball hash against the value
    # archived at save time. Never blocks the load — mismatch is logged
    # + pushed to Diagnostics so the user can investigate (bit-rot /
    # manual tar swap / truncated write).
    hash_verify = _verify_and_log_memory_hash(
        archive, tarball_bytes, op="session.load", archive_ref=name,
    )

    store = get_session_store()

    # Step 2 + 3: take a pre-load backup of the current session while
    # holding its lock. If no session is active we skip this and jump
    # straight to the create step (still a valid load flow — e.g. first
    # launch of the day).
    pre_load_backup_path = None
    if store.get() is not None:
        try:
            async with store.session_operation(
                "session.load", state=SessionState.LOADING,
            ) as current:
                pre_load_backup_path = persistence.write_pre_load_backup(current)
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

    # Step 5: dispose SQLAlchemy caches so the impending rmtree inside
    # ``store.destroy`` (called by ``store.create`` below) doesn't hit
    # WinError 32 on time_indexed.db.
    _dispose_all_sqlalchemy_caches()
    gc.collect()

    # Step 6: create the new session. The SessionStore internally holds
    # its registry lock + destroys the old session first (sandbox and
    # all). We use the **archive filename** (``name``) as the new
    # session label rather than ``archive.session_name`` (which is the
    # auto-generated "session-<hash>" at save time): the archive name
    # is the user's deliberate handle for "this saved experiment", and
    # the topbar chip should reflect it post-load so the user can
    # immediately recognise which archive they loaded. We preserve the
    # original session_name in the archive JSON for diagnostics, but
    # the live session label tracks the archive filename.
    #
    # ``session_id`` (P24 2026-04-21 fix, same as autosave restore path
    # below): reuse the archive's original session_id so autosave
    # rolling slots stay continuous post-load instead of leaking a
    # fresh id's worth of slots. See autosave restore endpoint for
    # full rationale.
    new_session = await store.create(
        name=name,
        session_id=archive.session_id or None,
    )

    # Step 7: extract tarball into the freshly created sandbox. We
    # run this under the new session's per-op lock so no concurrent
    # chat request can fire while files are being laid down.
    try:
        async with store.session_operation(
            "session.load_apply", state=SessionState.LOADING,
        ) as session:
            restore_stats = persistence.restore_memory_tarball(
                session.sandbox._app_docs,  # noqa: SLF001
                tarball_bytes,
            )
            # Step 8: wire archive data into the new session object.
            apply_stats = persistence.apply_to_session(session, archive)
            session.logger.log_sync(
                "session.load",
                payload={
                    "name": name,
                    "pre_load_backup": str(pre_load_backup_path) if pre_load_backup_path else None,
                    "restore_stats": restore_stats,
                    "apply_stats": apply_stats,
                    "schema_version": archive.schema_version,
                    "memory_hash_verify": hash_verify,
                },
            )
            return {
                "ok": True,
                "name": name,
                "restore_stats": restore_stats,
                "apply_stats": apply_stats,
                "pre_load_backup": str(pre_load_backup_path) if pre_load_backup_path else None,
                "memory_hash_verify": hash_verify,
                **session.describe(),
            }
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc
    except SessionConflictError as exc:
        # Extremely unlikely — the new session was just created. But
        # treat it as a 409 just in case some other coroutine raced us.
        raise HTTPException(
            status_code=409,
            detail={
                "error_type": "SessionConflict",
                "message": str(exc),
                "state": exc.state.value,
                "busy_op": exc.busy_op,
            },
        ) from exc


@router.delete("/saved/{name}")
async def delete_saved_session(name: str) -> dict[str, Any]:
    """Delete ``<name>.json`` + ``<name>.memory.tar.gz``.

    Does **not** touch the active session — deleting from disk is
    disjoint from which session is loaded. Useful for pruning after
    a successful Load.
    """
    try:
        stats = persistence.delete_saved(name)
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc
    return {"ok": True, **stats}


@router.post("/import")
async def import_saved_session(body: ImportSessionRequest) -> dict[str, Any]:
    """Write an inline export payload to ``saved_sessions/``.

    Does not load it — the UI flow is "Import then optionally Load".
    Keeping these separate lets power users inspect the imported
    archive before committing to a cross-session switch.
    """
    try:
        json_path = persistence.import_from_payload(
            body.payload, name=body.name, overwrite=body.overwrite,
        )
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc
    return {
        "ok": True,
        "name": json_path.stem,
        "json_path": str(json_path),
    }


# ── P22: autosave (config / list / flush / restore / delete) ──────


class AutosaveConfigUpdate(BaseModel):
    """Body for ``POST /api/session/autosave/config``.

    Every field is optional — unspecified fields keep their current
    values.  Router merges the partial update on top of
    :func:`autosave.get_autosave_config` before calling
    :func:`autosave.set_autosave_config`.
    """

    enabled: bool | None = Field(
        default=None,
        description="Global on/off; ``False`` pauses all scheduler flushes.",
    )
    debounce_seconds: float | None = Field(
        default=None, ge=0.5, le=300.0,
        description="Quiet period after last edit before a flush fires.",
    )
    force_seconds: float | None = Field(
        default=None, ge=0.5, le=3600.0,
        description="Upper bound on total dirty time before forced flush.",
    )
    rolling_count: int | None = Field(
        default=None, ge=1, le=3,
        description="How many slots to retain per session (1..3).",
    )
    keep_window_hours: float | None = Field(
        default=None, ge=1.0, le=720.0,
        description="Cleanup retention window. 1 hour to 30 days.",
    )


@router.get("/autosave/config")
async def get_autosave_config_endpoint() -> dict[str, Any]:
    """Return the current autosave configuration."""
    return {"config": autosave.get_autosave_config().to_dict()}


@router.post("/autosave/config")
async def update_autosave_config_endpoint(
    body: AutosaveConfigUpdate,
) -> dict[str, Any]:
    """Merge a partial update into the autosave config.

    Validates bounds (:func:`autosave.validate_autosave_config`) before
    applying; returns 422 on out-of-range values. Changes take effect
    within one debounce cycle without restarting scheduler tasks.
    """
    cur = autosave.get_autosave_config()
    updated = autosave.AutosaveConfig(
        enabled=body.enabled if body.enabled is not None else cur.enabled,
        debounce_seconds=(
            body.debounce_seconds if body.debounce_seconds is not None
            else cur.debounce_seconds
        ),
        force_seconds=(
            body.force_seconds if body.force_seconds is not None
            else cur.force_seconds
        ),
        rolling_count=(
            body.rolling_count if body.rolling_count is not None
            else cur.rolling_count
        ),
        keep_window_hours=(
            body.keep_window_hours if body.keep_window_hours is not None
            else cur.keep_window_hours
        ),
    )
    try:
        autosave.set_autosave_config(updated)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_type": "InvalidAutosaveConfig", "message": str(exc)},
        ) from exc
    return {"ok": True, "config": updated.to_dict()}


@router.get("/autosaves")
async def list_autosave_entries(
    within_hours: float | None = None,
) -> dict[str, Any]:
    """Return every autosave slot under ``AUTOSAVE_DIR``.

    Query param ``within_hours`` filters out entries older than the
    cutoff; defaults to no filter (return all). The topbar banner hits
    this with ``within_hours=<keep_window_hours>`` on boot to decide
    whether to prompt for restore.
    """
    items = autosave.list_autosaves(within_hours=within_hours)
    return {"items": items, "count": len(items)}


@router.get("/autosaves/boot_orphans")
async def list_boot_orphans() -> dict[str, Any]:
    """Slot-0 autosaves from **other** sessions within the recovery window.

    This is what the topbar "Unsaved work detected" banner consumes on
    mount. When the list is non-empty, the UI surfaces a banner
    inviting the user into the restore modal. When empty, the banner
    stays hidden.
    """
    store = get_session_store()
    active = store.get()
    cfg = autosave.get_autosave_config()
    items = autosave.list_autosaves_boot_orphans(
        active_session_id=active.id if active else None,
        within_hours=cfg.keep_window_hours,
    )
    return {"items": items, "count": len(items)}


@router.post("/autosave/flush")
async def flush_autosave_now() -> dict[str, Any]:
    """Force an immediate flush of the active session.

    Returns 404 when no session is active. Otherwise delegates to
    :meth:`AutosaveScheduler.flush_now`. Non-fatal failures come back
    as ``{wrote: false, reason: "..."}`` (200) because "flush failed
    mid-I/O" is an operational signal rather than a hard error — the
    user can retry or file a bug. Hard errors (session disappeared
    between check and flush, scheduler crashed) still surface via the
    500 handler.
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        raise HTTPException(status_code=404, detail="no active session")
    if session.autosave_scheduler is None:
        # Should never happen — create() attaches the scheduler. But if
        # it did, tell the user rather than 500 on attribute access.
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "AutosaveSchedulerMissing",
                "message": "session has no autosave scheduler attached",
            },
        )
    result = await session.autosave_scheduler.flush_now()
    return {"ok": True, **result}


@router.get("/autosave/status")
async def get_autosave_status() -> dict[str, Any]:
    """Diagnostic snapshot of the active session's scheduler.

    Returns 404 when no session is active. Used by the diagnostics
    subpage + settings "Autosave health" widget. The payload shape
    comes directly from :meth:`AutosaveScheduler.get_status`.
    """
    store = get_session_store()
    session = store.get()
    if session is None:
        raise HTTPException(status_code=404, detail="no active session")
    if session.autosave_scheduler is None:
        raise HTTPException(
            status_code=500,
            detail={
                "error_type": "AutosaveSchedulerMissing",
                "message": "session has no autosave scheduler attached",
            },
        )
    return {"ok": True, **session.autosave_scheduler.get_status()}


@router.post("/autosaves/{entry_id}/restore")
async def restore_autosave_entry(entry_id: str) -> dict[str, Any]:
    """Restore a single autosave slot as the active session.

    Flow mirrors ``/load/{name}`` but reads from the autosave slot:

    1. Parse entry_id + load archive + tarball (fail fast on 400/404).
    2. Take pre_load_backup of the current session (if any).
    3. Dispose SQLAlchemy caches + gc.
    4. ``store.create`` — destroys old session (its scheduler
       ``close()`` runs here and does a last-gasp flush if dirty).
    5. Restore tarball into new sandbox.
    6. Apply archive to the new session.
    7. Return describe + stats.

    URL-encode the entry_id on the client side — it contains a ``:``
    separator (``<session_id>:<slot>``).
    """
    try:
        archive, tarball_bytes = autosave.read_autosave_archive_and_tarball(entry_id)
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc

    # P21.1 G3/G10: verify autosave tarball hash (same semantics as
    # the named-archive load path — diagnostic only, never blocks).
    hash_verify = _verify_and_log_memory_hash(
        archive, tarball_bytes,
        op="session.autosave_restore", archive_ref=entry_id,
    )

    store = get_session_store()

    pre_load_backup_path = None
    if store.get() is not None:
        try:
            async with store.session_operation(
                "session.autosave_restore", state=SessionState.LOADING,
                # Don't notify autosave — we're about to destroy this
                # session anyway, no point kicking off a scheduler flush
                # that close() will immediately reduplicate.
                autosave_notify=False,
            ) as current:
                pre_load_backup_path = persistence.write_pre_load_backup(current)
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

    _dispose_all_sqlalchemy_caches()
    gc.collect()

    # ``name`` on restore: use the archive's session name if set,
    # otherwise a derived label. ``session_id`` (P24 2026-04-21 fix):
    # reuse the archive's original session_id rather than generating a
    # new one. Previously each restore spawned a new id, which caused
    # the autosave scheduler to write a *fresh* rolling 3-slot set
    # under the new id while the old id's 3 slots lingered on disk;
    # the user saw "6 autosaves when I set rolling_count=3". Reusing
    # the original id means the restored session's subsequent edits
    # overwrite the *same* rolling slots that gave rise to this
    # restore — continuous identity, no duplicate footprint.
    restore_label = (
        archive.session_name
        or archive.name
        or f"autosave-{entry_id.split(':', 1)[0]}"
    )
    await store.create(name=restore_label, session_id=archive.session_id or None)

    try:
        async with store.session_operation(
            "session.autosave_restore_apply", state=SessionState.LOADING,
            autosave_notify=False,
        ) as session:
            restore_stats = persistence.restore_memory_tarball(
                session.sandbox._app_docs,  # noqa: SLF001
                tarball_bytes,
            )
            apply_stats = persistence.apply_to_session(session, archive)
            session.logger.log_sync(
                "session.autosave_restore",
                payload={
                    "entry_id": entry_id,
                    "pre_load_backup": (
                        str(pre_load_backup_path) if pre_load_backup_path else None
                    ),
                    "restore_stats": restore_stats,
                    "apply_stats": apply_stats,
                    "schema_version": archive.schema_version,
                    "memory_hash_verify": hash_verify,
                },
            )
            return {
                "ok": True,
                "entry_id": entry_id,
                "restore_stats": restore_stats,
                "apply_stats": apply_stats,
                "pre_load_backup": (
                    str(pre_load_backup_path) if pre_load_backup_path else None
                ),
                "memory_hash_verify": hash_verify,
                **session.describe(),
            }
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc
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


@router.delete("/autosaves/{entry_id}")
async def delete_autosave_entry_endpoint(entry_id: str) -> dict[str, Any]:
    """Delete a single autosave slot's JSON + tarball pair."""
    try:
        stats = autosave.delete_autosave_entry(entry_id)
    except persistence.PersistenceError as exc:
        raise _persistence_error_to_http(exc) from exc
    return {"ok": True, **stats}


@router.delete("/autosaves")
async def delete_all_autosaves_endpoint() -> dict[str, Any]:
    """Nuke every autosave slot. Does NOT touch ``pre_load_*.json``.

    Intended for the Settings "Clear all autosaves" button. UI should
    present a second confirmation before calling this because it's
    destructive.
    """
    stats = autosave.delete_all_autosaves()
    return {"ok": True, **stats}


# ── P23: multi-format / multi-scope export ─────────────────────────


class ExportSessionRequest(BaseModel):
    """Body for ``POST /api/session/export``.

    Combinations are validated server-side via
    :func:`session_export.ensure_valid_combination`; invalid combos come
    back as ``400 InvalidCombination`` rather than being silently shaped.

    ``include_memory`` is only meaningful for ``scope=full`` or
    ``persona_memory`` + ``format=json`` (the export envelope has room
    for a base64 tarball only in those shapes). For other combinations
    the flag is silently ignored so the UI checkbox stays simple.

    API keys are **always redacted** in exports — there's no knob for
    "include plaintext keys" here (see module docstring §"API Key
    脱敏" decision note). Save/Load has that knob; Export deliberately
    doesn't because exports are shared artefacts by definition.
    """

    scope: str = Field(
        ...,
        description=(
            "One of: full / persona_memory / conversation / "
            "conversation_evaluations / evaluations."
        ),
    )
    format: str = Field(
        ...,
        description="One of: json / markdown / dialog_template.",
    )
    include_memory: bool = Field(
        default=False,
        description=(
            "When true + scope in (full, persona_memory) + format=json, "
            "base64-encode the sandbox memory tarball into the payload "
            "envelope. Ignored silently for other combinations."
        ),
    )


def _session_export_error_to_http(
    exc: session_export.SessionExportError,
) -> HTTPException:
    """Map :class:`SessionExportError` codes to HTTP status.

    All export schema errors are client-facing 400s (the user picked an
    invalid combination or we couldn't infer a scope from their request).
    Internal failures (disk read, sandbox missing) should surface as
    a 500 with ``error_type`` set by the underlying exception.
    """
    return HTTPException(
        status_code=400,
        detail={
            "error_type": exc.code,
            "message": exc.message,
        },
    )


@router.post("/export")
async def export_current_session(body: ExportSessionRequest) -> Response:
    """Produce a downloadable export of the active session.

    Returns the raw body with ``Content-Disposition: attachment`` so the
    browser treats it as a file download (same pattern as
    :func:`judge_router.export_report`).

    Flow:

    1. Require an active session (404 otherwise).
    2. Grab ``session_operation("session.export")`` to serialise against
       concurrent mutation (autosave / chat writes / etc). Export is a
       **read**, but we still want a consistent view of messages +
       eval_results + snapshot meta taken together.
    3. Inside the lock, run :func:`session_export.export_session` which
       dispatches to the right scope/format builder. ``include_memory``
       packs the sandbox tarball from inside the lock too — pack is
       read-only but blocking on IO, and we'd rather hold the lock a
       few hundred ms than risk interleaving with an autosave write.
    4. Build the filename + Content-Disposition header and return.
    """
    store = get_session_store()
    if store.get() is None:
        raise HTTPException(
            status_code=404, detail="no active session to export",
        )

    try:
        session_export.ensure_valid_combination(body.scope, body.format)
    except session_export.SessionExportError as exc:
        raise _session_export_error_to_http(exc) from exc

    try:
        # Export is a short-lived read-only op; the BUSY umbrella state
        # is the right fit (SAVING would imply disk-writing, LOADING is
        # for "replaces session entirely"). The lock still serialises
        # against concurrent chat writes so the exported snapshot of
        # messages / eval_results is internally consistent.
        async with store.session_operation(
            "session.export", state=SessionState.BUSY,
        ) as session:
            try:
                body_text, media_type = session_export.export_session(
                    session,
                    scope=body.scope,
                    fmt=body.format,
                    include_memory=body.include_memory,
                )
            except session_export.SessionExportError as exc:
                raise _session_export_error_to_http(exc) from exc

            filename = session_export.session_export_filename(
                session_name=getattr(session, "name", "") or "session",
                scope=body.scope,
                fmt=body.format,
            )
            session.logger.log_sync(
                "session.export",
                payload={
                    "scope": body.scope,
                    "format": body.format,
                    "include_memory": body.include_memory,
                    "filename": filename,
                    "body_bytes": len(body_text.encode("utf-8")),
                },
            )
            return Response(
                content=body_text,
                media_type=media_type,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{filename}"'
                    ),
                },
            )
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
