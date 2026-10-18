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

"""Visit data endpoints backed by Servers: history, details, reports (design §4.6 / §4.7).

* ``GET /history?cursor=`` -- proxies ``GET {social_base}/api/visit/history``;
  ``peer_display_name`` is self-reported by the peer, so it goes through the
  OD-23 display-name cleaning before it reaches the page.
* ``GET /details/{visit_id}?cursor=`` -- proxies the details of one
  visit ("view details", OD-26 v3), one page per call, ``cursor`` passed
  through and ``next_cursor`` returned as is.
* ``POST /report`` -- writes the queued report file first, then submits it
  (after this side's transcript upload when ``include_transcript``); a
  network error / 429 / 5xx answers 202 ``{queued:true}`` and keeps retrying.
* ``GET /report/queue`` and ``POST /report/queue/{visit_id}`` -- the queued
  reports and the "retry / give up" buttons shown once one is a week old.

The bearer never leaves the backend, nothing is cached. All of them are data
management: not behind the ``NEKO_VISIT_ENABLED`` release switch. Every one
passes the local-origin gate (loopback peer + Origin / Host + CSRF).
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import (
    VISIT_DETAILS_MAX_PAGES,
    VISIT_LP_MAX,
    VISIT_REPORT_NOTE_MAX_CHARS,
    VISIT_TEXT_MAX_BYTES,
    VISIT_UPLOAD_RETRY_BACKOFF_S,
)
from main_logic.visit import memory_bridge
from main_logic.visit.memory_commit import SIDE_RANK
from main_logic.visit.sanitize import neutralize_display_name
from main_routers.system_router._shared import _read_json_object
from main_routers.visit_router import accounts
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import transcript_upload as tu
from main_routers.visit_router.local_context import (
    load_character_context,
    prompt_lang,
    protected_display_names,
    speaker_label,
)
from main_routers.visit_router.local_guard import http_denied
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE

logger = get_module_logger(__name__, "Main")

router = APIRouter()

_CLOUD_TIMEOUT_S = 10.0
_CURSOR_RE = re.compile(r"^[\x21-\x7e]{1,512}$")
_SHORT_CODE_RE = re.compile(r"^[0-9A-F]{6}$")
DETAILS_PAGE_LIMIT = 500
"""Rows per page the transcript fallback asks for (Servers caps ``limit`` at 500)."""


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, **extra}, status_code=status)


def _cloud_error(err: cr.VisitServersError) -> JSONResponse:
    status, body = err.to_local_error()
    return JSONResponse({"ok": False, **body}, status_code=status)


class CloudError(Exception):
    """A Servers read failed; ``response`` is the local reply to give."""

    def __init__(self, response: JSONResponse, *, status: int | None = None) -> None:
        super().__init__(status)
        self.response = response
        self.status = status


_READ_ERRORS: Mapping[tuple[int, str], tuple[int, str]] = {
    (403, "not_participant"): (403, "not_participant"),
    (404, "unknown_visit"): (404, "unknown_visit"),
}


async def cloud_get(path: str, *, op: str, params: Mapping[str, str] | None = None) -> Any:
    """``GET {social_base}{path}`` with the signed-in bearer; the JSON body, or :class:`CloudError`.

    401 -> 409 ``VISIT_LOGIN_REQUIRED``; ``403 not_participant`` / ``404
    unknown_visit`` pass through; network, 5xx and anything uncontracted ->
    503 ``servers_unreachable``.
    """
    try:
        session = await cr._servers_session()
        resp = await cr._send("GET", f"{session.base_url}{path}", op=op, headers=session.headers(),
                              params=params, timeout=_CLOUD_TIMEOUT_S)
    except cr.VisitServersError as exc:
        raise CloudError(_cloud_error(exc)) from None
    status = resp.status_code
    body = cr._body_json(resp)
    if 200 <= status < 300:
        if not isinstance(body, dict):
            raise CloudError(_error(503, "servers_unreachable"), status=status)
        return body
    if status == 401:
        raise CloudError(_cloud_error(cr.VisitLoginRequired()), status=status)
    mapped = _READ_ERRORS.get((status, cr._body_code(body) or ""))
    if mapped is not None:
        raise CloudError(_error(mapped[0], mapped[1]), status=status)
    if status < 500:
        logger.warning("visit servers %s: uncontracted reply status=%s code=%s", op, status,
                       cr._diag_code(cr._body_code(body)))
    raise CloudError(_error(503, "servers_unreachable"), status=status)


def _cursor_ok(cursor: str) -> bool:
    return cursor == "" or _CURSOR_RE.fullmatch(cursor) is not None


async def fetch_details_page(visit_id: str, *, cursor: str = "", limit: int | None = None) -> dict:
    """One page of ``GET /api/visit/details/{visit_id}`` (validated ``visit_id``); :class:`CloudError` on failure."""
    params: dict[str, str] = {}
    if cursor:
        params["cursor"] = cursor
    if limit is not None:
        params["limit"] = str(int(limit))
    body = await cloud_get(f"/api/visit/details/{visit_id}", op="details", params=params)
    if body.get("visit_id") != visit_id:
        # Servers returned another session: treat as a malformed response and do not forward to the frontend
        logger.warning("visit servers details: reply names another visit")
        raise CloudError(_error(503, "servers_unreachable"))
    return body


class CloudTranscriptIncomplete(Exception):
    """Servers still had a ``next_cursor`` after ``VISIT_DETAILS_MAX_PAGES`` pages."""


_MALFORMED: dict = {}
"""Marker of a details row whose half for this side does not have the contracted shape."""


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # Integers beyond float range make isfinite raise; one invalid row must not turn export into 500
        return False


def _utf8_str(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # Escaped lone surrogates in JSON parse as strings but cannot encode as UTF-8, producing 500 when returned to the frontend
        return False
    return True


def _cloud_line(row: Any, role: str) -> dict | None:
    """This side's half of one aligned details row in the cloud transcript shape.

    None when the row has no half for this side, :data:`_MALFORMED` when it is malformed.
    """
    if not isinstance(row, dict):
        return _MALFORMED
    mine = row.get(role)
    lp = row.get("lp")
    if mine is None:
        return None
    if not isinstance(mine, dict):
        # A present local-side field that is not an object is an invalid row, not an absent side to omit silently
        return _MALFORMED
    ts = mine.get("ts")
    if (
        not isinstance(lp, int) or isinstance(lp, bool) or not 0 <= lp <= VISIT_LP_MAX
        or row.get("side") not in ("host", "guest")
        or mine.get("from") not in tu.SPEAKERS
        or not _finite_number(ts)
        or not _utf8_str(mine.get("text"))
        or len(mine["text"].encode("utf-8")) > VISIT_TEXT_MAX_BYTES
        or not isinstance(mine.get("truncated"), bool)
    ):
        return _MALFORMED
    return {"lp": lp, "side": row["side"], "from": mine["from"], "ts": ts, "text": mine["text"],
            "truncated": mine["truncated"]}


async def fetch_cloud_transcript(visit_id: str) -> dict:
    """This side's transcript of ``visit_id`` from Servers details, every page (``GET /transcript`` fallback).

    Pages are requested with ``limit`` = :data:`DETAILS_PAGE_LIMIT` and the
    returned ``cursor`` until there is no ``next_cursor``; this side is the
    reply's ``requester_role``. Returns ``{source:'cloud', visit_id, role,
    lines}`` sorted by ``(lp, side_rank)``. Raises :class:`CloudError` when
    any page fails (no partial lines) and :class:`CloudTranscriptIncomplete`
    past ``VISIT_DETAILS_MAX_PAGES`` pages.
    """
    role: str | None = None
    lines: list[dict] = []
    rejected = 0
    cursor = ""
    seen_cursors = {cursor}
    for _page in range(VISIT_DETAILS_MAX_PAGES):
        body = await fetch_details_page(visit_id, cursor=cursor, limit=DETAILS_PAGE_LIMIT)
        page_role = body.get("requester_role")
        rows = body.get("lines")
        if (page_role not in ("host", "guest") or (role is not None and page_role != role)
                or not isinstance(rows, list)):
            logger.warning("visit servers details: malformed transcript page")
            raise CloudError(_error(503, "servers_unreachable"))
        role = page_role
        next_cursor = body.get("next_cursor")
        if len(rows) > DETAILS_PAGE_LIMIT or (next_cursor not in (None, "") and len(rows) != DETAILS_PAGE_LIMIT):
            # Contract (§4.7 details): every page except the last has exactly limit rows, and no page exceeds limit;
            # violating this invalidates the total derived from page limits (short pages waste pagination, oversized pages exceed memory bounds)
            logger.warning("visit servers details: transcript page size off contract")
            raise CloudError(_error(503, "servers_unreachable"))
        for row in rows:
            line = _cloud_line(row, role)
            if line is _MALFORMED:
                rejected += 1
            elif line is not None:
                lines.append(line)
        if next_cursor is None or next_cursor == "":
            if rejected:
                # Drop contract-invalid rows and emit one diagnostic per transcript, avoiding per-row spam
                memory_bridge.diag("cloud_transcript_rows_rejected", count=rejected)
            lines.sort(key=lambda line: (line["lp"], SIDE_RANK.get(line["side"], 2)))
            return {"source": "cloud", "visit_id": visit_id, "role": role, "lines": lines}
        if not isinstance(next_cursor, str) or not _CURSOR_RE.fullmatch(next_cursor) or next_cursor in seen_cursors:
            # Revisiting a requested cursor (unchanged or cyclic) duplicates pages; treat as malformed instead of exhausting the page limit
            logger.warning("visit servers details: malformed next_cursor")
            raise CloudError(_error(503, "servers_unreachable"))
        cursor = next_cursor
        seen_cursors.add(cursor)
    memory_bridge.diag("cloud_transcript_incomplete", pages=VISIT_DETAILS_MAX_PAGES)
    raise CloudTranscriptIncomplete(visit_id)


# ── History / details ────────────────────────────────────────────────────────


@router.get("/history")
async def visit_history(request: Request, cursor: str = ""):
    """Visits of the signed-in account still kept by Servers, newest first (one page)."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not _cursor_ok(cursor):
        return _error(400, "cursor_format")
    try:
        body = await cloud_get("/api/visit/history", op="history", params={"cursor": cursor} if cursor else None)
    except CloudError as exc:
        return exc.response
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        return _error(503, "servers_unreachable")
    lang = prompt_lang()
    ctx = await load_character_context()
    protected = protected_display_names(lang, ctx.family_names, ctx.char_names)
    label = speaker_label("peer_cat", lang)
    items = []
    for item in raw_items:
        if not isinstance(item, dict) or not isinstance(item.get("visit_id"), str) \
                or not VISIT_ID_RE.fullmatch(item["visit_id"]) or item.get("role") not in ("host", "guest"):
            # Format gate: invalid visit_id values must not enter the frontend, where they later derive paths and requests
            memory_bridge.diag("history_item_rejected")
            continue
        short = item.get("peer_short_code")
        short = short if isinstance(short, str) and _SHORT_CODE_RE.fullmatch(short) else ""
        raw_name = item.get("peer_display_name")
        items.append({
            **item,
            "peer_short_code": short,
            "peer_display_name": neutralize_display_name(
                raw_name if isinstance(raw_name, str) else None,
                protected_names=protected, generic_label=label, short_code=short,
            ),
        })
    out: dict[str, Any] = {"items": items}
    next_cursor = body.get("next_cursor")
    if isinstance(next_cursor, str) and _CURSOR_RE.fullmatch(next_cursor):
        out["next_cursor"] = next_cursor
    return JSONResponse(out)


@router.get("/details/{visit_id}")
async def visit_details(request: Request, visit_id: str, cursor: str = ""):
    """One page of a visit's details (duration, usage, both transcripts aligned); passed through."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    if not VISIT_ID_RE.fullmatch(visit_id or ""):
        return _error(400, "visit_id_format")
    if not _cursor_ok(cursor):
        return _error(400, "cursor_format")
    try:
        return JSONResponse(await fetch_details_page(visit_id, cursor=cursor))
    except CloudError as exc:
        return exc.response


# ── Reports ───────────────────────────────────────────────────────────────


def _report_fields(payload: Mapping[str, Any]) -> dict | JSONResponse:
    visit_id = payload.get("visit_id")
    if not isinstance(visit_id, str) or not VISIT_ID_RE.fullmatch(visit_id):
        return _error(400, "visit_id_format")
    reason = payload.get("reason")
    if reason not in tu.REPORT_REASONS:
        return _error(400, "invalid_reason")
    note = payload.get("note", "")
    if note is None:
        note = ""
    if not isinstance(note, str) or len(note) > VISIT_REPORT_NOTE_MAX_CHARS:
        return _error(400, "invalid_note")
    try:
        # Lone surrogates (JSON "\ud800") parse but cannot write UTF-8 report files: reject immediately as invalid notes instead of 500
        note.encode("utf-8")
    except UnicodeEncodeError:
        return _error(400, "invalid_note")
    include = payload.get("include_transcript")
    if not isinstance(include, bool):
        return _error(400, "include_transcript_required")
    return {"visit_id": visit_id, "reason": reason, "note": note, "include_transcript": include}


@router.post("/report")
async def report_visit(request: Request):
    """Report the other side of a visit (queued locally first; Servers infers who is reported)."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    fields = _report_fields(payload)
    if isinstance(fields, JSONResponse):
        return fields
    visit_id = fields["visit_id"]
    account = await accounts.local_account()
    if not account:
        return _cloud_error(cr.VisitLoginRequired())
    config_dir = Path(tu.config_dir_provider())
    own_visit_uid = await accounts.lookup_visit_uid(account)
    doc = {
        **fields,
        "own_visit_uid": own_visit_uid,
        "own_account": account,
        # Exclude anomaly counts from another account's side on a shared computer
        "anomalies": await tu.visit_anomalies(config_dir, visit_id, own_visit_uid),
        "app_version": cr._app_version(),
        "queued_at": time.time(),
    }
    # Enqueue and this submission share a per-session lock; neither background rounds (which may submit newly queued reports) nor another window's discard
    # can intervene, so the returned result reflects this report's actual disposition
    async with tu.visit_lock(visit_id):
        try:
            await tu.queue_report(config_dir, doc)
        except tu.ReportAlreadyQueued:
            return _error(409, "already_queued")
        except (OSError, ValueError) as exc:
            logger.warning("visit report: cannot queue %s: %s", visit_id, type(exc).__name__)
            return _error(500, "report_persist_failed")
        return await _submit_new_report(config_dir, doc)


def _after_attempt(retry_after_s: int | None) -> float:
    """Background retry delay right after an attempt: the first backoff step, or Servers' ``retry_after``."""
    return float(max(VISIT_UPLOAD_RETRY_BACKOFF_S[0], retry_after_s or 0))


def _queued_login_required() -> JSONResponse:
    """``VISIT_LOGIN_REQUIRED`` for a report that is already queued (``queued: true``).

    Unlike a report refused before it was queued, re-submitting it only gets
    ``already_queued``; the page shows it as queued and asks to sign in.
    """
    status, body = cr.VisitLoginRequired().to_local_error()
    return JSONResponse({"ok": False, **body, "queued": True}, status_code=status)


async def _submit_new_report(config_dir: Path, doc: dict) -> JSONResponse:
    visit_id = doc["visit_id"]
    if doc["include_transcript"]:
        # Transcript-attached reports wait until this side's transcript reaches Servers: await archival for ongoing sessions, or synchronously attempt an archived upload once
        if tu.is_live(visit_id):
            return JSONResponse({"queued": True}, status_code=202)
        deferred = tu.upload_deferred_s(visit_id, doc.get("own_visit_uid"))
        if deferred > 0:
            # Servers just rate-limited this report's transcript side (Retry-After pending): do not retransmit synchronously; leave it to deferred
            # background retries. Rate limiting of another account's transcript does not block this report; continue ownership checks below
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=deferred)
            return JSONResponse({"queued": True}, status_code=202)
        try:
            upload = await tu.attempt_upload(visit_id, config_dir=config_dir)
        except (OSError, ValueError) as exc:
            # The report is persisted: upload-progress bookkeeping failures (full disk, etc.) must not become 500; leave them to background retries
            logger.warning("visit report %s: upload attempt failed: %s", visit_id, type(exc).__name__)
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
            return JSONResponse({"queued": True}, status_code=202)
        # Another account's transcript neither blocks this report nor supplies its reason; retryable failures still schedule background work
        upload = upload.for_report(doc)
        if not upload.pending and upload.retryable:
            # Already uploaded, but the archived file remains; background cleanup handles it later
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
        if upload.pending:
            if upload.retryable:
                tu.schedule_visit_retry(visit_id, config_dir=config_dir,
                                        initial_delay_s=_after_attempt(upload.retry_after_s))
            if upload.login_required:
                # Queued, but transcript upload cannot authenticate: request login and include queued so the page displays queued status
                return _queued_login_required()
            return JSONResponse({"queued": True}, status_code=202)
        # On terminal rejection/expiration, attempt_upload records the reason in the report file; reread to include it;
        # if disk/permissions prevented persistence, take the reason from upload.unavailable and include it in this submission anyway
        doc = await tu.load_report(config_dir, visit_id) or doc
        if upload.unavailable and not doc.get("transcript_unavailable"):
            # If the reason exists only in memory (transcript settled before the report or persistence failed), write it into the queued report for post-restart recovery submission
            await tu.mark_report_transcript_unavailable(config_dir, visit_id, upload.unavailable)
            doc = {**doc, "transcript_unavailable": upload.unavailable}
    result = await tu.send_report(doc)
    if await tu.finish_report(config_dir, visit_id, result, doc):
        return JSONResponse({"ok": True, "report_id": result.report_id})
    if result.unknown_visit:
        # Retain the rejected report file (delete only after acceptance or user discard) and offer Retry / Discard in the queue.
        # If the marker could not persist because of disk/permissions, schedule a background retry to record it next round
        if not await tu.rejection_recorded(config_dir, visit_id):
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
        return _error(404, "unknown_visit")
    tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(result.retry_after_s))
    if result.login_required:
        # Queued but login expired: request login; background/startup recovery resumes submission when the original account returns
        return _queued_login_required()
    return JSONResponse({"queued": True}, status_code=202)


@router.get("/report/queue")
async def list_report_queue(request: Request):
    """Reports still waiting for Servers; ``stale`` once a week old (UI offers retry / give up)."""
    denied = http_denied(request)
    if denied is not None:
        return denied
    account = await accounts.local_account()
    return JSONResponse({"items": await tu.list_queued_reports(Path(tu.config_dir_provider()), account)})


@router.post("/report/queue/{visit_id}")
async def act_on_queued_report(request: Request, visit_id: str):
    """``{action:'retry'}`` resubmits now (after the transcript upload); ``'abandon'`` deletes it."""
    payload = await _read_json_object(request)
    denied = http_denied(request, payload)
    if denied is not None:
        return denied
    if not VISIT_ID_RE.fullmatch(visit_id or ""):
        return _error(400, "visit_id_format")
    action = payload.get("action")
    if action not in ("retry", "abandon"):
        return _error(400, "invalid_action")
    config_dir = Path(tu.config_dir_provider())
    account = await accounts.local_account()

    async def _owned_report() -> dict | None:
        report = await tu.load_report(config_dir, visit_id)
        # Reports queued by other accounts on this computer are neither visible nor deletable
        return report if report is not None and await tu.report_belongs_to(report, account) else None

    async def _owned() -> bool:
        return await _owned_report() is not None

    if action == "abandon":
        # Share the background submitter's per-session lock and check ownership inside it: while awaiting the lock, the original report may have been submitted/deleted,
        # with another account queuing a new report for that session; delete only the current file belonging to the current account
        async with tu.visit_lock(visit_id):
            if not await _owned() or not await tu.delete_report(config_dir, visit_id):
                return _error(404, "not_queued")
        return JSONResponse({"ok": True, "removed": True})
    owned = await _owned_report()
    if owned is None:
        return _error(404, "not_queued")
    outcome = await tu.retry_visit_once(visit_id, config_dir=config_dir, manual=True, owner=account, expect=owned)
    # Unreadable because of contention does not mean delivered; treat as still processing
    current, unreadable = await tu._read_report(config_dir, visit_id)
    delivered = not unreadable and (current is None or not await tu.report_belongs_to(current, account))
    if not delivered and outcome.unknown_visit:
        # Rejected again: return 404 unknown_visit like POST /report; UI still offers Retry / Discard
        return _error(404, "unknown_visit")
    if not delivered and outcome.login_required:
        # Original account login expired: request login and retain the file for normal recovery submission afterward
        return _cloud_error(cr.VisitLoginRequired())
    if not delivered and outcome.pending:
        tu.schedule_visit_retry(visit_id, config_dir=config_dir,
                                initial_delay_s=_after_attempt(outcome.retry_after_s))
    return JSONResponse({"ok": True, "delivered": delivered})
