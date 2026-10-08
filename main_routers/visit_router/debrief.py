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

"""Home-coming debrief, runtime side (OD-16 v4, design §3.7.4; the two-step diary write is PR-14).

After the visit is finalized the cat tells the family, in two or three
sentences, who the visit was with and what they talked about:

* input: the in-memory transcript (the upload copy, both sides, ``(lp,
  side_rank)`` order), whatever ``visitMemoryEnabled`` says -- it is only
  said aloud, never stored; at most ``VISIT_DEBRIEF_INPUT_MAX_TOKENS``
  taken whole-line from the newest line backwards, the peer's lines inside
  ``VISIT_PEER_LINES_BLOCK``;
* one isolated-session turn (``VISIT_DEBRIEF_INSTRUCTION``, within
  ``VISIT_CEREMONY_TIMEOUT_S``) → ``strip_emotion_tags`` → ``redact_outbound``
  → token cap → ``assert_no_peer_ngram``; any failure says
  ``VISIT_DEBRIEF_FALLBACK``;
* spoken as a mirror speech registered for the inbox handoff (text only
  with voice off); when the family started talking after the takeover was
  released, it is only shown, after that ordinary turn ended;
* with visit memory on and something to digest: the chips ("keep as diary"
  / "don't keep") and ``state.json{debrief_choice:'ask_later',
  debrief_chip_pending:true}``. Nothing is written to private memory here.

:func:`render_chips` is also the ``render_chips`` callback of PR-08 startup
recovery (PR-09b wires it).
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from config.visit_settings import (
    VISIT_CEREMONY_TIMEOUT_S,
    VISIT_DEBRIEF_DEFAULT,
    VISIT_DEBRIEF_INPUT_MAX_TOKENS,
    VISIT_DEBRIEF_MAX_TOKENS,
    VISIT_PEER_NGRAM_N,
)
from main_logic.visit.sanitize import PeerNgramHit, assert_no_peer_ngram, redact_outbound, strip_emotion_tags
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

DEBRIEF_ACTION = "visit_debrief_choice"
"""``react-chat-window:action`` of the chip buttons (handled by the page, PR-12 / PR-14)."""


def chips_request_id(visit_id: str) -> str:
    return f"visit-debrief:{visit_id}"


def chip_blocks(visit_id: str, lang: Optional[str]) -> list[dict]:
    """The chips message: one line of text and the two buttons (``diary`` / ``forget``)."""
    from config.prompts.prompts_visit import get_visit_debrief_chip_labels

    prompt, diary, forget = get_visit_debrief_chip_labels(lang)
    return [
        {"type": "text", "text": prompt},
        {"type": "buttons", "buttons": [
            {"id": "diary", "label": diary, "action": DEBRIEF_ACTION,
             "payload": {"visit_id": visit_id, "choice": "diary"}},
            {"id": "forget", "label": forget, "action": DEBRIEF_ACTION, "variant": "danger",
             "payload": {"visit_id": visit_id, "choice": "forget"}},
        ]},
    ]


async def show_chips(host: Any, visit_id: str, *, own_char: str, lang: Optional[str]) -> bool:
    """Render the chips through ``host`` (a ``VisitHost``); False when no display took them."""
    try:
        return bool(await host.render_chat_blocks(chip_blocks(visit_id, lang),
                                                  request_id=chips_request_id(visit_id),
                                                  source_name=own_char))
    except Exception as exc:  # noqa: BLE001 - 页面不在：标记留在 state.json，等下次 bind 重放
        logger.info("visit %s: debrief chips not shown: %s", visit_id[:6], type(exc).__name__)
        return False


async def render_chips(visit_id: str, *, own_char: str, status: Optional[str] = None) -> bool:
    """PR-08 recovery callback: show the chips of ``visit_id`` on ``own_char``'s display now.

    ``status='interrupted'`` (a crashed visit) also tells the page the last
    visit was interrupted. The pending flag in ``state.json`` stays until
    the user decides.
    """
    from main_routers.visit_router.host_port import ManagerHost
    from main_routers.visit_router.local_context import prompt_lang

    host = ManagerHost.for_character(own_char)
    if host is None:
        return False
    if status == "interrupted":
        await host.send_status("VISIT_INTERRUPTED_LAST_TIME", {"visit_id": visit_id})
    return await show_chips(host, visit_id, own_char=own_char, lang=prompt_lang())


async def build_debrief_record(lines: list[dict], lang: Optional[str]) -> str:
    """The record block of the summary turn (newest lines first within the token budget)."""
    from main_logic.visit.memory_commit import record_block_within_budget

    if not lines:
        return ""
    return await asyncio.to_thread(record_block_within_budget, lines, lang, VISIT_DEBRIEF_INPUT_MAX_TOKENS)


async def clean_summary(raw: Optional[str], *, family_names: tuple[str, ...], neutral_term: str,
                        peer_lines: list[str]) -> Optional[str]:
    """Summary text safe to say at home, or None (fallback line) when it copies the peer or is empty."""
    from utils.tokenize import atruncate_to_tokens

    text = strip_emotion_tags(str(raw or "")).strip()
    if not text:
        return None
    if family_names:
        text = redact_outbound(text, family_names=family_names, replacement=neutral_term)
    text = (await atruncate_to_tokens(text, VISIT_DEBRIEF_MAX_TOKENS)).strip()
    try:
        assert_no_peer_ngram(text, peer_lines, n=VISIT_PEER_NGRAM_N)
    except PeerNgramHit:
        logger.info("visit debrief: summary copied the peer, using the fixed line")
        return None
    return text or None


async def run_debrief(rt: Any, *, input_stamp: float) -> None:
    """Finalize step 4 for ``rt`` (a ``VisitRuntime``): summary, chips, ``ask_later``."""
    from config.prompts.prompts_visit import build_visit_debrief_prompt, get_visit_debrief_fallback

    lines = rt.journal.lines()
    peer_lines = [str(line.get("text") or "") for line in lines if str(line.get("from")).startswith("peer_")]
    text: Optional[str] = None
    if lines:
        block = await build_debrief_record(lines, rt.lang)
        # 输入只用有预算的整场记录：不再带隔离会话里的近期历史（重复、超预算、对端原文出了数据块）
        raw = await rt.one_shot_turn(build_visit_debrief_prompt(block, rt.lang), timeout=VISIT_CEREMONY_TIMEOUT_S,
                                     without_history=True)
        text = await clean_summary(raw, family_names=tuple(rt.family_names), neutral_term=rt.neutral_term,
                                   peer_lines=peer_lines)
    if not text:
        text = get_visit_debrief_fallback(rt.lang)
    family_spoke = rt.host.last_user_input() > input_stamp
    if family_spoke:
        # 亲人在仪式句 / 简述生成中先开口：简述与芯片排到那一轮 turn end 之后、只上屏不出声
        await rt.wait_family_turn()
        if rt.handoff is not None:
            rt.handoff.skip("debrief")
        try:
            await rt.host.mirror_assistant_output(text, metadata=rt._mirror_meta("visit_debrief"),
                                                  request_id=f"visit-debrief-summary:{rt.visit_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("visit %s: debrief summary not shown: %s", rt.visit_id[:6], type(exc).__name__)
    else:
        await rt.speak_home_segment("debrief", text, kind="visit_debrief")
    await _push_state(rt, "summary")
    # 日记读的是 spool：有没有可记的句子按 spool 实际写进去的算（与上传流水各自独立）
    await _offer_chips(rt, has_lines=rt.spool_lines > 0)


async def _push_state(rt: Any, phase: str) -> None:
    """``visit_debrief{phase}`` status frame (§4.5; the chips themselves are ``chat_blocks``)."""
    await rt.host.send_frame({"type": "visit_debrief", "visit_id": rt.visit_id, "phase": phase,
                              "request_id": chips_request_id(rt.visit_id), "ts": rt.wall()})


async def _offer_chips(rt: Any, *, has_lines: bool) -> None:
    spool = rt.spool
    if not rt.memory_enabled or spool is None or not has_lines:
        return
    try:
        await spool.update_state(debrief_choice=VISIT_DEBRIEF_DEFAULT, debrief_chip_pending=True)
    except Exception as exc:  # noqa: BLE001 - 标记写不进：芯片照常出，下次启动由补录兜底
        logger.warning("visit %s: debrief state not written: %s", rt.visit_id[:6], type(exc).__name__)
    await show_chips(rt.host, rt.visit_id, own_char=rt.lanlan_name, lang=rt.lang)
    await _push_state(rt, "asked")
