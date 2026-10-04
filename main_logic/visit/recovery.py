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

"""Startup recovery of visit files (background task, never on the startup path).

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 item 7 and
PR-08 ``recovery.py``. :func:`visit_spool_recovery` is started with
``asyncio.create_task`` after startup (PR-09b) and runs, in order:

1. the character-rename reconciliation (``visit_peers.json.pending_rename``),
   first, so forgets resolve roster entries under their current name;
2. unfinished local forgets (clearing sentinels, then revocation logs);
3. startup cleanup: only leftover ``.outbox.jsonl`` files are deleted;
4. ``VisitSpool.sweep`` (7 days / 20 MB, pending uploads and unsettled
   visits are never reclaimed for size);
5. every visit's ``state.json``: a crashed visit (``finalized`` empty, not
   live) is marked ``crash``; a visit with digestable lines whose debrief
   is still open gets ``debrief_chip_pending`` (the chips are replayed on the
   next ``visit_bind``; nothing is written to private memory here); the
   visit-region digest and the last-visit summary are completed;
6. pending transcript uploads: every ``.upload.json`` is retried; a
   ``.upload.jsonl`` stream without one (a crash anywhere before finalize
   wrote it) is turned into one first, whatever ``finalized`` says;
7. queued reports in ``visit_reports/``, after their visit's upload.

Unreachable memory_server or Servers only leave files for the next start.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.visit_settings import VISIT_REPORTS_DIRNAME, VISIT_SPOOL_DIRNAME
from main_logic.visit import local_chars, memory_bridge
from main_logic.visit.forget_runner import VoidPending, replay_forgets
from main_logic.visit.memory_commit import (
    ResolveCharName,
    SummaryLLM,
    commit_last_summary,
    commit_visit_region,
    line_order_key,
)
from main_logic.visit.spool import (
    OUTBOX_SUFFIX,
    STATE_SUFFIX,
    UPLOAD_JSON_SUFFIX,
    UPLOAD_JSONL_SUFFIX,
    VisitSpool,
    is_digestable,
    is_spool_open,
)
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    clear_roster_marker,
    read_roster_marker,
)
from memory.scoped_client import ScopedMemoryClient
from utils.file_utils import atomic_write_json
from utils.logger_config import get_module_logger
from utils.visit_wire import VISIT_ID_RE, visit_path

logger = get_module_logger(__name__, "Main")

UPLOAD_DOC_VERSION = 1
_USAGE_KEYS = ("llm_input_tokens", "llm_output_tokens", "tts_requests", "tts_chars")
_LINE_FIELDS = ("lp", "side", "from", "ts", "text", "truncated")

RenderChips = Callable[..., Awaitable[bool]]
"""``render_chips(visit_id, *, own_char, status) -> bool``: try to show the debrief block now.

``status`` is ``'interrupted'`` for a crashed visit, else ``None``. The
return value is only whether it reached a connected display; the pending
flag in ``state.json`` stays until the user decides either way."""

UploadTranscript = Callable[[str, dict], Awaitable[bool]]
"""``upload_transcript(visit_id, upload_doc) -> bool``: upload one pending transcript.

``upload_doc`` is the ``.upload.json`` document (``{v, own_visit_uid,
own_char_uid, transport, request}``; ``request`` is the Servers wire body).
Returns True when the local file may go (accepted, duplicate or a terminal
rejection); False keeps it for a later retry (network error, 429, account
mismatch: uploads happen only while the signed-in account is
``own_visit_uid``). The callback may rewrite the file with its chunk
progress."""

SubmitReport = Callable[[str, dict], Awaitable[bool]]
"""``submit_report(visit_id, report_doc) -> bool``: True once Servers accepted the queued report."""

SpawnBackground = Callable[[str, Callable[[], Awaitable[Any]]], Any]
"""``spawn_background(own_char_uid, factory)``: run ``factory()`` as the character's visit background task."""

ResumeDiaryCommit = Callable[[VisitSpool, dict], Awaitable[Any]]
"""``resume_diary_commit(spool, state)``: continue a ``committing:diary`` two-step write (PR-14)."""


@dataclass
class RecoveryReport:
    """What one recovery pass did (for logs and tests)."""

    forgets_clean: bool = True
    renamed: bool = False
    crashed: list[str] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    digests: dict[str, bool] = field(default_factory=dict)
    summaries: dict[str, bool] = field(default_factory=dict)
    uploads: dict[str, bool] = field(default_factory=dict)
    reports: dict[str, bool] = field(default_factory=dict)
    swept: int = 0


# ── 上传流水 → 上传文件 ───────────────────────────────────────────────


def _read_stream(path: Path) -> list[dict]:
    data = path.read_bytes()
    parts = data.split(b"\n")
    tail = parts.pop()
    if tail:
        logger.warning("visit upload stream %s: dropped a partial trailing line", path.name)
    out = []
    for raw in parts:
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_upload_doc(records: list[dict], *, visit_id: str, finalized_reason: str | None) -> dict | None:
    """Build the ``.upload.json`` document of a crashed visit from its upload stream.

    ``records`` are the stream's JSON objects. The first must be the upload
    header ``{kind:'header', visit_id, role, own_visit_uid, started_at,
    own_char_uid, app_version, transport}``; without it ``None`` is returned
    (corrupt stream). ``ended_at`` is the ``ts`` of the last record that has
    one (the header's ``started_at`` when there is none), ``usage`` the sum of
    every usage delta, ``anomalies`` the number of anomaly records, ``lines``
    every line record in ``(lp, side_rank)`` order, and ``finalized_reason``
    the given reason or ``'crash'``.
    """
    if not records or records[0].get("kind") != "header":
        return None
    header = records[0]
    started_at = _number(header.get("started_at"))
    if header.get("visit_id") != visit_id or started_at is None or header.get("role") not in ("host", "guest"):
        return None
    usage = {key: 0 for key in _USAGE_KEYS}
    anomalies = 0
    lines: list[dict] = []
    ended_at = started_at
    for record in records[1:]:
        ts = _number(record.get("ts"))
        if ts is not None:
            ended_at = ts
        kind = record.get("kind")
        if kind == "line":
            if all(name in record for name in _LINE_FIELDS):
                lines.append({name: record[name] for name in _LINE_FIELDS})
        elif kind == "usage":
            delta = record.get("d")
            if isinstance(delta, dict):
                for key in _USAGE_KEYS:
                    value = delta.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        usage[key] += value
        elif kind == "anomaly":
            anomalies += 1
    lines.sort(key=line_order_key)
    request = {
        "visit_id": visit_id,
        "role": header["role"],
        "started_at": started_at,
        "ended_at": ended_at,
        "finalized_reason": finalized_reason or "crash",
        "usage": {"duration_s": max(0, int(ended_at - started_at)), **usage},
        "lines": lines,
        "anomalies": anomalies,
        "app_version": str(header.get("app_version") or ""),
    }
    return {
        "v": UPLOAD_DOC_VERSION,
        "own_visit_uid": header.get("own_visit_uid"),
        "own_char_uid": header.get("own_char_uid"),
        "transport": header.get("transport"),
        "request": request,
    }


def _write_private_json(path: Path, doc: dict) -> None:
    atomic_write_json(path, doc)
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        # 与凭证文件同一立场：权限位尽力而为（Windows 上本就无效），设不上不影响上传
        logger.debug("visit recovery: chmod 0600 failed for %s: %s", path.name, exc)


def _seal_stream_sync(spool_dir: Path, visit_id: str, finalized_reason: str | None) -> dict | None:
    stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
    sealed = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
    doc = build_upload_doc(_read_stream(stream), visit_id=visit_id, finalized_reason=finalized_reason)
    if doc is None:
        memory_bridge.diag("upload_stream_corrupt", visit_id=visit_id)
        stream.unlink(missing_ok=True)
        return None
    # 与 finalize 同一顺序：先原子写 .upload.json，再删流水
    _write_private_json(sealed, doc)
    stream.unlink(missing_ok=True)
    return doc


# ── 主流程 ────────────────────────────────────────────────────────────


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _reconcile_rename(config_dir: Path, names: set[str]) -> bool:
    try:
        marker = await read_roster_marker(config_dir, "pending_rename")
    except RosterCorruptError as exc:
        logger.error("visit recovery: roster unreadable, rename not reconciled: %s", exc)
        return False
    if not isinstance(marker, dict):
        return False
    old, new = marker.get("old"), marker.get("new")
    if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
        return False
    # rename_char 按机器上的角色名改写全部账号分区，与 own_uid 无关
    roster = PeerRoster(config_dir, own_uid="pending-rename")
    if new in names and old not in names:
        await roster.rename_char(old, new)
        await VisitSpool.rename_own_char(config_dir, old, new)
    elif old in names and new not in names:
        # 改名没生效：把已经改写成新名的场次改回旧名
        await VisitSpool.rename_own_char(config_dir, new, old)
        await roster.rename_char(new, old)
    else:
        logger.warning("visit recovery: pending_rename %r -> %r is ambiguous, kept", old, new)
        return False
    return await clear_roster_marker(config_dir, "pending_rename", marker)


async def _cleanup_outboxes(config_dir: Path, live: Callable[[str], bool]) -> None:
    spool_dir = Path(config_dir) / VISIT_SPOOL_DIRNAME
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (OUTBOX_SUFFIX,)):
        if live(visit_id):
            continue
        path = visit_path(spool_dir, visit_id, OUTBOX_SUFFIX)
        try:
            await asyncio.to_thread(path.unlink, True)
        except OSError as exc:
            logger.warning("visit recovery: cannot delete %s: %s", path.name, exc)


async def _has_digestable_lines(spool: VisitSpool, state: dict) -> bool:
    if not is_digestable(state):
        return False
    contents = await spool.read_back()
    return contents.header is not None and any(
        str(line.get("text") or "").strip() for line in contents.lines
    )


async def _recover_visit(
    visit_id: str,
    *,
    config_dir: Path,
    render_chips: RenderChips,
    resolve_char_name: ResolveCharName,
    spawn_background: SpawnBackground | None,
    summary_llm: SummaryLLM | None,
    family_names: Iterable[str],
    resume_diary_commit: ResumeDiaryCommit | None,
    client: ScopedMemoryClient | None,
    report: RecoveryReport,
) -> None:
    spool = VisitSpool(config_dir, visit_id)
    state = await spool.read_state()
    if state is None:
        return
    own_char = await resolve_char_name(state["own_char_uid"])
    if not own_char:
        # 角色已删：退役流程负责这场的文件，这里不出芯片、不写任何东西
        return
    choice = state["debrief_choice"]
    status = None
    show_chip = False
    if state["finalized"] is None:
        state = await spool.update_state(finalized="crash")
        report.crashed.append(visit_id)
        status = "interrupted"
        # 只在有可 digest 句时出芯片（记忆关的场次没有 .jsonl）；不写任何私聊记忆
        show_chip = choice in (None, "ask_later") and await _has_digestable_lines(spool, state)
    elif state["finalized"] == "shutdown" and choice is None:
        # 兜底：老版本关机没写 ask_later，或写之前就被杀
        if await _has_digestable_lines(spool, state):
            state = await spool.update_state(debrief_choice="ask_later")
            show_chip = True
    elif choice == "ask_later":
        show_chip = True
    if choice in ("generating:diary", "commit_failed:diary"):
        # 生成预览时崩溃 / 永久性写入失败：零 LLM、零写入，只经 bind 重放
        show_chip = True
    if show_chip:
        if not state["debrief_chip_pending"]:
            state = await spool.update_state(debrief_chip_pending=True)
        report.chips.append(visit_id)
        try:
            await render_chips(visit_id, own_char=own_char, status=status)
        except Exception as exc:  # noqa: BLE001 - 发不出去就等 bind 重放
            logger.warning("visit recovery: render_chips failed for %s: %r", visit_id, exc)
    if choice == "committing:diary" and resume_diary_commit is not None:
        await _maybe_await(resume_diary_commit(spool, state))

    async def digest() -> Any:
        return await commit_visit_region(spool, resolve_char_name=resolve_char_name, client=client)

    async def summary() -> Any:
        return await commit_last_summary(
            spool, llm=summary_llm, resolve_char_name=resolve_char_name, family_names=family_names,
        )

    jobs: list[tuple[str, Callable[[], Awaitable[Any]]]] = []
    if is_digestable(state):
        jobs.append(("digest", digest))
    if not state["last_summary_done"] and summary_llm is not None:
        jobs.append(("summary", summary))
    for kind, factory in jobs:
        if spawn_background is not None:
            result = await _maybe_await(spawn_background(state["own_char_uid"], factory))
        else:
            result = await factory()
        ok = bool(getattr(result, "ok", result))
        (report.digests if kind == "digest" else report.summaries)[visit_id] = ok


async def _upload_pending(
    config_dir: Path,
    *,
    live: Callable[[str], bool],
    upload_transcript: UploadTranscript | None,
    submit_report: SubmitReport | None,
    report: RecoveryReport,
) -> set[str]:
    """Retry pending uploads; return the visit ids whose upload is still pending."""
    spool_dir = config_dir / VISIT_SPOOL_DIRNAME
    pending: set[str] = set()
    sealed = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSON_SUFFIX,)))
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)):
        if live(visit_id):
            continue
        stream = visit_path(spool_dir, visit_id, UPLOAD_JSONL_SUFFIX)
        if visit_id in sealed:
            # 封存时「已写 .upload.json、还没删流水」就崩了：上传文件才是这场的那份，
            # 留着流水会在上传成功后被再封一次、重复上传
            try:
                await asyncio.to_thread(stream.unlink, True)
            except OSError as exc:
                logger.warning("visit recovery: cannot delete stale stream %s: %s", stream.name, exc)
                pending.add(visit_id)
            continue
        # 转录补传与 finalized 无关：流水还在、上传文件没写成，就从流水构建
        state = None
        try:
            state = await VisitSpool(config_dir, visit_id).read_state()
        except (OSError, ValueError) as exc:
            logger.warning("visit recovery: state of %s unreadable, upload marked crash: %s", visit_id, exc)
        reason = state["finalized"] if state else None
        try:
            doc = await asyncio.to_thread(_seal_stream_sync, spool_dir, visit_id, reason)
        except OSError as exc:
            # 一份流水读写不了只跳过它自己，不能挡住其余场次的补传与举报
            logger.warning("visit recovery: cannot seal %s: %s", stream.name, exc)
            pending.add(visit_id)
            continue
        if doc is not None:
            sealed.add(visit_id)
    for visit_id in sorted(sealed):
        if live(visit_id):
            # 在飞场次的转录还没传：它排队的举报也不能先交
            pending.add(visit_id)
            continue
        path = visit_path(spool_dir, visit_id, UPLOAD_JSON_SUFFIX)
        try:
            doc = await asyncio.to_thread(_load_json, path)
        except (OSError, ValueError) as exc:
            logger.warning("visit recovery: pending upload %s unreadable: %s", path.name, exc)
            pending.add(visit_id)
            continue
        if doc is None:
            continue
        if upload_transcript is None:
            pending.add(visit_id)
            continue
        try:
            ok = bool(await upload_transcript(visit_id, doc))
        except Exception as exc:  # noqa: BLE001 - 任何失败都留文件下次再试
            logger.warning("visit recovery: upload of %s failed: %r", visit_id, exc)
            ok = False
        report.uploads[visit_id] = ok
        if not ok:
            pending.add(visit_id)
            continue
        await asyncio.to_thread(path.unlink, True)
        # 该场转录上传成功后，接着提交它排队的举报
        await _submit_report(config_dir, visit_id, submit_report, report)
    return pending


def _load_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        raise ValueError("too deeply nested") from exc


async def _submit_report(
    config_dir: Path, visit_id: str, submit_report: SubmitReport | None, report: RecoveryReport,
) -> None:
    if submit_report is None:
        return
    path = visit_path(config_dir / VISIT_REPORTS_DIRNAME, visit_id, ".json")
    try:
        doc = await asyncio.to_thread(_load_json, path)
    except (OSError, ValueError) as exc:
        logger.warning("visit recovery: queued report %s unreadable: %s", path.name, exc)
        return
    if doc is None:
        return
    try:
        ok = bool(await submit_report(visit_id, doc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("visit recovery: report of %s failed: %r", visit_id, exc)
        ok = False
    report.reports[visit_id] = ok
    if ok:
        # 举报文件只在 Servers 受理后删
        await asyncio.to_thread(path.unlink, True)


async def _submit_reports(
    config_dir: Path, *, skip: set[str], submit_report: SubmitReport | None, report: RecoveryReport,
) -> None:
    directory = config_dir / VISIT_REPORTS_DIRNAME
    try:
        names = await asyncio.to_thread(os.listdir, directory)
    except FileNotFoundError:
        return
    for name in sorted(names):
        visit_id = name[: -len(".json")] if name.endswith(".json") else ""
        if not VISIT_ID_RE.fullmatch(visit_id) or visit_id in skip or visit_id in report.reports:
            continue
        await _submit_report(config_dir, visit_id, submit_report, report)


async def visit_spool_recovery(
    render_chips: RenderChips,
    upload_transcript: UploadTranscript | None = None,
    *,
    is_live: Callable[[str], bool] | None = None,
    spawn_background: SpawnBackground | None = None,
    config_dir: str | Path | None = None,
    resolve_char_name: ResolveCharName | None = None,
    list_char_names: Callable[[], Awaitable[Iterable[str]]] | None = None,
    summary_llm: SummaryLLM | None = None,
    family_names: Iterable[str] = (),
    submit_report: SubmitReport | None = None,
    resume_diary_commit: ResumeDiaryCommit | None = None,
    void_pending: VoidPending | None = None,
    client: ScopedMemoryClient | None = None,
    now: float | None = None,
) -> RecoveryReport:
    """Run one startup recovery pass over the visit files (see the module docstring).

    ``is_live(visit_id)`` tells visits of a ``VisitRuntime`` alive in this
    process (their files are never touched). ``spawn_background`` routes the
    digest / summary commits through the character's visit background-task
    entry. ``summary_llm`` is required for last-visit summaries (without it
    they wait for a later pass). The other callbacks are optional and their
    steps are skipped (files kept) when missing. Independent of
    ``visitMemoryEnabled`` and of the ``NEKO_VISIT_ENABLED`` release switch.
    """
    report = RecoveryReport()
    if config_dir is None:
        from utils.config_manager import get_config_manager

        config_dir = get_config_manager().config_dir
    config_dir = Path(config_dir)
    resolve = resolve_char_name or local_chars.resolve_char_name
    live = is_live or (lambda _visit_id: False)
    # 改名对账先于清除重放：清除按角色当前名字找名册条目，改名迁移没做完时条目还在
    # 旧名字下，remove_char 会「成功」地什么都没删，随后迁移又把条目连同摘要搬到新名字
    try:
        names = (set(await list_char_names()) if list_char_names is not None
                 else set((await local_chars.load_local_characters()).keys()))
        report.renamed = await _reconcile_rename(config_dir, names)
    except Exception as exc:  # noqa: BLE001 - 补录各段互不连累
        logger.error("visit recovery: rename reconciliation failed: %r", exc)
    try:
        report.forgets_clean = await replay_forgets(
            config_dir, resolve_char_name=resolve, client=client, void_pending=void_pending,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("visit recovery: forget replay failed: %r", exc)
        report.forgets_clean = False
    await _cleanup_outboxes(config_dir, live)
    try:
        report.swept = len(await VisitSpool.sweep(config_dir, time.time() if now is None else now))
    except Exception as exc:  # noqa: BLE001
        logger.error("visit recovery: sweep failed: %r", exc)
    for visit_id in await VisitSpool.list_visit_ids(config_dir, (STATE_SUFFIX,)):
        if live(visit_id) or is_spool_open(visit_path(config_dir / VISIT_SPOOL_DIRNAME, visit_id, ".jsonl")):
            continue
        try:
            await _recover_visit(
                visit_id, config_dir=config_dir, render_chips=render_chips,
                resolve_char_name=resolve, spawn_background=spawn_background,
                summary_llm=summary_llm, family_names=family_names,
                resume_diary_commit=resume_diary_commit, client=client, report=report,
            )
        except Exception as exc:  # noqa: BLE001 - 一场坏了不挡其余场次
            logger.warning("visit recovery: visit %s skipped: %r", visit_id, exc)
    pending = await _upload_pending(
        config_dir, live=live, upload_transcript=upload_transcript,
        submit_report=submit_report, report=report,
    )
    # 另外独立扫描举报队列：转录已上传（.upload.json 已删）而举报还没提交的也继续提交
    streams = set(await VisitSpool.list_visit_ids(config_dir, (UPLOAD_JSONL_SUFFIX,)))
    await _submit_reports(config_dir, skip=pending | streams, submit_report=submit_report, report=report)
    return report
