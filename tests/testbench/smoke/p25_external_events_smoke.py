"""P25 external-events TestClient smoke (PLAN §P25 Day 1).

Exercises the three handlers in
``tests.testbench.pipeline.external_events`` end-to-end through the
HTTP boundary:

    POST /api/session/external-event
    GET  /api/session/external-event/dedupe-info
    POST /api/session/external-event/dedupe-reset

Environment isolation: DATA_DIR / SANDBOXES_DIR / LOGS_DIR are all
redirected to ``tempfile.mkdtemp`` before any testbench import so the
repo's ``tests/testbench_data/`` tree stays untouched.

LLM isolation: we monkeypatch
``tests.testbench.pipeline.external_events._invoke_llm_once`` with an
async stub (never hitting the network). The stub is reassignable so a
single case can flip it to return ``"[PASS]"`` or raise
``RuntimeError`` on demand.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p25_external_events_smoke.py

Exits 0 on success (prints ``P25 EXTERNAL EVENTS SMOKE OK``), non-zero
on any assertion failure. Every failing case prints a clearly labeled
line identifying which sub-case tripped.

Matrix (strictly per the Day-1 subagent-C spec):
- A1 / A2 / A3  — avatar / agent_callback / proactive happy path.
- B1..B5         — avatar dedupe matrix (hit, rank upgrade, distinct
                   keys, reset, info shape, cap-overflow LRU +
                   AVATAR_DEDUPE_CACHE_FULL once-per-cycle).
- C1..C4         — coerce_info surfacing (intensity, kind, language
                   fallback + zh-TW stays native).
- D1..D3         — mirror_to_recent applied / fallback_reason paths
                   (incl. proactive [PASS]).
- E1..E7         — error branches (invalid_payload / empty_callbacks /
                   InvalidKind 400 / NoActiveSession 404 x3 /
                   llm_failed + dedupe rollback).
- F1 / F2        — append_message choke-point invariants (non-decreasing
                   timestamps on success; messages unchanged on failure).
- G1 / G2        — diagnostics op records (right op_type, right detail
                   fields).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Environment isolation — must run BEFORE any testbench import.
# ─────────────────────────────────────────────────────────────


def _setup_env() -> Path:
    """Redirect DATA_DIR + SANDBOXES_DIR to a temp root."""
    tmp_data = Path(tempfile.mkdtemp(prefix="p25_extev_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    for d in [
        tb_config.SAVED_SESSIONS_DIR,
        tb_config.AUTOSAVE_DIR,
        tb_config.LOGS_DIR,
        tb_config.SANDBOXES_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return tmp_data


# ─────────────────────────────────────────────────────────────
# Shared fixture: TestClient + live session + LLM monkeypatch
# ─────────────────────────────────────────────────────────────


class _MockLLM:
    """Swappable async stub for ``external_events._invoke_llm_once``."""

    def __init__(self) -> None:
        self.calls: int = 0
        self.last_wire: list[dict[str, Any]] | None = None
        self._reply: str = "Mocked reply"
        self._exc: BaseException | None = None

    def set_reply(self, text: str) -> None:
        self._reply = text
        self._exc = None

    def set_raise(self, exc: BaseException) -> None:
        self._exc = exc

    def reset(self) -> None:
        self._reply = "Mocked reply"
        self._exc = None
        self.calls = 0
        self.last_wire = None

    async def __call__(
        self, session: Any, wire_messages: list[dict[str, Any]],
    ) -> str:
        self.calls += 1
        self.last_wire = wire_messages
        if self._exc is not None:
            raise self._exc
        return self._reply


def _install_llm_mock():
    """Patch ``_invoke_llm_once`` and return the ``_MockLLM`` instance."""
    from tests.testbench.pipeline import external_events as ee
    mock = _MockLLM()
    ee._invoke_llm_once = mock  # type: ignore[assignment]
    return mock


def _set_default_persona(client) -> None:
    """Populate the active session's persona so ``build_prompt_bundle``
    has what it needs (``character_name`` is the hard requirement).
    """
    r = client.put("/api/persona", json={
        "character_name": "NEKO",
        "master_name": "Master",
        "language": "zh-CN",
        "system_prompt": "You are {LANLAN_NAME}. You address the user as {MASTER_NAME}.",
    })
    assert r.status_code == 200, f"persona PUT failed: {r.status_code} {r.text}"


def _set_model_config() -> None:
    """Give the session a chat model config — not strictly needed because
    we bypass ``resolve_group_config`` via the LLM mock, but set it so any
    incidental path that reads ``session.model_config`` sees sane values.
    """
    from tests.testbench.session_store import get_session_store
    session = get_session_store().require()
    session.model_config = {
        "chat": {
            "api_key": "sk-FAKE",
            "model": "gpt-4o",
            "base_url": "http://localhost:1",
        },
        "judge": {"api_key": "", "model": "gpt-4o"},
    }


def _create_session(client, name: str = "p25_extev") -> None:
    """Create (or replace) the active session."""
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"
    _set_default_persona(client)
    _set_model_config()


def _delete_session(client) -> None:
    """Best-effort teardown of the active session between checks."""
    try:
        client.delete("/api/session")
    except Exception:
        pass


def _reset_dedupe(client) -> int:
    """Clear the avatar dedupe cache and return the cleared count."""
    r = client.post("/api/session/external-event/dedupe-reset")
    assert r.status_code == 200, f"dedupe-reset failed: {r.status_code} {r.text}"
    return int(r.json().get("cleared", 0))


# ─────────────────────────────────────────────────────────────
# Assertion helpers
# ─────────────────────────────────────────────────────────────


class AssertionFailed(Exception):
    """Raised by ``_check`` so we can surface a clean one-line label."""


def _check(cond: bool, label: str, msg: str = "") -> None:
    if not cond:
        detail = f" — {msg}" if msg else ""
        raise AssertionFailed(f"[{label}] {detail.strip(' —')}")


def _post_event(
    client, kind: str, payload: dict[str, Any], *,
    mirror_to_recent: bool = False,
) -> tuple[int, dict[str, Any]]:
    r = client.post(
        "/api/session/external-event",
        json={
            "kind": kind,
            "payload": payload,
            "mirror_to_recent": mirror_to_recent,
        },
    )
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"_raw": r.text}


def _extract_result(body: dict[str, Any]) -> dict[str, Any]:
    """Return the SimulationResult fields from the POST response body.

    Wire shape (router post-2026-04-23, per P25_BLUEPRINT §2.6 "一个响应
    结构"): SimulationResult fields are flat at the top level alongside a
    sibling ``kind`` discriminator — ``{kind, accepted, reason, ...}``.
    Previously the router wrapped them inside an ``{kind, result: {...}}``
    envelope, which shape-drifted against the P25 Day 2 UI (the panel's
    ``state.lastResult = resp.data`` expected flat). We flipped the
    router and the smoke together to eliminate the envelope.
    """
    if not isinstance(body, dict):
        return {}
    return body


def _latest_diag(client, op_type: str) -> dict[str, Any] | None:
    """Return the most recent diagnostics entry with this op_type, or None.

    Passes ``include_info=true`` because the P25 simulated-event records
    (``avatar_interaction_simulated`` / ``agent_callback_simulated`` /
    ``proactive_simulated``) are written at level=info for audit replay.
    As of P25 hotfix 2026-04-23, ``/api/diagnostics/errors`` default-
    hides info-level entries; the UI exposes a checkbox to opt-in, and
    this smoke is that opt-in from the backend side.
    """
    r = client.get(
        "/api/diagnostics/errors",
        params={"op_type": op_type, "limit": 10, "include_info": "true"},
    )
    if r.status_code != 200:
        return None
    items = r.json().get("items") or []
    return items[0] if items else None


def _avatar_payload(
    *, interaction_id: str, tool_id: str = "fist",
    action_id: str = "poke", intensity: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimum-valid avatar event payload.

    Uses ``fist/poke`` by default because the spec line A1 tool_id='head',
    action_id='touch' is not in the main program's allowed set
    (_AVATAR_INTERACTION_ALLOWED_ACTIONS = {lollipop, fist, hammer}).
    The subagent-C brief says to test the "three kind happy path" — so
    we pick a combination the normaliser actually accepts.
    """
    p: dict[str, Any] = {
        "interaction_id": interaction_id,
        "tool_id": tool_id,
        "action_id": action_id,
        "target": "avatar",
    }
    if intensity is not None:
        p["intensity"] = intensity
    if extra:
        p.update(extra)
    return p


# ─────────────────────────────────────────────────────────────
# Case A — three kinds happy path
# ─────────────────────────────────────────────────────────────


def check_a_happy_paths(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_A")
        mock.reset()
        mock.set_reply("Mocked reply")

        # A1 — avatar.
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="i1"),
        )
        _check(status == 200, "A1.status", f"expected 200, got {status}: {body}")
        result = _extract_result(body)
        _check(result.get("accepted") is True, "A1.accepted",
               f"expected accepted=True, got {result.get('accepted')}; reason={result.get('reason')}")
        _check(result.get("persisted") is True, "A1.persisted", str(result.get("persisted")))
        _check(bool(result.get("instruction")), "A1.instruction_nonempty",
               f"instruction was empty; len={len(result.get('instruction') or '')}")
        _check(result.get("assistant_reply") == "Mocked reply", "A1.reply",
               f"assistant_reply={result.get('assistant_reply')!r}")
        memory_pair = result.get("memory_pair") or []
        _check(len(memory_pair) == 2, "A1.memory_pair_len",
               f"expected 2 (user memory_note + assistant), got {len(memory_pair)}")
        if len(memory_pair) == 2:
            _check(memory_pair[0].get("role") == "user", "A1.memory_pair_user_role",
                   f"first msg role={memory_pair[0].get('role')}")
            _check(memory_pair[1].get("role") == "assistant", "A1.memory_pair_asst_role",
                   f"second msg role={memory_pair[1].get('role')}")
        dedupe_info = result.get("dedupe_info") or {}
        _check(dedupe_info.get("hit") is False, "A1.dedupe_hit",
               f"dedupe_info.hit={dedupe_info.get('hit')}")

        # A1c — wire instruction role (P25 Day 2 polish 2):
        # 主程序 prompt_ephemeral 的语义契约是 ``messages + HumanMessage(
        # content=instruction)`` — instruction 以 **user** 角色注入. 如果
        # 误写成 role=system, 会让空 session 的 wire 只有 [system, system],
        # Gemini 400 "Model input cannot be empty". 这条 check 守住契约.
        wire = mock.last_wire or []
        _check(bool(wire), "A1c.wire_nonempty",
               f"mock.last_wire empty after avatar call: {wire!r}")
        if wire:
            _check(wire[-1].get("role") == "user", "A1c.instruction_role_user",
                   f"instruction role in wire tail = {wire[-1].get('role')!r}, "
                   f"expected 'user' (L36 第三轮: 违反主程序 prompt_ephemeral "
                   f"契约 → Gemini 空消息 400)")
            _check(wire[-1].get("content") == result.get("instruction"),
                   "A1c.wire_tail_is_instruction",
                   f"wire tail content != SimulationResult.instruction")

        # A2 — agent_callback.
        mock.set_reply("Mocked reply")
        status, body = _post_event(
            client, "agent_callback",
            {"callbacks": ["任务已完成", "2 条邮件"]},
        )
        _check(status == 200, "A2.status", f"expected 200, got {status}: {body}")
        r2 = _extract_result(body)
        _check(r2.get("accepted") is True, "A2.accepted",
               f"reason={r2.get('reason')}")
        pair2 = r2.get("memory_pair") or []
        _check(len(pair2) == 1, "A2.memory_pair_len",
               f"expected 1 assistant-only entry, got {len(pair2)}")
        if pair2:
            _check(pair2[0].get("role") == "assistant", "A2.memory_pair_role",
                   f"role={pair2[0].get('role')}")
        instr2 = r2.get("instruction") or ""
        _check("\n" in instr2, "A2.instruction_newline",
               "instruction missing newline (expected multi-line bullet list)")
        _check("- 任务已完成" in instr2 and "- 2 条邮件" in instr2,
               "A2.instruction_bullets",
               f"instruction lacks '- <item>' bullets: {instr2[:200]!r}")
        # A2c — wire instruction role (see A1c for rationale).
        wire2 = mock.last_wire or []
        if wire2:
            _check(wire2[-1].get("role") == "user", "A2c.instruction_role_user",
                   f"agent_callback instruction role = {wire2[-1].get('role')!r}, "
                   f"expected 'user'")

        # A3 — proactive.
        mock.set_reply("Mocked reply")
        status, body = _post_event(client, "proactive", {"kind": "home"})
        _check(status == 200, "A3.status", f"expected 200, got {status}: {body}")
        r3 = _extract_result(body)
        _check(r3.get("accepted") is True, "A3.accepted",
               f"reason={r3.get('reason')}")
        pair3 = r3.get("memory_pair") or []
        _check(len(pair3) == 1, "A3.memory_pair_len",
               f"expected 1, got {len(pair3)}")
        _check(r3.get("assistant_reply") == "Mocked reply", "A3.reply",
               f"reply={r3.get('assistant_reply')!r}")
        # A3c — wire instruction role (see A1c for rationale).
        wire3 = mock.last_wire or []
        if wire3:
            _check(wire3[-1].get("role") == "user", "A3c.instruction_role_user",
                   f"proactive instruction role = {wire3[-1].get('role')!r}, "
                   f"expected 'user'")

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[A.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Case B — avatar dedupe matrix
# ─────────────────────────────────────────────────────────────


def check_b_dedupe_matrix(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_B")
        mock.reset()
        mock.set_reply("Mocked reply")

        # B1 — same tool_id/action_id within 8s window → second rejects.
        p1 = _avatar_payload(interaction_id="b1-first",
                             tool_id="fist", action_id="poke")
        status, body = _post_event(client, "avatar", p1)
        _check(status == 200, "B1.setup_status",
               f"first post failed: {status} {body}")
        r = _extract_result(body)
        _check(r.get("accepted") is True, "B1.first_accepted",
               f"first should accept; reason={r.get('reason')}")

        p2 = _avatar_payload(interaction_id="b1-second",
                             tool_id="fist", action_id="poke")
        status, body = _post_event(client, "avatar", p2)
        _check(status == 200, "B1.second_status", str(status))
        r = _extract_result(body)
        _check(r.get("accepted") is False, "B1.second_accepted",
               f"expected False; got accepted={r.get('accepted')} reason={r.get('reason')}")
        _check(r.get("reason") == "dedupe_window_hit", "B1.second_reason",
               f"reason={r.get('reason')}")
        di = r.get("dedupe_info") or {}
        _check(di.get("hit") is True, "B1.dedupe_hit",
               f"dedupe_info={di}")

        # B2 — rank upgrade path.
        #
        # Strict three-step matrix:
        #   fist/poke  intensity=normal → rank=1 → accepted
        #   fist/poke  intensity=rapid  → rank=2 → accepted (upgrade)
        #   fist/poke  intensity=rapid  → rank=2 → rejected (short-circuit)
        # This exercises the copy-protected helper's "previous.rank vs now.rank"
        # branch inside the dedupe window, not just the wall-clock window.
        _reset_dedupe(client)
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b2-r1",
                            tool_id="fist", action_id="poke",
                            intensity="normal"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "B2.first_accepted",
               f"reason={r.get('reason')}")

        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b2-r2",
                            tool_id="fist", action_id="poke",
                            intensity="rapid"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "B2.rank_upgrade_accepted",
               f"expected rank=2 to override rank=1 within the 8s window; "
               f"got accepted={r.get('accepted')} reason={r.get('reason')}")

        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b2-r3",
                            tool_id="fist", action_id="poke",
                            intensity="rapid"),
        )
        r3 = _extract_result(body)
        _check(r3.get("accepted") is False, "B2.third_short_circuit",
               f"expected short-circuit reject after rank upgrade; "
               f"got accepted={r3.get('accepted')} reason={r3.get('reason')}")
        _check(r3.get("reason") == "dedupe_window_hit", "B2.third_reason",
               f"reason={r3.get('reason')}")

        # B3 — distinct dedupe keys don't collide.
        _reset_dedupe(client)
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b3-fist",
                            tool_id="fist", action_id="poke"),
        )
        _check(_extract_result(body).get("accepted") is True,
               "B3.fist_accepted", str(body))
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b3-lollipop",
                            tool_id="lollipop", action_id="offer"),
        )
        _check(_extract_result(body).get("accepted") is True,
               "B3.lollipop_accepted", str(body))
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b3-hammer",
                            tool_id="hammer", action_id="bonk"),
        )
        _check(_extract_result(body).get("accepted") is True,
               "B3.hammer_accepted", str(body))

        # B4 — reset returns cleared count + re-post succeeds.
        info = client.get("/api/session/external-event/dedupe-info").json()
        size_before = int((info.get("info") or {}).get("size") or 0)
        r = client.post("/api/session/external-event/dedupe-reset")
        _check(r.status_code == 200, "B4.reset_status", str(r.status_code))
        cleared = int(r.json().get("cleared") or 0)
        _check(cleared == size_before, "B4.reset_cleared_count",
               f"cleared={cleared} vs size_before={size_before}")
        # Re-post what would've been dedupe'd pre-reset → now accepts.
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b4-after-reset",
                            tool_id="fist", action_id="poke"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "B4.after_reset_accepted",
               f"accepted={r.get('accepted')} reason={r.get('reason')}")

        # B5 — GET dedupe-info shape.
        _reset_dedupe(client)
        # Seed exactly one entry so ``entries`` is non-empty.
        _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="b5-seed",
                            tool_id="fist", action_id="poke"),
        )
        r = client.get("/api/session/external-event/dedupe-info")
        _check(r.status_code == 200, "B5.status", str(r.status_code))
        body = r.json()
        info = body.get("info") or {}
        _check("size" in info and "max_entries" in info and "entries" in info,
               "B5.shape", f"keys={sorted(info.keys())}")
        _check(int(info.get("size") or 0) == 1, "B5.size",
               f"size={info.get('size')}")
        _check(int(info.get("max_entries") or 0) == 100, "B5.max_entries",
               f"max_entries={info.get('max_entries')} "
               f"(_AvatarDedupeCache._MAX_ENTRIES should be 100)")
        _check(isinstance(info.get("entries"), dict), "B5.entries_type",
               f"type={type(info.get('entries')).__name__}")

        # B5b — cap overflow matrix with a lowered soft cap.
        _reset_dedupe(client)
        # Clear diagnostics so we can cleanly count cache-full notices.
        client.delete("/api/diagnostics/errors")

        from tests.testbench.pipeline.avatar_dedupe import _AvatarDedupeCache
        original_cap = _AvatarDedupeCache._MAX_ENTRIES
        try:
            # Lower the soft cap to 2 so three distinct avatar events
            # (one per allowed tool: lollipop / fist / hammer) overflow
            # by exactly one entry. Pushing a 4th must NOT re-fire the
            # notice within the same cycle (warn-once latch).
            #
            # The three tools map to three distinct cache keys set by
            # ``_build_avatar_interaction_memory_meta``:
            # ``lollipop_feed`` / ``fist_touch`` / ``hammer_bonk``.
            _AvatarDedupeCache._MAX_ENTRIES = 2
            seeds = [
                ("lollipop", "offer", None),
                ("fist", "poke", None),
                ("hammer", "bonk", None),
            ]
            for idx, (tool, action, intensity) in enumerate(seeds):
                status, body = _post_event(
                    client, "avatar",
                    _avatar_payload(
                        interaction_id=f"b5b-{idx}",
                        tool_id=tool, action_id=action,
                        intensity=intensity,
                    ),
                )
                _check(status == 200, f"B5b.seed{idx}.status", str(status))
                _check(_extract_result(body).get("accepted") is True,
                       f"B5b.seed{idx}.accepted",
                       f"tool={tool} action={action} body={body}")

            # (i) LRU drops the oldest entry — cache size stays at cap (2).
            info = client.get(
                "/api/session/external-event/dedupe-info",
            ).json().get("info") or {}
            _check(int(info.get("size") or 0) == 2,
                   "B5b.lru_cap", f"size={info.get('size')}")
            entries = info.get("entries") or {}
            _check(isinstance(entries, dict) and len(entries) == 2,
                   "B5b.entries_len", f"len={len(entries)}")
            # The very first pushed key (``lollipop_feed``) should have been
            # evicted by the time the 3rd distinct key went in.
            _check("lollipop_feed" not in entries, "B5b.lru_oldest_evicted",
                   f"oldest key 'lollipop_feed' should be LRU-evicted; entries keys={sorted(entries.keys())}")

            # (ii) AVATAR_DEDUPE_CACHE_FULL diagnostics entry appears
            # exactly once per fill cycle.
            from tests.testbench.pipeline.diagnostics_ops import DiagnosticsOp
            op = DiagnosticsOp.AVATAR_DEDUPE_CACHE_FULL.value
            r = client.get(
                "/api/diagnostics/errors",
                params={"op_type": op, "limit": 20},
            )
            _check(r.status_code == 200, "B5b.cache_full_status", str(r.status_code))
            items = r.json().get("items") or []
            _check(len(items) == 1, "B5b.cache_full_once",
                   f"expected exactly 1 AVATAR_DEDUPE_CACHE_FULL entry "
                   f"in this fill cycle; got {len(items)}")

            # (iii) After reset the warn-once latch rearms — a second
            # overflow cycle fires a fresh notice.
            _reset_dedupe(client)
            for idx, (tool, action, intensity) in enumerate(seeds):
                _post_event(
                    client, "avatar",
                    _avatar_payload(
                        interaction_id=f"b5b-cycle2-{idx}",
                        tool_id=tool, action_id=action,
                        intensity=intensity,
                    ),
                )
            r = client.get(
                "/api/diagnostics/errors",
                params={"op_type": op, "limit": 20},
            )
            items2 = r.json().get("items") or []
            _check(len(items2) == 2, "B5b.cache_full_rearm",
                   f"after reset + fresh overflow expected total=2 "
                   f"AVATAR_DEDUPE_CACHE_FULL entries; got {len(items2)}")
        finally:
            _AvatarDedupeCache._MAX_ENTRIES = original_cap

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[B.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Case C — coerce surfacing
# ─────────────────────────────────────────────────────────────


def check_c_coerce(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_C")
        mock.reset()
        mock.set_reply("Mocked reply")

        # C1 — illegal avatar intensity is normalised, and the diff is surfaced.
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="c1-bad-intensity",
                            tool_id="fist", action_id="poke",
                            intensity="crazy"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "C1.accepted",
               f"reason={r.get('reason')}")
        coerce_list = r.get("coerce_info") or []
        intensity_entries = [c for c in coerce_list if c.get("field") == "intensity"]
        _check(len(intensity_entries) >= 1, "C1.coerce_present",
               f"coerce_info={coerce_list}")
        if intensity_entries:
            c = intensity_entries[0]
            _check(c.get("requested") == "crazy", "C1.coerce_requested",
                   f"requested={c.get('requested')}")
            _check(c.get("applied") in {"normal", "rapid", "burst", "easter_egg"},
                   "C1.coerce_applied",
                   f"applied={c.get('applied')}")

        # C2 — bogus proactive kind coerces to home.
        _reset_dedupe(client)
        status, body = _post_event(client, "proactive", {"kind": "totally_bogus"})
        r = _extract_result(body)
        _check(r.get("accepted") is True, "C2.accepted",
               f"reason={r.get('reason')}")
        coerce_list = r.get("coerce_info") or []
        kind_entries = [c for c in coerce_list if c.get("field") == "kind"]
        _check(len(kind_entries) == 1, "C2.coerce_kind",
               f"coerce_info={coerce_list}")
        c = kind_entries[0]
        _check(c.get("requested") == "totally_bogus", "C2.requested",
               f"requested={c.get('requested')}")
        _check(c.get("applied") == "home", "C2.applied",
               f"applied={c.get('applied')}")

        # C3 — proactive language fallback surfaces.
        r = client.patch("/api/persona", json={"language": "es-ES"})
        _check(r.status_code == 200, "C3.persona_patch_status",
               f"{r.status_code} {r.text}")
        status, body = _post_event(client, "proactive", {"kind": "home"})
        r3 = _extract_result(body)
        _check(r3.get("accepted") is True, "C3.accepted",
               f"reason={r3.get('reason')}")
        coerce_list = r3.get("coerce_info") or []
        lang_entries = [c for c in coerce_list if c.get("field") == "language"]
        _check(len(lang_entries) == 1, "C3.coerce_language",
               f"coerce_info={coerce_list}")
        c = lang_entries[0]
        _check(c.get("requested") == "es-ES", "C3.lang_requested",
               f"requested={c.get('requested')}")
        _check(c.get("applied") == "en", "C3.lang_applied",
               f"applied={c.get('applied')}")

        # C4 — zh-TW is a native zh prefix; must NOT surface language coerce.
        r = client.patch("/api/persona", json={"language": "zh-TW"})
        _check(r.status_code == 200, "C4.persona_patch_status",
               f"{r.status_code} {r.text}")
        status, body = _post_event(client, "proactive", {"kind": "home"})
        r4 = _extract_result(body)
        _check(r4.get("accepted") is True, "C4.accepted",
               f"reason={r4.get('reason')}")
        coerce_list = r4.get("coerce_info") or []
        lang_entries = [c for c in coerce_list if c.get("field") == "language"]
        _check(len(lang_entries) == 0, "C4.no_language_coerce",
               f"unexpected language coerce for zh-TW: {lang_entries}")

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[C.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Case D — mirror_to_recent behaviour
# ─────────────────────────────────────────────────────────────


def _recent_json_path(character_name: str) -> Path:
    """Resolve ``memory/<character>/recent.json`` via ConfigManager."""
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character_name / "recent.json"


def check_d_mirror(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_D")
        mock.reset()
        mock.set_reply("Mocked reply")

        # D1 — mirror_to_recent: true + character_name present → applied.
        recent_path = _recent_json_path("NEKO")
        # Start clean: make sure the file doesn't exist (sandbox is fresh
        # per session, so this is purely defensive).
        if recent_path.exists():
            try:
                recent_path.unlink()
            except OSError:
                pass

        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="d1",
                            tool_id="fist", action_id="poke"),
            mirror_to_recent=True,
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "D1.accepted",
               f"reason={r.get('reason')}")
        mi = r.get("mirror_to_recent_info") or {}
        _check(mi.get("requested") is True, "D1.requested",
               f"mirror_info={mi}")
        _check(mi.get("applied") is True, "D1.applied",
               f"mirror_info={mi}")
        _check(mi.get("fallback_reason") in (None, ""), "D1.no_fallback",
               f"fallback_reason={mi.get('fallback_reason')}")
        _check(recent_path.exists(), "D1.recent_written",
               f"recent.json missing at {recent_path}")
        if recent_path.exists():
            with recent_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            _check(isinstance(data, list), "D1.recent_shape",
                   f"top-level type={type(data).__name__}")
            _check(len(data) == 2, "D1.recent_len",
                   f"expected 2 rows (user + assistant), got {len(data)}")

            # P25 Day 1 fixup (2026-04-23): recent.json entries MUST be
            # written in the main-program canonical LangChain shape
            # ``{"type": "human"|"ai"|"system", "data": {"content": str}}``,
            # NOT the testbench-internal shape ``{"role", "content":
            # [{"type": "text", "text": ...}]}``. The latter was the
            # original Day 1 impl, caught post-commit by manual curl
            # verification: downstream ``memory_runner._preview_recent_
            # compress`` / ``_preview_facts_extract`` both round-trip
            # via ``messages_from_dict(_read_json_list(recent_path))``
            # (``memory_runner.py`` L456 + L621), and the main program's
            # ``memory/recent.py`` uses the same deserializer. Writing
            # testbench-internal shape would silently fall through
            # ``messages_from_dict`` (``utils/llm_client.py`` L113-114)
            # into ``HumanMessage(content=str(d))``, stringifying the
            # whole dict — corrupting both sides' understanding of the
            # file. The fix threads mirror writes through
            # ``messages_to_dict([HumanMessage|AIMessage|SystemMessage])``
            # in ``external_events._apply_mirror_to_recent``.
            _check(
                all(isinstance(e, dict) and "type" in e and "data" in e
                    and isinstance(e["data"], dict) and "content" in e["data"]
                    for e in data),
                "D1.recent_langchain_shape",
                "every row must be {type, data:{content}} (canonical "
                "LangChain ``messages_to_dict`` shape). Found row "
                f"keys: {[sorted(e.keys()) if isinstance(e, dict) else type(e).__name__ for e in data]}",
            )
            _check(
                data[0]["type"] == "human" and data[1]["type"] == "ai",
                "D1.recent_role_pair",
                f"avatar pair must map user→human + assistant→ai; got "
                f"types=[{data[0].get('type')}, {data[1].get('type')}]",
            )
            # And the round-trip must survive ``messages_from_dict`` —
            # i.e. ``content`` comes back as a plain string, not a
            # stringified dict (the silent-failure signature).
            from utils.llm_client import messages_from_dict
            roundtripped = messages_from_dict(data)
            _check(
                len(roundtripped) == 2,
                "D1.recent_roundtrip_len",
                f"messages_from_dict returned {len(roundtripped)} msgs",
            )
            _check(
                all(isinstance(m.content, str) and not m.content.startswith("{")
                    for m in roundtripped),
                "D1.recent_roundtrip_content",
                f"content field must round-trip as plain str, got: "
                f"{[(type(m.content).__name__, str(m.content)[:40]) for m in roundtripped]}",
            )

        # D2 — mirror_to_recent: true + persona.character_name="" → fallback.
        r = client.patch("/api/persona", json={"character_name": ""})
        _check(r.status_code == 200, "D2.persona_patch_status",
               f"{r.status_code} {r.text}")
        # build_prompt_bundle now raises PreviewNotReady because
        # character_name is required there too; the avatar handler would
        # hit ``persona_not_ready`` before the mirror step. To keep D2
        # purely about the mirror's character_name fallback path, we
        # exercise ``_apply_mirror_to_recent`` directly — its contract is
        # "requested=True + empty character_name → applied=False,
        # fallback_reason mentions character_name".
        from tests.testbench.pipeline.external_events import _apply_mirror_to_recent
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        mi2 = _apply_mirror_to_recent(
            session,
            [{"role": "user", "content": "hello"}],
            requested=True,
        )
        _check(mi2.requested is True, "D2.requested", f"requested={mi2.requested}")
        _check(mi2.applied is False, "D2.applied", f"applied={mi2.applied}")
        _check(isinstance(mi2.fallback_reason, str) and "character_name" in mi2.fallback_reason,
               "D2.fallback_reason_text",
               f"fallback_reason={mi2.fallback_reason!r}")

        # D3 — proactive + mirror_to_recent + LLM returns [PASS]
        # → accepted=False, reason=pass_signaled, mirror applied=False.
        r = client.patch("/api/persona", json={"character_name": "NEKO"})
        _check(r.status_code == 200, "D3.persona_restore_status",
               f"{r.status_code} {r.text}")
        mock.set_reply("[PASS]")
        status, body = _post_event(
            client, "proactive",
            {"kind": "home"},
            mirror_to_recent=True,
        )
        r3 = _extract_result(body)
        _check(r3.get("accepted") is False, "D3.accepted",
               f"accepted={r3.get('accepted')}")
        _check(r3.get("reason") == "pass_signaled", "D3.reason",
               f"reason={r3.get('reason')}")
        mi3 = r3.get("mirror_to_recent_info") or {}
        _check(mi3.get("requested") is True, "D3.requested",
               f"mirror={mi3}")
        _check(mi3.get("applied") is False, "D3.applied",
               f"mirror={mi3}")
        fallback_reason = mi3.get("fallback_reason") or ""
        _check("[PASS]" in fallback_reason or "PASS" in fallback_reason,
               "D3.fallback_mentions_pass",
               f"fallback_reason={fallback_reason!r}")

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[D.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        mock.reset()
        mock.set_reply("Mocked reply")
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Case E — error branches
# ─────────────────────────────────────────────────────────────


def check_e_errors(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_E")
        mock.reset()
        mock.set_reply("Mocked reply")

        # E1 — invalid avatar payload.
        status, body = _post_event(client, "avatar", {})
        _check(status == 200, "E1.status", f"expected 200, got {status}: {body}")
        r = _extract_result(body)
        _check(r.get("accepted") is False, "E1.accepted",
               f"accepted={r.get('accepted')}")
        _check(r.get("reason") == "invalid_payload", "E1.reason",
               f"reason={r.get('reason')}")

        # E2 — empty callbacks list.
        status, body = _post_event(client, "agent_callback", {"callbacks": []})
        _check(status == 200, "E2.status", f"expected 200, got {status}: {body}")
        r = _extract_result(body)
        _check(r.get("accepted") is False, "E2.accepted",
               f"accepted={r.get('accepted')}")
        _check(r.get("reason") == "empty_callbacks", "E2.reason",
               f"reason={r.get('reason')}")

        # E3 — bogus kind → 400 InvalidKind.
        r = client.post(
            "/api/session/external-event",
            json={"kind": "bogus", "payload": {}},
        )
        _check(r.status_code == 400, "E3.status", f"{r.status_code} {r.text}")
        detail = (r.json() or {}).get("detail") or {}
        _check(detail.get("error_type") == "InvalidKind",
               "E3.error_type", f"detail={detail}")
    except AssertionFailed as exc:
        errors.append(str(exc))
    finally:
        _delete_session(client)

    # E4 / E5 / E6 — no active session.
    try:
        # Paranoia: make sure no session is attached.
        _delete_session(client)

        r = client.post(
            "/api/session/external-event",
            json={"kind": "avatar", "payload": _avatar_payload(interaction_id="e4")},
        )
        _check(r.status_code == 404, "E4.status", f"{r.status_code} {r.text}")
        detail = (r.json() or {}).get("detail") or {}
        _check(detail.get("error_type") == "NoActiveSession",
               "E4.error_type", f"detail={detail}")

        r = client.get("/api/session/external-event/dedupe-info")
        _check(r.status_code == 404, "E5.status", f"{r.status_code} {r.text}")
        detail = (r.json() or {}).get("detail") or {}
        _check(detail.get("error_type") == "NoActiveSession",
               "E5.error_type", f"detail={detail}")

        r = client.post("/api/session/external-event/dedupe-reset")
        _check(r.status_code == 404, "E6.status", f"{r.status_code} {r.text}")
        detail = (r.json() or {}).get("detail") or {}
        _check(detail.get("error_type") == "NoActiveSession",
               "E6.error_type", f"detail={detail}")
    except AssertionFailed as exc:
        errors.append(str(exc))

    # E7 — LLM failure + dedupe rollback.
    try:
        _create_session(client, "p25_E7")
        mock.reset()
        mock.set_raise(RuntimeError("synthetic LLM failure"))

        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="e7-first",
                            tool_id="fist", action_id="poke"),
        )
        _check(status == 200, "E7.first_status", str(status))
        r = _extract_result(body)
        _check(r.get("accepted") is False, "E7.first_accepted",
               f"accepted={r.get('accepted')}")
        _check(r.get("reason") == "llm_failed", "E7.first_reason",
               f"reason={r.get('reason')}")

        # Restore a working mock. Dedupe rollback should let the retry
        # succeed immediately (otherwise the 8s dedupe window would bite).
        mock.set_reply("Mocked reply after recovery")
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="e7-retry",
                            tool_id="fist", action_id="poke"),
        )
        _check(status == 200, "E7.retry_status", str(status))
        r = _extract_result(body)
        _check(r.get("accepted") is True, "E7.retry_accepted",
               f"rollback failed; retry rejected with reason={r.get('reason')}")
    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[E.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        mock.reset()
        mock.set_reply("Mocked reply")
        _delete_session(client)

    return errors


# ─────────────────────────────────────────────────────────────
# Case F — append_message invariants
# ─────────────────────────────────────────────────────────────


def check_f_append_invariants(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_F")
        mock.reset()
        mock.set_reply("Mocked reply")

        from tests.testbench.session_store import get_session_store

        # F1 — after a successful avatar event, the last two messages'
        # timestamps must be non-decreasing.
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="f1", tool_id="fist", action_id="poke"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is True, "F1.setup_accepted",
               f"reason={r.get('reason')}")
        session = get_session_store().require()
        msgs = session.messages
        _check(len(msgs) >= 2, "F1.messages_len",
               f"expected >=2 messages, got {len(msgs)}")
        if len(msgs) >= 2:
            ts_a = msgs[-2].get("timestamp")
            ts_b = msgs[-1].get("timestamp")
            _check(ts_a is not None and ts_b is not None, "F1.timestamps_present",
                   f"ts_a={ts_a} ts_b={ts_b}")
            _check(str(ts_a) <= str(ts_b), "F1.monotonic",
                   f"ts_a={ts_a} > ts_b={ts_b}")

        # F2 — after an LLM failure, session.messages must be unchanged.
        before = list(session.messages)
        before_len = len(before)
        mock.set_raise(RuntimeError("synthetic F2 failure"))
        status, body = _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="f2-fail",
                            tool_id="hammer", action_id="bonk"),
        )
        r = _extract_result(body)
        _check(r.get("accepted") is False and r.get("reason") == "llm_failed",
               "F2.failure_reason",
               f"accepted={r.get('accepted')} reason={r.get('reason')}")
        _check(len(session.messages) == before_len, "F2.messages_unchanged",
               f"expected {before_len}, got {len(session.messages)}")

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[F.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        mock.reset()
        mock.set_reply("Mocked reply")
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Case G — diagnostics op records
# ─────────────────────────────────────────────────────────────


_DIAG_OPS = {
    "avatar": "avatar_interaction_simulated",
    "agent_callback": "agent_callback_simulated",
    "proactive": "proactive_simulated",
}


def check_g_diagnostics(client, mock) -> list[str]:
    errors: list[str] = []
    try:
        _create_session(client, "p25_G")
        mock.reset()
        mock.set_reply("Mocked reply")

        # Clear the ring so our three simulate_* calls are the entries
        # we inspect.
        client.delete("/api/diagnostics/errors")

        # G1a — avatar.
        _post_event(
            client, "avatar",
            _avatar_payload(interaction_id="g1", tool_id="fist", action_id="poke"),
        )
        entry = _latest_diag(client, _DIAG_OPS["avatar"])
        _check(entry is not None, "G1a.present",
               "no avatar_interaction_simulated entry after simulate_avatar_interaction")
        if entry is not None:
            detail = entry.get("detail") or {}
            for k in ("accepted", "reason", "elapsed_ms", "mirror_to_recent"):
                _check(k in detail, f"G1a.detail.{k}",
                       f"missing key {k}; detail={detail}")
            _check(entry.get("type") == "avatar_interaction_simulated",
                   "G2.avatar_op_type", f"type={entry.get('type')}")

        # G1b — agent_callback.
        _post_event(
            client, "agent_callback",
            {"callbacks": ["g1b msg"]},
        )
        entry = _latest_diag(client, _DIAG_OPS["agent_callback"])
        _check(entry is not None, "G1b.present",
               "no agent_callback_simulated entry")
        if entry is not None:
            detail = entry.get("detail") or {}
            for k in ("accepted", "reason", "elapsed_ms", "mirror_to_recent"):
                _check(k in detail, f"G1b.detail.{k}",
                       f"missing key {k}; detail={detail}")
            _check(entry.get("type") == "agent_callback_simulated",
                   "G2.agent_callback_op_type", f"type={entry.get('type')}")

        # G1c — proactive.
        _post_event(client, "proactive", {"kind": "home"})
        entry = _latest_diag(client, _DIAG_OPS["proactive"])
        _check(entry is not None, "G1c.present",
               "no proactive_simulated entry")
        if entry is not None:
            detail = entry.get("detail") or {}
            for k in ("accepted", "reason", "elapsed_ms", "mirror_to_recent"):
                _check(k in detail, f"G1c.detail.{k}",
                       f"missing key {k}; detail={detail}")
            _check(entry.get("type") == "proactive_simulated",
                   "G2.proactive_op_type", f"type={entry.get('type')}")

    except AssertionFailed as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f"[G.unhandled] {type(exc).__name__}: {exc}\n"
                      + traceback.format_exc())
    finally:
        _delete_session(client)
    return errors


# ─────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────


def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok] no violations")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        for sub in str(line).splitlines():
            print(f"    {sub}")
    return len(errors)


def main() -> int:
    print("=" * 66)
    print(" P25 External Events Smoke  (Day 1 TestClient coverage)")
    print("=" * 66)
    started = time.perf_counter()

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)
    mock = _install_llm_mock()

    total = 0
    total += _report("A | three kinds happy path", check_a_happy_paths(client, mock))
    total += _report("B | dedupe matrix (hit / upgrade / reset / info / cap overflow)",
                     check_b_dedupe_matrix(client, mock))
    total += _report("C | coerce_info surfacing (intensity / kind / language)",
                     check_c_coerce(client, mock))
    total += _report("D | mirror_to_recent applied / fallback / [PASS]",
                     check_d_mirror(client, mock))
    total += _report("E | error branches (invalid / empty / 400 / 404 x3 / llm_failed)",
                     check_e_errors(client, mock))
    total += _report("F | append_message invariants (monotonic ts / no-append on fail)",
                     check_f_append_invariants(client, mock))
    total += _report("G | diagnostics op records (correct type + detail fields)",
                     check_g_diagnostics(client, mock))

    elapsed = time.perf_counter() - started
    print("")
    print("=" * 66)
    print(f" total elapsed: {elapsed:.2f}s")
    if total:
        print(f" [FAIL] {total} violation(s) across P25 external-events smoke.")
        return 1
    print("P25 EXTERNAL EVENTS SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
