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

"""Visit-region commit and the last-visit summary of one finished visit.

Design: ``docs/design/visit-infrastructure.md`` section 3.7.3 and PR-08
``memory_commit.py``.

Both run once per visit, at finalize (as background work) or from startup
recovery, and both look only at the visit's own ``state.json.memory_enabled``
(frozen when the visit began); the debrief choice never matters here.

* :func:`commit_visit_region` digests the spool into the visit memory region:
  the group digest (``/scoped_history`` on ``group_chat``) and the two peer
  speakers (segments batch). Lines are capped at ``VISIT_DIGEST_MAX_LINES``
  (every cat line, then the newest human lines), sorted by
  ``(lp, side_rank)`` and cut into consecutive batches of at most
  ``SCOPED_HISTORY_BATCH_MAX_MESSAGES``. Each batch carries the key
  ``visit-digest:{visit_id}:{run}:group|segments:{batch}``; the run (only
  ``run=0`` today, no periodic digest) and every batch are registered in
  ``state.json.digest_writes`` before the first request and ticked off one by
  one, so a retry resends exactly the unfinished batches with the same lines.
* :func:`commit_last_summary` makes one LLM call that summarizes the visit for
  the next visit with the same person and stores it in the roster only.

Both hold :func:`peer_lock` ``(own_char_uid, peer_uid)``, the same lock the
local forget takes, so a forget never interleaves with them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import SCOPED_HISTORY_BATCH_MAX_MESSAGES
from config.prompts.prompts_visit import (
    build_visit_last_summary_prompt,
    build_visit_record_block,
    get_family_neutral_term,
    get_visit_speaker_header,
)
from config.visit_settings import (
    VISIT_DIGEST_MAX_LINES,
    VISIT_SHUTDOWN_BUDGET_S,
    VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS,
    VISIT_LAST_SUMMARY_MAX_TOKENS,
    VISIT_LLM_TIMEOUT_S,
    VISIT_PEER_NGRAM_N,
)
from main_logic.visit import memory_bridge
from main_logic.visit.forget import ForgetEpochs, ForgetEpochsUnreadable, ForgetEpochsUnsynced
from main_logic.visit.sanitize import (
    PeerNgramHit,
    assert_no_peer_ngram,
    neutralize_display_name,
    redact_outbound,
    strip_emotion_tags,
)
from main_logic.visit.spool import VisitSpool, is_digestable, is_spool_open
from main_logic.visit.subjects import (
    PeerRoster,
    RosterCorruptError,
    derive_person_id,
    derive_short_code,
    group_chat_subject,
    group_participant_subject,
    participant_subject,
    read_roster_marker,
)
from memory.scoped_client import ScopedMemoryClient
from utils.language_utils import is_supported_language_code
from utils.logger_config import get_module_logger
from utils.tokenize import count_tokens, take_lines_within_token_budget, truncate_to_tokens

logger = get_module_logger(__name__, "Main")

SIDE_RANK = {"host": 0, "guest": 1}
_CAT_SPEAKERS = ("own_cat", "peer_cat")
_PEER_SPEAKERS = ("peer_cat", "peer_human")

ResolveCharName = Callable[[str], Awaitable["str | None"]]
"""``own_char_uid`` -> the local character's current name, ``None`` once deleted."""

SummaryLLM = Callable[[str], Awaitable[str]]
"""One-shot LLM call: prompt in, text out (no session, no history)."""


# ── 每对一把锁 ────────────────────────────────────────────────────────

_PEER_LOCKS: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()


def peer_lock(own_char_uid: str, peer_uid: str) -> asyncio.Lock:
    """Return the process-wide lock of one ``(own_char_uid, peer_uid)`` pair.

    Taken by :func:`commit_visit_region` (finalize and recovery),
    :func:`commit_last_summary` and every local forget of that pair. Callers
    keep the returned lock referenced while holding or waiting on it (the
    registry holds it weakly, an idle pair's lock is dropped).
    """
    key = (str(own_char_uid), str(peer_uid))
    lock = _PEER_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PEER_LOCKS[key] = lock
    return lock


# ── 选句与切批（纯函数）──────────────────────────────────────────────


def line_order_key(line: Mapping[str, Any]) -> tuple[int, int]:
    """Total order of spool lines: ``(lp, side_rank)``, host before guest on a tie."""
    return (int(line["lp"]), SIDE_RANK.get(line.get("side"), 2))


def select_digest_lines(
    lines: Iterable[Mapping[str, Any]], max_lines: int | None = None,
) -> tuple[list[dict], int]:
    """Cap one run's lines at ``max_lines`` (default ``VISIT_DIGEST_MAX_LINES``).

    Every cat line (both sides) is kept first, then human lines from the
    newest backwards until the cap. Returns ``(selected, dropped)`` with
    ``selected`` in ``(lp, side_rank)`` order.
    """
    limit = VISIT_DIGEST_MAX_LINES if max_lines is None else int(max_lines)
    ordered = sorted((dict(line) for line in lines), key=line_order_key)
    cats = [line for line in ordered if line["from"] in _CAT_SPEAKERS]
    humans = [line for line in ordered if line["from"] not in _CAT_SPEAKERS]
    if len(cats) > limit:
        # 协议上猫娘行一场 ≤80，超出只能是坏数据：仍守总量，取最新的
        cats = cats[-limit:]
    room = max(0, limit - len(cats))
    kept_humans = humans[-room:] if room else []
    selected = sorted(cats + kept_humans, key=line_order_key)
    return selected, len(ordered) - len(selected)


def split_batches(lines: Sequence[dict], size: int | None = None) -> list[list[dict]]:
    """Cut ``lines`` (already ordered) into consecutive batches of at most ``size``."""
    size = SCOPED_HISTORY_BATCH_MAX_MESSAGES if size is None else int(size)
    return [list(lines[i:i + size]) for i in range(0, len(lines), size)]


def plan_digest_batches(
    selected: Sequence[dict], size: int | None = None,
) -> tuple[list[list[dict]], list[list[dict]]]:
    """Return ``(group_batches, segments_batches)`` of one run.

    The group digest takes every selected line; the segments batches take the
    selected peer lines only, both peer speakers counted together per batch.
    """
    peer = [line for line in selected if line["from"] in _PEER_SPEAKERS]
    return split_batches(selected, size), split_batches(peer, size)


def _batch_fingerprint(batch: Sequence[Mapping[str, Any]]) -> str:
    """Short digest of which lines a batch holds and what they say (``lp``, ``side``, speaker, text)."""
    # 正文也算进去：开轮后某行被改了内容，续跑就不能沿用旧计划、拿旧键发出不同的内容
    identity = [[line["lp"], line["side"], line["from"], line.get("text")] for line in batch]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def digest_key(visit_id: str, run: int, part: str, batch: int) -> str:
    """``visit-digest:{visit_id}:{run}:{part}:{batch}`` (``part`` is ``group`` or ``segments``)."""
    return f"visit-digest:{visit_id}:{run}:{part}:{batch}"


# ── 串门区 digest ────────────────────────────────────────────────────


@dataclass
class CommitResult:
    """Outcome of :func:`commit_visit_region`.

    ``ok`` means nothing is left to retry for now (done, or skipped because
    there is nothing this function may do); ``requests`` counts the scoped
    history calls sent; ``skipped`` names the reason nothing was sent.
    """

    ok: bool
    requests: int = 0
    skipped: str | None = None
    run: int | None = None
    dropped_lines: int = 0


def _visit_subjects(state: Mapping[str, Any]) -> list[dict]:
    pair_id = state["pair_id"]
    return [
        group_chat_subject(pair_id),
        group_participant_subject(pair_id, state["peer_char_id"]),
        participant_subject(derive_person_id(state["own_uid"], state["peer_uid"])),
    ]


_IDENTITY_FIELDS = ("own_uid", "own_char_uid", "peer_uid", "pair_id", "peer_char_id")


def _header_matches_state(header: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    """Whether the transcript header and ``state.json`` describe the same visit identity."""
    return all(header.get(name) == state[name] for name in _IDENTITY_FIELDS)


def _protected_display_names(
    own_char: str, lang: str | None, family_names: Iterable[str], local_char_names: Iterable[str],
) -> tuple[str, ...]:
    """Names a peer-reported display name must never take over (see ``neutralize_display_name``).

    The local character and every other local character, the local family
    names, the neutral family term, and the speaker labels of all four visit
    speakers (own cat / own human as well as the two peer labels).
    """
    labels = tuple(
        get_visit_speaker_header(speaker, lang).strip("[] ")
        for speaker in ("own_cat", "own_human", "peer_cat", "peer_human")
    )
    return (own_char, *local_char_names, *labels, get_family_neutral_term(lang), *family_names)


async def _peer_displays(
    config_dir: Path, state: Mapping[str, Any], own_char: str, lang: str | None,
    family_names: Iterable[str] = (), local_char_names: Iterable[str] = (),
) -> tuple[str, str]:
    cat_label = get_visit_speaker_header("peer_cat", lang).strip("[] ")
    human_label = get_visit_speaker_header("peer_human", lang).strip("[] ")
    short = derive_short_code(state["peer_uid"])
    roster = PeerRoster(config_dir, own_uid=state["own_uid"])
    # 显示名只是装饰：名册条目结构坏了也退回通用标签，不能让 segments digest 失败
    peer = await roster.get_peer(state["peer_uid"])
    entry = await roster.get_char_entry(state["peer_uid"], own_char)
    chars = entry.get("chars") if isinstance(entry, dict) else None
    cat_info = chars.get(state["peer_char_id"]) if isinstance(chars, dict) else None
    peer = peer if isinstance(peer, dict) else {}
    cat_info = cat_info if isinstance(cat_info, dict) else {}
    # 对端自报的名字冒充本地角色 / 家人 / 己方或对端的说话人标签时换成通用标签：否则对端的话会以
    # 本地角色（或「你」「你的家里人」）的名义进 speaker_label 与 display_name，抽出的事实归属就错了
    protected = _protected_display_names(own_char, lang, family_names, local_char_names)
    cat = neutralize_display_name(cat_info.get("display_name"), protected_names=protected,
                                  generic_label=cat_label, short_code=short)
    human = neutralize_display_name(peer.get("display_name"), protected_names=protected,
                                    generic_label=human_label, short_code=short)
    return cat, human


async def commit_visit_region(
    spool: VisitSpool,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None = None,
    shutdown: bool = False,
    now: float | None = None,
    family_names: Iterable[str] = (),
    local_char_names: Iterable[str] = (),
) -> CommitResult:
    """Digest one finished visit into the visit memory region (finalize or recovery).

    Runs only when ``state.json.memory_enabled`` (the value frozen at the
    start of the visit) is true and the visit is finalized; otherwise zero
    requests. Inside :func:`peer_lock` it resumes the unfinished run (same
    ``through_lp``, ``requested_at``, forget generations and batches) or
    registers a new one for the lines past ``digested_through_lp``, then sends
    every unfinished group batch and segments batch, ticking each off in
    ``state.json`` as soon as it is confirmed. The first failure stops the
    run (the rest waits for the next recovery). When every batch of the run
    is done, ``digested_through_lp`` / ``digest_runs`` advance, and the
    ``.jsonl`` is deleted once the visit is settled.

    A run freezes in its plan, besides the batching parameters, everything
    the requests render that the server fingerprints under a key: the
    ``language`` actually sent, the group speaker prefixes and the two peer
    display names; a resumed run sends exactly those. A resumed run whose
    batches no longer match the transcript (lines changed or lost after it
    opened) is marked ``abandoned`` and closed (the watermark advances, the
    visit can settle) instead of resending other lines under its keys.

    ``family_names`` and ``local_char_names`` (the current names of the other
    local characters) are protected from peer display names: a peer named
    like one of them is stored under the generic peer label instead.
    """
    state = await spool.read_state()
    if state is None:
        return CommitResult(ok=True, skipped="no_state")
    if not is_digestable(state):
        return CommitResult(ok=True, skipped="memory_off")
    if state["finalized"] is None:
        # finalize 之前一律不提交：串门区只在收口（或崩溃补录）时整理一次
        return CommitResult(ok=False, skipped="not_finalized")
    if state["peer_uid"] is None:
        return CommitResult(ok=True, skipped="peer_forgotten")
    lock = peer_lock(state["own_char_uid"], state["peer_uid"])

    async def locked() -> CommitResult:
        async with lock:
            return await _commit_locked(spool, resolve_char_name=resolve_char_name,
                                        client=client, shutdown=shutdown, now=now,
                                        family_names=family_names,
                                        local_char_names=local_char_names)

    if shutdown:
        # 关机预算按整次提交算：等锁（可能有摘要的 LLM 调用正持着它）与先后几个批次
        # 共用同一个 VISIT_SHUTDOWN_BUDGET_S，超时就把剩下的留给补录
        try:
            result = await asyncio.wait_for(locked(), VISIT_SHUTDOWN_BUDGET_S)
        except asyncio.TimeoutError:
            logger.warning("visit digest exceeded the shutdown budget; left for recovery")
            return CommitResult(ok=False, skipped="shutdown_budget")
    else:
        result = await locked()
    if result.ok:
        await spool.delete_if_settled()
    return result


async def _commit_locked(
    spool: VisitSpool,
    *,
    resolve_char_name: ResolveCharName,
    client: ScopedMemoryClient | None,
    shutdown: bool,
    now: float | None,
    family_names: Iterable[str] = (),
    local_char_names: Iterable[str] = (),
) -> CommitResult:
    # 锁内重读：等锁期间「清除这个人」可能已抹掉对端身份
    state = await spool.read_state()
    if state is None:
        return CommitResult(ok=True, skipped="no_state")
    if not is_digestable(state):
        return CommitResult(ok=True, skipped="memory_off")
    if state["peer_uid"] is None:
        return CommitResult(ok=True, skipped="peer_forgotten")
    if await memory_bridge.forget_in_progress(spool.config_dir, state["own_char_uid"], state["peer_uid"],
                                              own_uid=state["own_uid"]):
        # 清除做到一半（memory_server 不可用时日志留着待重放）：此时开轮会带上已加过的清除代数，
        # 服务端不会挡，已清掉的记忆就被写回。等清除完成（对端身份随之抹掉）再说
        return CommitResult(ok=False, skipped="forget_in_progress")
    name = await resolve_char_name(state["own_char_uid"])
    if not name:
        return CommitResult(ok=True, skipped="character_deleted")
    contents = await spool.read_back()
    header = contents.header
    if header is None:
        return CommitResult(ok=True, skipped="no_transcript")
    if not _header_matches_state(header, state):
        memory_bridge.diag("digest_header_mismatch", visit_id=spool.visit_id)
        return CommitResult(ok=False, skipped="header_mismatch")
    lang = header.get("lang")
    runs = {k: dict(v) for k, v in state["digest_writes"].items()}
    resume = bool(runs) and state["digest_runs"] == len(runs) - 1
    previous = state["digested_through_lp"]
    if resume:
        run = len(runs) - 1
        record = runs[str(run)]
        through = record["through_lp"]
    else:
        fresh = [line for line in contents.lines if line["lp"] > previous]
        if not fresh:
            return CommitResult(ok=True, skipped="nothing_new")
        run = len(runs)
        through = max(line["lp"] for line in fresh)
    run_lines = [line for line in contents.lines if previous < line["lp"] <= through]
    # 续跑按开轮时记下的切批参数切：升级改了句数上限 / 每批句数，批号与幂等键仍对得上
    plan = dict(record.get("plan") or {}) if resume else {}
    max_lines = int(plan.get("max_lines", VISIT_DIGEST_MAX_LINES))
    batch_size = int(plan.get("batch_size", SCOPED_HISTORY_BATCH_MAX_MESSAGES))
    if batch_size > SCOPED_HISTORY_BATCH_MAX_MESSAGES:
        # 开轮时的每批句数比当前的上行上限大（升级把这个协议上限调低了）：按原计划续跑
        # 发不出去，换新键重切又会与已登记的批次与服务端记录冲突。不抛错卡住每次启动，
        # 记诊断、留着这一轮（7 天按龄回收）
        memory_bridge.diag("digest_plan_exceeds_wire_limit", visit_id=spool.visit_id, run=run,
                           batch_size=batch_size, limit=SCOPED_HISTORY_BATCH_MAX_MESSAGES)
        return CommitResult(ok=False, skipped="plan_exceeds_wire_limit", run=run)
    selected, dropped = select_digest_lines(run_lines, max_lines)
    group_batches, segment_batches = plan_digest_batches(selected, batch_size)
    subjects = _visit_subjects(state)
    membership = {
        "group": [_batch_fingerprint(batch) for batch in group_batches],
        "segments": [_batch_fingerprint(batch) for batch in segment_batches],
    }
    if resume:
        recorded = record.get("membership")
        if (
            len(record["group"]) != len(group_batches) or len(record["segments"]) != len(segment_batches)
            # 批数相同、边界却挪了（开轮后某行读不出被丢弃）：已确认的批次就会漏掉挪进来的句子，
            # 待发的批次也会拿别的句子用旧键重发。逐批核对开轮时的成员指纹
            or (recorded is not None and recorded != membership)
        ):
            # 切批只由 through_lp 与句序决定；对不上说明转录被改动过，不能拿别的句子用旧键重发。
            # 这是终态：转录里那几行不会自己恢复，重试也不会成功。把这一轮记成放弃并推进水位
            # （region_settled 按已结清），转录按正常流程释放，不再每次启动重读、重记诊断空转 7 天
            memory_bridge.diag("digest_batches_mismatch", visit_id=spool.visit_id, run=run)
            record["abandoned"] = "batches_mismatch"
            await spool.update_state(digest_writes=runs, digested_through_lp=through, digest_runs=run + 1)
            return CommitResult(ok=True, skipped="batches_mismatch", run=run, dropped_lines=dropped)
        # 续跑原样使用开轮时定格的请求渲染：language、group 的说话人前缀、segments 的显示名都进
        # 服务端的请求指纹，现算的话跨版本（支持语言表 / 标签模板 / 名册显示名变了）同键不同体会被永久 422。
        # 没有这些字段的旧计划按当前规则现算（与开轮时同一套算法；只有这些字段加入之前开的轮会走到）
        language = plan["language"] if "language" in plan else _wire_language(lang)
        headers = plan.get("headers")
        if headers is None:
            headers = memory_bridge.group_speaker_headers(selected, lang)
        displays = plan.get("displays")
        if language is not None and not is_supported_language_code(language):
            # 开轮时发出了这个语言码、当前版本已不支持：客户端会把它丢掉，请求体对不上必 422。
            # 与切批上限变小同一处理：记诊断、留着这一轮（7 天按龄回收）
            memory_bridge.diag("digest_plan_language_unsupported", visit_id=spool.visit_id, run=run)
            return CommitResult(ok=False, skipped="plan_language_unsupported", run=run)
    else:
        try:
            # 开轮前先把本地清除代数抬到服务端墓碑的当前值：云存档恢复 / 换机后本地从 0 重计，
            # 低于已有墓碑的整轮写入会被服务端静默丢弃
            await memory_bridge.sync_forget_epochs(name, subjects, config_dir=spool.config_dir, client=client)
            epochs = await ForgetEpochs(spool.config_dir).get(subjects)
        except ForgetEpochsUnsynced as exc:
            memory_bridge.diag("forget_epochs_unsynced", error=str(exc))
            return CommitResult(ok=False, skipped="epochs_unsynced")
        except ForgetEpochsUnreadable as exc:
            memory_bridge.diag("forget_epochs_unreadable", error=str(exc))
            return CommitResult(ok=False, skipped="epochs_unreadable")
        language = _wire_language(lang)
        headers = memory_bridge.group_speaker_headers(selected, lang)
        displays = None
        if segment_batches:
            cat_display, human_display = await _peer_displays(
                spool.config_dir, state, name, lang, family_names, local_char_names,
            )
            displays = {"peer_cat": cat_display, "peer_human": human_display}
        plan = {"max_lines": max_lines, "batch_size": batch_size, "language": language, "headers": headers}
        if displays is not None:
            plan["displays"] = displays
        record = {
            "requested_at": time.time() if now is None else float(now),
            "through_lp": through,
            "group": {str(b): False for b in range(len(group_batches))},
            "segments": {str(b): False for b in range(len(segment_batches))},
            "epochs": epochs,
            # 开轮时定格切批参数与实际要发出的请求渲染，续跑原样使用
            "plan": plan,
            "membership": membership,
        }
        runs[str(run)] = record
        # 先落盘本轮 through_lp / requested_at / 全部批号 / 清除代数，再发第一个请求：重试沿用它们
        await spool.update_state(digest_writes=runs)
        if dropped:
            memory_bridge.diag("digest_lines_capped", visit_id=spool.visit_id, dropped=dropped)
    epochs = dict(record.get("epochs") or {})
    requested_at = record["requested_at"]
    requests = 0
    group_subject = subjects[0]
    for b, batch in enumerate(group_batches):
        if record["group"][str(b)]:
            continue
        requests += 1
        ok = await memory_bridge.post_visit_digest(
            name, state["pair_id"], batch, subject=group_subject, lang=language,
            idempotency_key=digest_key(spool.visit_id, run, "group", b),
            client_requested_at=requested_at,
            subject_epochs=_epochs_for(epochs, [group_subject]),
            shutdown=shutdown, client=client, speaker_headers=headers,
        )
        if not ok:
            return CommitResult(ok=False, requests=requests, run=run, dropped_lines=dropped)
        record["group"][str(b)] = True
        await spool.update_state(digest_writes=runs)
    if segment_batches and displays is None:
        # 旧计划没有定格显示名：按当前名册现算
        cat_display, human_display = await _peer_displays(spool.config_dir, state, name, lang, family_names,
                                                          local_char_names)
        displays = {"peer_cat": cat_display, "peer_human": human_display}
    for b, batch in enumerate(segment_batches):
        if record["segments"][str(b)]:
            continue
        requests += 1
        ok = await memory_bridge.post_visit_segments(
            name, pair_id=state["pair_id"], own_uid=state["own_uid"], peer_uid=state["peer_uid"],
            peer_char_id=state["peer_char_id"], peer_cat_display=displays["peer_cat"],
            peer_human_display=displays["peer_human"], lines=batch,
            idempotency_key=digest_key(spool.visit_id, run, "segments", b),
            client_requested_at=requested_at,
            subject_epochs=_epochs_for(epochs, subjects[1:]), lang=language,
            shutdown=shutdown, client=client,
        )
        if not ok:
            return CommitResult(ok=False, requests=requests, run=run, dropped_lines=dropped)
        record["segments"][str(b)] = True
        await spool.update_state(digest_writes=runs)
    # 这一轮全部批次都确认：推进水位（与批次表同一次原子写）
    await spool.update_state(
        digest_writes=runs, digested_through_lp=through, digest_runs=run + 1,
    )
    return CommitResult(ok=True, requests=requests, run=run, dropped_lines=dropped)


def _wire_language(lang: Any) -> str | None:
    """The ``language`` a digest request actually carries: ``lang`` when it is a supported code."""
    return lang if is_supported_language_code(lang) else None


def _epochs_for(epochs: Mapping[str, int], subjects: Iterable[Mapping[str, str]]) -> dict[str, int]:
    out = {}
    for subject in subjects:
        key = f"{subject['subject_kind']}:{subject['subject_id']}"
        out[key] = int(epochs.get(key, 0))
    return out


# ── 上次串门摘要 ──────────────────────────────────────────────────────

_SUMMARY_TASKS: dict[str, "asyncio.Future[Any]"] = {}


def track_last_summary(visit_id: str, awaitable: Awaitable[Any]) -> "asyncio.Future[Any]":
    """Register the in-flight last-summary commit of ``visit_id`` and return it as a future.

    :func:`last_summary_handoff` waits on registered futures instead of
    starting a second commit for the same visit.
    """
    future = asyncio.ensure_future(awaitable)
    _SUMMARY_TASKS[visit_id] = future

    def _forget(done: "asyncio.Future[Any]") -> None:
        if _SUMMARY_TASKS.get(visit_id) is done:
            _SUMMARY_TASKS.pop(visit_id, None)

    future.add_done_callback(_forget)
    return future


async def last_summary_handoff(
    config_dir: str | Path,
    *,
    own_uid: str,
    own_char_uid: str,
    peer_uid: str,
    start_summary: Callable[[VisitSpool], Awaitable[Any]] | None = None,
    is_live: Callable[[str], bool] | None = None,
    opening_visit_id: str | None = None,
) -> None:
    """Let the previous visits of this pair finish their last-visit summary.

    ``opening_visit_id`` is the visit being opened right now: it is never
    treated as a previous visit, whatever order the caller registers it as
    live in.

    For every local visit of ``(own_char_uid, pair)`` whose summary is not
    done and that is no longer running (finalized, or not live: a crash not
    recovered yet; never while this process still holds its transcript open
    for appends, the same rule startup recovery applies), wait for its
    registered commit or start one through
    ``start_summary`` (which goes through the visit background-task entry).
    Then take and release :func:`peer_lock`, so a commit holding it has
    finished. Callers bound the whole wait (``VISIT_LAST_SUMMARY_HANDOFF_S``);
    the commits themselves are shielded and keep running after a timeout.
    """
    from main_logic.visit.subjects import derive_pair_id

    pair_id = derive_pair_id(own_uid, peer_uid)
    try:
        # 宽松查找：读不出的场次跳过，只返回能确认属于这一对的；一场无关的坏文件不能
        # 让这一对的所有上一场都不等
        visit_ids = await VisitSpool.find_visits_for_pairs(config_dir, own_char_uid, [pair_id], strict=False)
    except Exception as exc:  # noqa: BLE001 - 扫描失败只是少等，不挡开场
        logger.warning("visit last-summary handoff: cannot scan spools: %s", exc)
        visit_ids = []
    waits: list[asyncio.Future[Any]] = []
    for visit_id in visit_ids:
        if visit_id == opening_visit_id:
            # 正要开场的这一场自己：可能还没登记成在飞，不能被当成崩溃场次标 crash
            continue
        running = _SUMMARY_TASKS.get(visit_id)
        if running is not None:
            waits.append(running)
            continue
        if start_summary is None or (is_live is not None and is_live(visit_id)):
            continue
        spool = VisitSpool(config_dir, visit_id)
        if is_spool_open(spool.jsonl_path):
            # 与补录同一口径：本进程里这场的 writer 还开着（已从 is_live 注销、追加写与收口还在排队），
            # 此时标 crash 会拿半截转录生成摘要，随后真正的收口被覆盖或拒绝
            continue
        try:
            state = await spool.read_state()
        except Exception:  # noqa: BLE001
            continue
        if state is None or state["last_summary_done"]:
            continue
        if state["finalized"] is None:
            if is_live is None:
                continue
            # 已确认不在飞、却没收口：上次崩溃、补录还没跑到它。先照补录的口径标成 crash
            # （芯片由补录照常补弹），摘要的收口闸才放行——它不会对在飞场次的半截转录生成
            try:
                await spool.update_state(finalized="crash")
            except Exception as exc:  # noqa: BLE001 - 标不上就不替它生成，等补录
                logger.warning("visit last-summary handoff: cannot mark %s crashed: %r", visit_id, exc)
                continue
        waits.append(track_last_summary(visit_id, start_summary(spool)))
    if waits:
        await asyncio.gather(*(asyncio.shield(w) for w in waits), return_exceptions=True)
    async with peer_lock(own_char_uid, peer_uid):
        pass


def _record_block_within_budget(lines: Sequence[Mapping[str, Any]], lang: str | None, budget: int) -> str:
    ordered = sorted(lines, key=line_order_key)
    rendered = [
        f"{get_visit_speaker_header(line['from'], lang)} {line.get('text') or ''}"
        for line in ordered
    ]
    allowance = budget
    while True:
        kept, _ = take_lines_within_token_budget(list(reversed(rendered)), max(allowance, 1))
        chosen = ordered[len(ordered) - len(kept):]
        block = build_visit_record_block([(line["from"], line.get("text") or "") for line in chosen], lang)
        excess = count_tokens(block) - budget
        if excess <= 0:
            return block
        if len(chosen) <= 1:
            if not chosen:
                return block
            # 只剩最新一句、它本身就超过预算：截短这一句，不把超长输入整句送进 LLM
            # （否则每次都超时、每次启动都重试）
            line = chosen[0]
            text = line.get("text") or ""
            room = max(count_tokens(text) - excess - 8, 1)
            return build_visit_record_block([(line["from"], truncate_to_tokens(text, room))], lang)
        # 数据块分隔符与说话人分组的开销不在逐句预算里：按超出量收紧后重取，仍从最新一句往前整句取
        allowance -= excess + 8


async def _retired(config_dir: Path, own_char_uid: str) -> bool:
    try:
        marker = await read_roster_marker(config_dir, "pending_retire")
    except RosterCorruptError:
        return True
    if not isinstance(marker, list):
        return False
    return any(isinstance(item, dict) and item.get("character_uid") == own_char_uid for item in marker)


async def _mark_summary_done(spool: VisitSpool, own_char_uid: str) -> bool:
    # 写 last_summary_done 之前确认这场没被退役：state.json 已删或角色在退役标记里 → 不写、不重建
    if await _retired(spool.config_dir, own_char_uid):
        return False
    try:
        await spool.update_state(last_summary_done=True)
    except FileNotFoundError:
        return False
    return True


async def commit_last_summary(
    spool: VisitSpool,
    roster: PeerRoster | None = None,
    *,
    llm: SummaryLLM,
    resolve_char_name: ResolveCharName,
    family_names: Iterable[str] = (),
) -> bool:
    """Generate and store the last-visit summary of one finished visit (roster only).

    Same timing, gate and lock as :func:`commit_visit_region`. With memory
    off, no transcript, no digestable line or a forgotten peer it stores
    nothing (an older summary stays) and marks ``last_summary_done``.
    Otherwise one LLM call (``VISIT_LAST_SUMMARY_INSTRUCTION``, timeout
    ``VISIT_LLM_TIMEOUT_S``) over a record block of at most
    ``VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS`` taken whole-line from the newest
    line backwards, the peer's lines inside ``VISIT_PEER_LINES_BLOCK``; the
    output goes through ``strip_emotion_tags``, ``redact_outbound`` and
    ``truncate_to_tokens(VISIT_LAST_SUMMARY_MAX_TOKENS)``, and is dropped when
    it copies ``VISIT_PEER_NGRAM_N`` consecutive units of the peer. The text is
    written with :meth:`PeerRoster.set_last_summary` under the character's
    current name (looked up by ``own_char_uid``; a deleted character stores
    nothing and leaves ``state.json`` alone). Nothing goes to memory_server,
    private memory or any session.

    Returns True once the visit is handled (stored or decided not to store),
    False when the LLM failed (recovery retries; ``last_summary_done`` stays
    false).
    """
    state = await spool.read_state()
    if state is None or state["last_summary_done"]:
        return True
    if state["finalized"] is None:
        # 与 commit_visit_region 同一道闸：在飞场次的转录还是半截，现在生成并记 done，
        # 收口时就不会再生成，下一场开场带的是截断的摘要
        return False
    if not is_digestable(state) or state["peer_uid"] is None or state["pair_id"] is None:
        if await _mark_summary_done(spool, state["own_char_uid"]):
            # 「清除这个人」后补完的摘要也可能让这场刚好结清：与正常路径同一处回收转录
            await spool.delete_if_settled()
        return True
    lock = peer_lock(state["own_char_uid"], state["peer_uid"])
    async with lock:
        handled = await _summary_locked(spool, roster, llm=llm, resolve_char_name=resolve_char_name,
                                        family_names=family_names)
    if handled:
        await spool.delete_if_settled()
    return handled


async def _summary_locked(
    spool: VisitSpool,
    roster: PeerRoster | None,
    *,
    llm: SummaryLLM,
    resolve_char_name: ResolveCharName,
    family_names: Iterable[str],
) -> bool:
    state = await spool.read_state()
    if state is None or state["last_summary_done"]:
        return True
    own_char_uid = state["own_char_uid"]
    if state["peer_uid"] is None or state["pair_id"] is None:
        # 等锁期间「清除这个人」已抹掉对端身份：不存
        await _mark_summary_done(spool, own_char_uid)
        return True
    if await memory_bridge.forget_in_progress(spool.config_dir, own_char_uid, state["peer_uid"],
                                              own_uid=state["own_uid"]):
        # 清除未完成：现在写进名册的摘要会在 remove_char 之前出现、之后被一并删掉，
        # 但若 remove_char 已做完，就会把条目里的摘要写回。等清除结束再处理
        return False
    contents = await spool.read_back()
    lines = [line for line in contents.lines if str(line.get("text") or "").strip()]
    if contents.header is None or not lines:
        await _mark_summary_done(spool, own_char_uid)
        return True
    if not _header_matches_state(contents.header, state):
        # 转录头行与 state 指的不是同一场（同账号同对端、别的本地角色……）：不能把这份转录的
        # 摘要存到 state 那个角色名下
        memory_bridge.diag("summary_header_mismatch", visit_id=spool.visit_id)
        return False
    lang = contents.header.get("lang")
    own_char = await resolve_char_name(own_char_uid)
    if not own_char:
        # 角色已删：不写名册、也不写 state.json（退役流程会删掉这场）
        return True
    if roster is None:
        roster = PeerRoster(spool.config_dir, own_uid=state["own_uid"])
    elif roster.own_uid != state["own_uid"]:
        raise ValueError("roster belongs to another community account than this visit")
    try:
        # 调 LLM 之前先按写入时的同一套严格规则读这条名册条目：名册坏着时写入必然抛
        # RosterCorruptError，先付费调用再写失败的话，每次启动都会为每个保留中的场次重复计费
        entry = await roster.get_char_entry(state["peer_uid"], own_char, strict=True)
    except RosterCorruptError as exc:
        memory_bridge.diag("summary_roster_unreadable", visit_id=spool.visit_id, error=str(exc))
        return False
    pairs = entry.get("pairs") if entry is not None else None
    if not isinstance(pairs, list) or state["pair_id"] not in pairs:
        # set_last_summary 只写进已有、且 pairs 含这一对的条目（已完成的清除不能被迟到的摘要撤销）：
        # 现在就写不进去，生成也是白花钱，与写入返回 False 时一样只记 done
        await _mark_summary_done(spool, own_char_uid)
        return True
    existing = entry.get("last_summary")
    if isinstance(existing, dict) and existing.get("visit_id") == spool.visit_id:
        # 上次已把这场的摘要写进名册、只差记 done 就被杀：不再调一次 LLM（会重复计费，
        # 同 ended_at 还会把已提交的摘要换成另一版），只补记 done
        await _mark_summary_done(spool, own_char_uid)
        return True
    block = await asyncio.to_thread(
        _record_block_within_budget, lines, lang, VISIT_LAST_SUMMARY_INPUT_MAX_TOKENS,
    )
    prompt = build_visit_last_summary_prompt(own_char, block, lang)
    try:
        raw = await asyncio.wait_for(llm(prompt), VISIT_LLM_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - 任何失败都只是这次没生成，补录重试
        logger.warning("visit last summary generation failed for %s: %r", spool.visit_id, exc)
        return False
    text = strip_emotion_tags(str(raw or ""))
    text = redact_outbound(text, family_names=list(family_names),
                           replacement=get_family_neutral_term(lang))
    text = (await asyncio.to_thread(truncate_to_tokens, text, VISIT_LAST_SUMMARY_MAX_TOKENS)).strip()
    peer_texts = [str(line["text"]) for line in lines if line["from"] in _PEER_SPEAKERS]
    try:
        # 全转录逐字扫描：对端行可能很多，放到工作线程，不卡事件循环
        await asyncio.to_thread(assert_no_peer_ngram, text, peer_texts, n=VISIT_PEER_NGRAM_N)
    except PeerNgramHit:
        memory_bridge.diag("last_summary_peer_ngram", visit_id=spool.visit_id)
        text = ""
    if text:
        # 按规范顺序的最后一行取时间：墙钟中途被校正时取最大值会拿到某个跳到未来的时间戳，
        # 名册按它排序，之后的场次就再也盖不掉这份旧摘要
        ended_at = float(max(contents.lines, key=line_order_key)["ts"])
        await roster.set_last_summary(
            state["peer_uid"], own_char, visit_id=spool.visit_id, ended_at=ended_at,
            text=text, pair_id=state["pair_id"],
        )
    await _mark_summary_done(spool, own_char_uid)
    return True
