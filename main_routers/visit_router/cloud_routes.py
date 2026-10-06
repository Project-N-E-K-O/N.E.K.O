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
* ``GET /details/{visit_id}?catgirl=&cursor=`` -- proxies the details of one
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

import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.visit_settings import VISIT_REPORT_NOTE_MAX_CHARS
from main_logic.visit import memory_bridge
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
        # Servers 回了别的场次：按坏响应处理，不交给前端
        logger.warning("visit servers details: reply names another visit")
        raise CloudError(_error(503, "servers_unreachable"))
    return body


# ── 历史 / 详情 ────────────────────────────────────────────────────────


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
            # 格式闸：不合格的 visit_id 不进前端（之后还会被拿去派生路径、发请求）
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
async def visit_details(request: Request, visit_id: str, catgirl: str = "", cursor: str = ""):
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


# ── 举报 ───────────────────────────────────────────────────────────────


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
    doc = {
        **fields,
        "own_visit_uid": await accounts.lookup_visit_uid(account),
        "own_account": account,
        "anomalies": await tu.visit_anomalies(config_dir, visit_id),
        "app_version": cr._app_version(),
        "queued_at": time.time(),
    }
    try:
        await tu.queue_report(config_dir, doc)
    except tu.ReportAlreadyQueued:
        return _error(409, "already_queued")
    except (OSError, ValueError) as exc:
        logger.warning("visit report: cannot queue %s: %s", visit_id, type(exc).__name__)
        return _error(500, "report_persist_failed")
    async with tu.visit_lock(visit_id):
        return await _submit_new_report(config_dir, doc)


async def _submit_new_report(config_dir: Path, doc: dict) -> JSONResponse:
    visit_id = doc["visit_id"]
    if doc["include_transcript"]:
        # 附转录的举报等本侧转录先到 Servers：在飞场次等收尾封存，已封存的先同步试传一次
        if tu.is_live(visit_id):
            return JSONResponse({"queued": True}, status_code=202)
        upload = await tu.attempt_upload(visit_id, config_dir=config_dir)
        if upload.pending:
            if upload.retryable:
                tu.schedule_visit_retry(visit_id, config_dir=config_dir)
            return JSONResponse({"queued": True}, status_code=202)
        # 终态拒收 / 过期时 attempt_upload 已在举报文件里记下原因：重读一次带上
        doc = await tu.load_report(config_dir, visit_id) or doc
    result = await tu.send_report(doc)
    if await tu.finish_report(config_dir, visit_id, result):
        return JSONResponse({"ok": True, "report_id": result.report_id})
    if result.unknown_visit:
        # 举报文件留着（只有受理或用户放弃才删）、标记为被拒，队列里给「重试 / 放弃」
        return _error(404, "unknown_visit")
    tu.schedule_visit_retry(visit_id, config_dir=config_dir)
    if result.login_required:
        # 已排队，但登录失效：提示重新登录（原账号回来后由后台 / 启动补录接着提交）
        return _cloud_error(cr.VisitLoginRequired())
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

    async def _owned() -> bool:
        report = await tu.load_report(config_dir, visit_id)
        # 别的账号在这台机器上排的举报：不可见、不可删
        return report is not None and await tu.report_belongs_to(report, account)

    if action == "abandon":
        # 与后台提交共用逐场锁，并在锁内核对归属：等锁期间原举报可能已提交删除、
        # 另一账号又排了同一场的举报——删的必须是此刻这份、且属于当前账号
        async with tu.visit_lock(visit_id):
            if not await _owned() or not await tu.delete_report(config_dir, visit_id):
                return _error(404, "not_queued")
        return JSONResponse({"ok": True, "removed": True})
    if not await _owned():
        return _error(404, "not_queued")
    outcome = await tu.retry_visit_once(visit_id, config_dir=config_dir, manual=True)
    delivered = await tu.load_report(config_dir, visit_id) is None
    if not delivered and outcome.pending:
        tu.schedule_visit_retry(visit_id, config_dir=config_dir)
    return JSONResponse({"ok": True, "delivered": delivered})
