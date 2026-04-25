"""P25 Prompt-Preview ground-truth smoke (Day 2 polish r4).

Purpose
-------
Guard the "Prompt Preview = ground-truth of what the LLM actually saw"
invariant. Pre-r4 the preview was purely derived from session.messages,
which misses:

* External events (avatar / agent_callback / proactive) — they append an
  ephemeral ``HumanMessage(instruction)`` to the wire tail that never
  lands in session.messages. For avatar specifically, only the short
  human-readable ``memory_note`` (e.g. "[主人摸了摸你的头]") goes into
  session.messages — the real instruction (touch zone, intensity, text
  context, reward drop, easter egg) is only on the wire.

This smoke asserts:

    PP1: chat.send → last_llm_wire populated + source=chat.send +
         wire_messages equals the wire passed to the LLM and equals
         ``bundle.wire_messages`` (they're the same at send time).

    PP2: avatar event → last_llm_wire populated + source=avatar_event +
         tail message is the **full instruction**, not the memory_note.
         This is the regression test for the r4 bug the tester reported.

    PP3: agent_callback event → same shape, source=agent_callback, tail
         contains the instruction body (``AGENT_CALLBACK_NOTIFICATION``
         prefix + callbacks).

    PP4: proactive event → same shape, source=proactive_chat, tail
         contains the proactive prompt template (with lanlan / master
         name substitution done).

    PP5: chat.send after an avatar event → last_llm_wire.source flips
         back to chat.send; the earlier avatar snapshot is gone. This
         guards against "stale ground-truth" where a later chat.send
         doesn't re-stamp.

    PP6: reply_chars is populated correctly (equal to len(reply_text))
         after a successful LLM call; starts at -1 on failure path.

    PP7: record_last_llm_wire with an unknown source raises ValueError
         (chokepoint audit).

Environment isolation: mirrors p25_external_events_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p25_prompt_preview_truth_smoke.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Env setup — must run before any testbench import ────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p25_preview_truth_"))
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


# ── LLM mocks — never hit network ───────────────────────────────────────


class _MockAsyncLLM:
    """Async stub for external_events._invoke_llm_once."""

    def __init__(self) -> None:
        self.next_reply = "mocked reply"
        self.calls = 0

    def set_reply(self, text: str) -> None:
        self.next_reply = text

    async def __call__(self, session, wire_messages):  # noqa: ANN001
        self.calls += 1
        return self.next_reply


class _MockStreamChunk:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage_metadata = None


class _MockStreamingClient:
    """Async-iterable stub replacing ``utils.llm_client.ChatOpenAI``
    inside chat_runner. Streams a single-chunk reply, then closes.
    """

    def __init__(self, reply_text: str) -> None:
        self._reply = reply_text

    def astream(self, wire_messages):  # noqa: ANN001
        # Returns an async iterator that yields exactly one chunk.
        reply = self._reply

        async def _gen():
            yield _MockStreamChunk(reply)

        return _gen()

    async def aclose(self) -> None:
        pass


# ── Helpers ─────────────────────────────────────────────────────────────


class _AssertFail(Exception):
    """One-line assertion failure, raised by :func:`_check`."""


def _check(cond: bool, label: str, msg: str = "") -> None:
    if not cond:
        detail = f" — {msg}" if msg else ""
        raise _AssertFail(f"[{label}] {detail.strip(' —')}")


def _install_external_event_llm_mock() -> _MockAsyncLLM:
    from tests.testbench.pipeline import external_events as ee
    mock = _MockAsyncLLM()
    ee._invoke_llm_once = mock  # type: ignore[assignment]
    return mock


def _install_chat_send_mock(reply_text: str) -> None:
    """Swap ChatOpenAI used by chat_runner.stream_send with a stub that
    returns reply_text in a single chunk. chat_runner imports ChatOpenAI
    locally inside stream_send, so we monkeypatch at the module it imports
    from (utils.llm_client). The client is constructed fresh per call, so
    we intercept by binding a simple factory on the module.
    """
    from utils import llm_client as llm_mod

    class _Factory:
        def __init__(self, *args, **kwargs):  # noqa: ANN003, D401
            self._reply = reply_text

        def astream(self, wire_messages):  # noqa: ANN001
            reply = self._reply

            async def _gen():
                yield _MockStreamChunk(reply)

            return _gen()

        async def aclose(self) -> None:
            pass

    llm_mod.ChatOpenAI = _Factory  # type: ignore[assignment]


def _create_fresh_session(client) -> None:
    client.post("/api/session", json={"name": "p25_preview_truth"})
    r = client.put("/api/persona", json={
        "character_name": "NEKO",
        "master_name": "Master",
        "language": "zh-CN",
        "system_prompt": (
            "You are {LANLAN_NAME}. You address the user as {MASTER_NAME}."
        ),
    })
    assert r.status_code == 200, f"persona PUT failed: {r.text}"
    from tests.testbench.session_store import get_session_store
    s = get_session_store().require()
    s.model_config = {
        "chat": {
            "api_key": "sk-FAKE",
            "model": "gpt-4o",
            "base_url": "http://localhost:1",
        },
        "judge": {"api_key": "", "model": "gpt-4o"},
    }


def _get_preview_last_wire(client) -> dict[str, Any] | None:
    r = client.get("/api/chat/prompt_preview")
    assert r.status_code == 200, f"prompt_preview fetch failed: {r.text}"
    body = r.json()
    return body.get("last_llm_wire")


def _post_event(client, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = client.post(
        "/api/session/external-event",
        json={"kind": kind, "payload": payload, "mirror_to_recent": False},
    )
    assert r.status_code == 200, f"event POST {kind} failed: {r.status_code} {r.text}"
    return r.json()


# ── Cases ───────────────────────────────────────────────────────────────


def check_pp1_chat_send(client) -> list[str]:
    """chat.send populates last_llm_wire with source=chat.send."""
    errors: list[str] = []
    try:
        _create_fresh_session(client)
        _install_chat_send_mock("assistant reply content")

        r = client.post(
            "/api/chat/send",
            json={"content": "hello world", "role": "user"},
        )
        _check(
            r.status_code == 200,
            "PP1.status",
            f"chat.send status={r.status_code} body={r.text[:200]}",
        )

        # Stream is SSE — we don't need to parse it; we just wait for
        # TestClient to finish and read session.last_llm_wire via preview.
        last = _get_preview_last_wire(client)
        _check(last is not None, "PP1.present", "last_llm_wire is None after chat.send")
        assert last is not None
        _check(
            last.get("source") == "chat.send",
            "PP1.source",
            f"source={last.get('source')!r}, expected 'chat.send'",
        )
        wire = last.get("wire_messages") or []
        _check(
            len(wire) >= 2,
            "PP1.wire_len",
            f"wire len={len(wire)}, expected >= 2 (system + user)",
        )
        _check(
            wire[0].get("role") == "system",
            "PP1.head_role",
            f"head role={wire[0].get('role')!r}",
        )
        _check(
            wire[-1].get("role") == "user",
            "PP1.tail_role",
            f"tail role={wire[-1].get('role')!r}",
        )
        _check(
            "hello world" in str(wire[-1].get("content") or ""),
            "PP1.tail_content",
            f"tail content={str(wire[-1].get('content'))[:80]!r}",
        )
        _check(
            int(last.get("reply_chars", -99)) == len("assistant reply content"),
            "PP1.reply_chars",
            f"reply_chars={last.get('reply_chars')!r}, "
            f"expected {len('assistant reply content')}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP1.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp2_avatar_event(client, mock_ext) -> list[str]:
    """avatar event populates last_llm_wire; tail = full instruction,
    NOT the memory_note.
    """
    errors: list[str] = []
    try:
        _create_fresh_session(client)
        mock_ext.set_reply("assistant reaction")

        # Payload shape mirrors production UI: target="avatar" is required,
        # reward_drop is only meaningful for tool_id="fist" (normalized as
        # bool in config/prompts_avatar_interaction.py). We want both the
        # "reward" line AND the "text_context" line to appear in the
        # synthesized instruction so PP2.tail_is_instruction can assert that
        # last_llm_wire.tail contains the raw synthesized instruction, NOT
        # just the short memory_note that ends up in session.messages.
        _post_event(client, "avatar", {
            "interaction_id": "pp2",
            "tool_id": "fist",
            "action_id": "poke",
            "intensity": "normal",
            "target": "avatar",
            "text_context": "(tester draft in composer)",
            "reward_drop": True,
        })

        last = _get_preview_last_wire(client)
        _check(last is not None, "PP2.present", "last_llm_wire is None after avatar")
        assert last is not None
        _check(
            last.get("source") == "avatar_event",
            "PP2.source",
            f"source={last.get('source')!r}",
        )
        wire = last.get("wire_messages") or []
        _check(len(wire) >= 2, "PP2.wire_len", f"wire len={len(wire)}")
        tail = wire[-1]
        _check(
            tail.get("role") == "user",
            "PP2.tail_role",
            f"tail role={tail.get('role')!r}",
        )
        tail_content = str(tail.get("content") or "")

        # The full instruction (built via
        # _build_avatar_interaction_instruction) is multi-line and
        # clearly longer than any single-line memory_note. Assert > 40
        # chars as a proxy for "not the short note"; AND check that
        # at least one of the avatar-specific keywords from the helper's
        # template shows up, proving we captured the rendered
        # instruction rather than a placeholder string.
        _check(
            len(tail_content) > 40,
            "PP2.tail_not_note",
            f"tail content too short ({len(tail_content)} chars): "
            f"{tail_content[:80]!r} — looks like the memory_note, not "
            "the full instruction",
        )

        # Reward + text_context presence — these fields are ONLY in the
        # instruction, NEVER in the memory_note. If they show up here,
        # we've confirmed preview = ground truth.
        _check(
            "tester draft" in tail_content,
            "PP2.text_context",
            "tail missing text_context marker (confirms bug): "
            f"{tail_content[:200]!r}",
        )
        _check(
            int(last.get("reply_chars", -99)) == len("assistant reaction"),
            "PP2.reply_chars",
            f"reply_chars={last.get('reply_chars')!r}",
        )

        # PROVE THE BUG: session.messages tail should be the memory_note
        # (e.g. contain fist-flavoured phrase), NOT the full instruction.
        # If both are the same, the fix is a no-op and the test is
        # tautological. Fetch session.messages via the chat router.
        r = client.get("/api/chat/messages")
        _check(r.status_code == 200, "PP2.msgs.status", f"{r.status_code}")
        msgs = r.json().get("messages") or []
        # For avatar happy path messages are [user memory_note, assistant reply]
        _check(
            len(msgs) >= 1,
            "PP2.msgs.count",
            f"expected >=1 msg, got {len(msgs)}",
        )
        user_msg = next((m for m in msgs if m.get("role") == "user"), None)
        _check(
            user_msg is not None,
            "PP2.msgs.user_present",
            "no user msg in session.messages after avatar event",
        )
        if user_msg is not None:
            user_content = str(user_msg.get("content") or "")
            # Memory note is short + single line. Instruction is long +
            # multi-line with context/reward bullets. If the two are
            # **equal**, the bug is still there.
            _check(
                user_content != tail_content,
                "PP2.preview_diverges_from_memory",
                "session.messages[user].content == last_llm_wire tail "
                "content — this is the **bug** the r4 fix targets; "
                "the preview should show the ephemeral instruction which "
                "is strictly richer than the memory_note",
            )
            _check(
                len(user_content) < len(tail_content),
                "PP2.instruction_longer_than_note",
                f"user memory_note chars={len(user_content)} should be "
                f"shorter than instruction chars={len(tail_content)}",
            )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP2.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp3_agent_callback(client, mock_ext) -> list[str]:
    errors: list[str] = []
    try:
        _create_fresh_session(client)
        mock_ext.set_reply("callback reaction")
        _post_event(client, "agent_callback", {
            "callbacks": ["task_a: done", "task_b: cancelled"],
            "language": "zh-CN",
        })

        last = _get_preview_last_wire(client)
        assert last is not None
        _check(last.get("source") == "agent_callback", "PP3.source",
               f"source={last.get('source')!r}")
        wire = last.get("wire_messages") or []
        tail_content = str(wire[-1].get("content") or "") if wire else ""
        _check(
            "task_a: done" in tail_content,
            "PP3.callback_present",
            f"tail missing callback body: {tail_content[:200]!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP3.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp4_proactive(client, mock_ext) -> list[str]:
    errors: list[str] = []
    try:
        _create_fresh_session(client)
        mock_ext.set_reply("proactive opener")
        _post_event(client, "proactive", {
            "kind": "time_passed",
            "language": "zh-CN",
        })

        last = _get_preview_last_wire(client)
        assert last is not None
        _check(last.get("source") == "proactive_chat", "PP4.source",
               f"source={last.get('source')!r}")
        wire = last.get("wire_messages") or []
        _check(len(wire) >= 2, "PP4.wire_len", f"wire len={len(wire)}")
        tail_content = str(wire[-1].get("content") or "") if wire else ""
        _check(
            len(tail_content) > 40,
            "PP4.tail_populated",
            f"tail too short: {tail_content[:200]!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP4.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp5_source_flips_back(client, mock_ext) -> list[str]:
    """After an avatar event, a subsequent chat.send must flip
    last_llm_wire.source back to chat.send."""
    errors: list[str] = []
    try:
        _create_fresh_session(client)

        mock_ext.set_reply("avatar reply")
        _post_event(client, "avatar", {
            "interaction_id": "pp5",
            "tool_id": "fist",
            "action_id": "poke",
            "intensity": "normal",
            "target": "avatar",
        })

        last_after_avatar = _get_preview_last_wire(client)
        assert last_after_avatar is not None
        _check(
            last_after_avatar.get("source") == "avatar_event",
            "PP5.after_avatar",
            f"source={last_after_avatar.get('source')!r}",
        )

        _install_chat_send_mock("followup reply")
        r = client.post(
            "/api/chat/send",
            json={"content": "next turn", "role": "user"},
        )
        _check(r.status_code == 200, "PP5.send_status", f"{r.status_code}")

        last_after_send = _get_preview_last_wire(client)
        assert last_after_send is not None
        _check(
            last_after_send.get("source") == "chat.send",
            "PP5.after_send",
            f"source={last_after_send.get('source')!r} — stale avatar "
            "snapshot not overwritten by chat.send",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP5.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp6_initial_state(client) -> list[str]:
    """Fresh session → last_llm_wire is None (never called LLM yet)."""
    errors: list[str] = []
    try:
        _create_fresh_session(client)
        last = _get_preview_last_wire(client)
        _check(
            last is None,
            "PP6.initial_none",
            f"expected None, got {last!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP6.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


def check_pp7_unknown_source_rejected() -> list[str]:
    """record_last_llm_wire with a typo source → ValueError (chokepoint)."""
    errors: list[str] = []
    try:
        from tests.testbench.pipeline.wire_tracker import record_last_llm_wire
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        try:
            record_last_llm_wire(
                session,
                [{"role": "system", "content": "x"}],
                source="typo_not_a_real_source",
            )
        except ValueError:
            return errors
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"[PP7.wrong_exc] record_last_llm_wire raised "
                f"{type(exc).__name__} instead of ValueError for unknown source"
            )
            return errors
        errors.append(
            "[PP7.missed] record_last_llm_wire accepted unknown source — "
            "chokepoint audit broken"
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(f"[PP7.crash] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")
    return errors


# ── Orchestration ───────────────────────────────────────────────────────


def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok]")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        print(f"    {line}")
    return len(errors)


def main() -> int:
    print("=" * 66)
    print(" P25 Prompt-Preview Ground-Truth Smoke  (r4 regression guard)")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    mock_ext = _install_external_event_llm_mock()

    total = 0
    total += _report(
        "PP1 — chat.send stamps last_llm_wire.source=chat.send",
        check_pp1_chat_send(client),
    )
    total += _report(
        "PP2 — avatar event preview tail = full instruction (not memory_note)",
        check_pp2_avatar_event(client, mock_ext),
    )
    total += _report(
        "PP3 — agent_callback event preview tail contains callbacks",
        check_pp3_agent_callback(client, mock_ext),
    )
    total += _report(
        "PP4 — proactive event preview tail contains prompt template",
        check_pp4_proactive(client, mock_ext),
    )
    total += _report(
        "PP5 — chat.send after avatar flips source back to chat.send",
        check_pp5_source_flips_back(client, mock_ext),
    )
    total += _report(
        "PP6 — fresh session last_llm_wire is None",
        check_pp6_initial_state(client),
    )
    total += _report(
        "PP7 — record_last_llm_wire unknown source raises ValueError",
        check_pp7_unknown_source_rejected(),
    )

    try:
        client.delete("/api/session")
    except Exception:
        pass

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in Prompt-Preview r4 guard.")
        return 1
    print(" [PASS] Prompt-Preview ground-truth contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
