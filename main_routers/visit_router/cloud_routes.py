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
        # Servers 回了别的场次：按坏响应处理，不交给前端
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
        # 超出 float 范围的整数：isfinite 会抛，不能让一行坏数据把导出变成 500
        return False


def _utf8_str(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # JSON 里转义的孤立代理字符：解析得出字符串，却编不成 UTF-8，回给前端时会 500
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
        # 本侧字段在、却不是对象：坏行，不能当成「这一侧没有」静默漏掉
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
            # 契约（§4.7 details）：除最后一页外每页恰好 limit 行、任何一页都不超过 limit；
            # 不合的页会让页数上限算出的总量不成立（少了白翻满页数，多了撑破内存上界）
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
                # 不合契约的行丢掉、记一条诊断（每份转录一条，不逐行刷）
                memory_bridge.diag("cloud_transcript_rows_rejected", count=rejected)
            lines.sort(key=lambda line: (line["lp"], SIDE_RANK.get(line["side"], 2)))
            return {"source": "cloud", "visit_id": visit_id, "role": role, "lines": lines}
        if not isinstance(next_cursor, str) or not _CURSOR_RE.fullmatch(next_cursor) or next_cursor in seen_cursors:
            # 回到请求过的游标（原地不动或绕圈）会把同一页重复拼进来：按坏响应处理，不白等满页数
            logger.warning("visit servers details: malformed next_cursor")
            raise CloudError(_error(503, "servers_unreachable"))
        cursor = next_cursor
        seen_cursors.add(cursor)
    memory_bridge.diag("cloud_transcript_incomplete", pages=VISIT_DETAILS_MAX_PAGES)
    raise CloudTranscriptIncomplete(visit_id)


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
    try:
        # 孤立代理字符（JSON 里的 "\ud800"）解析得出、却写不进 UTF-8 的举报文件：当场按无效备注拒，不落成 500
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
        # 共用电脑上另一账号那一侧的异常计数不算进这份举报
        "anomalies": await tu.visit_anomalies(config_dir, visit_id, own_visit_uid),
        "app_version": cr._app_version(),
        "queued_at": time.time(),
    }
    # 入队与这一次提交在同一把逐场锁里：后台轮次（会把新排的举报顺手交掉）和另一个窗口的「放弃」
    # 都插不进两步之间，返回的结果就是这份举报的真实去向
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


async def _submit_new_report(config_dir: Path, doc: dict) -> JSONResponse:
    visit_id = doc["visit_id"]
    if doc["include_transcript"]:
        # 附转录的举报等本侧转录先到 Servers：在飞场次等收尾封存，已封存的先同步试传一次
        if tu.is_live(visit_id):
            return JSONResponse({"queued": True}, status_code=202)
        deferred = tu.upload_deferred_s(visit_id, doc.get("own_visit_uid"))
        if deferred > 0:
            # 这份举报那一侧的转录刚被 Servers 限流（Retry-After 未到）：不在这里同步重传，交给已推后的
            # 后台重试。另一账号那一侧的转录在限流时不挡（照常走下面的归属判断）
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=deferred)
            return JSONResponse({"queued": True}, status_code=202)
        try:
            upload = await tu.attempt_upload(visit_id, config_dir=config_dir)
        except (OSError, ValueError) as exc:
            # 举报已落盘：上传的进度记账出错（磁盘满等）不能变成 500，留给后台重试
            logger.warning("visit report %s: upload attempt failed: %s", visit_id, type(exc).__name__)
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
            return JSONResponse({"queued": True}, status_code=202)
        # 另一账号那一侧的转录不挡这份举报、也不借它的原因（仍可重试的照样排后台）
        upload = upload.for_report(doc)
        if not upload.pending and upload.retryable:
            # 已传上去、只是封存文件还没删掉：后台稍后清理
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
        if upload.pending:
            if upload.retryable:
                tu.schedule_visit_retry(visit_id, config_dir=config_dir,
                                        initial_delay_s=_after_attempt(upload.retry_after_s))
            if upload.login_required:
                # 已排队，但转录因登录失效传不上去：提示重新登录
                return _cloud_error(cr.VisitLoginRequired())
            return JSONResponse({"queued": True}, status_code=202)
        # 终态拒收 / 过期时 attempt_upload 已在举报文件里记下原因：重读一次带上；
        # 没能写进文件（磁盘 / 权限）时原因在 upload.unavailable 里，提交的这份照样带上
        doc = await tu.load_report(config_dir, visit_id) or doc
        if upload.unavailable and not doc.get("transcript_unavailable"):
            # 原因只在内存里（转录先于举报结清 / 之前没写成）：补写进排队的举报，重启后补录提交时也带着
            await tu.mark_report_transcript_unavailable(config_dir, visit_id, upload.unavailable)
            doc = {**doc, "transcript_unavailable": upload.unavailable}
    result = await tu.send_report(doc)
    if await tu.finish_report(config_dir, visit_id, result, doc):
        return JSONResponse({"ok": True, "report_id": result.report_id})
    if result.unknown_visit:
        # 举报文件留着（只有受理或用户放弃才删）、标记为被拒，队列里给「重试 / 放弃」。
        # 标记没写进去（磁盘 / 权限）时排一轮后台重试，下一轮再记
        if not await tu.rejection_recorded(config_dir, visit_id):
            tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(None))
        return _error(404, "unknown_visit")
    tu.schedule_visit_retry(visit_id, config_dir=config_dir, initial_delay_s=_after_attempt(result.retry_after_s))
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

    async def _owned_report() -> dict | None:
        report = await tu.load_report(config_dir, visit_id)
        # 别的账号在这台机器上排的举报：不可见、不可删
        return report if report is not None and await tu.report_belongs_to(report, account) else None

    async def _owned() -> bool:
        return await _owned_report() is not None

    if action == "abandon":
        # 与后台提交共用逐场锁，并在锁内核对归属：等锁期间原举报可能已提交删除、
        # 另一账号又排了同一场的举报——删的必须是此刻这份、且属于当前账号
        async with tu.visit_lock(visit_id):
            if not await _owned() or not await tu.delete_report(config_dir, visit_id):
                return _error(404, "not_queued")
        return JSONResponse({"ok": True, "removed": True})
    owned = await _owned_report()
    if owned is None:
        return _error(404, "not_queued")
    outcome = await tu.retry_visit_once(visit_id, config_dir=config_dir, manual=True, owner=account, expect=owned)
    # 读不了（被占用）不等于已送达：按还在处理
    current, unreadable = await tu._read_report(config_dir, visit_id)
    delivered = not unreadable and (current is None or not await tu.report_belongs_to(current, account))
    if not delivered and outcome.unknown_visit:
        # 又被拒：与 POST /report 同样回 404 unknown_visit，UI 照旧给「重试 / 放弃」
        return _error(404, "unknown_visit")
    if not delivered and outcome.login_required:
        # 原账号的登录已失效：提示重新登录（文件留着，登录后照常补提）
        return _cloud_error(cr.VisitLoginRequired())
    if not delivered and outcome.pending:
        tu.schedule_visit_retry(visit_id, config_dir=config_dir,
                                initial_delay_s=_after_attempt(outcome.retry_after_s))
    return JSONResponse({"ok": True, "delivered": delivered})
