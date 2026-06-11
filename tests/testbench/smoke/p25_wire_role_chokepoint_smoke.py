"""P25 Day 2 polish r3 — wire role chokepoint smoke.

Guards ``tests.testbench.pipeline.prompt_builder.build_prompt_bundle``
against the "runtime role=system message survives into wire" failure
class (LESSONS §7.25 — fourth recurrence of "cross-boundary shape drift").

Background
----------
The main program (``main_logic/omni_offline_client.py``) semantics:
``SystemMessage`` only exists at ``_conversation_history[0]`` (connect()
time init), every runtime entry point (``send_text_message`` /
``create_response`` / ``prompt_ephemeral``) appends ``HumanMessage``
(role=user). There is NO runtime path that yields a role=system message
in the wire.

Testbench composer exposes a "Role: User / System" dropdown + [Send]
button, letting the tester append a role=system message to
``session.messages``. If passed through to the wire raw, Vertex AI
Gemini gets ``INVALID_ARGUMENT "Model input cannot be empty"`` (when
wire has no role=user after the initial system prompt) or 200 with
empty reply (provider-side shape allergy, leads to stale-reply time
shift the next turn — documented in AGENT_NOTES §4.16 #32 and §4.27
#113).

This smoke exercises ``build_prompt_bundle`` directly (no LLM, no
HTTP, no TestClient) to lock the chokepoint contract:

* C1 — empty session.messages → wire length 1 (just system_prompt).
* C2 — role=system message in session.messages → wire rewrites it to
       role=user with "[system note] " prefix.
* C3 — role=system mixed with user/assistant → only system entries
       are rewritten, order preserved, no entry dropped.
* C4 — role=system with list (multi-modal) content → prefix prepended
       as a text-type block, original content preserved.
* C5 — wire never contains role=system at any index > 0 after the
       chokepoint, regardless of how many role=system messages the
       session had.

Usage::

    .venv\\Scripts\\python.exe \\
        tests/testbench/smoke/p25_wire_role_chokepoint_smoke.py

Exit 0 with ``P25 WIRE ROLE CHOKEPOINT SMOKE OK`` on success, non-zero
and a clearly labeled failing case on any violation.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# Environment isolation — mirror p25_external_events_smoke so the
# repo's ``tests/testbench_data`` tree stays untouched.
# ─────────────────────────────────────────────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p25_wire_role_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    tb_config.SAVED_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    tb_config.AUTOSAVE_DIR.mkdir(parents=True, exist_ok=True)
    tb_config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tb_config.SANDBOXES_DIR.mkdir(parents=True, exist_ok=True)
    return tmp_data


# ─────────────────────────────────────────────────────────────
# Session fabricator — build a minimal but complete Session that
# build_prompt_bundle is happy with. We do NOT go through the full
# create-session endpoint because this smoke probes a pure pipeline
# function; a fabricated Session with only the fields the builder
# actually reads is enough and keeps the smoke fast/deterministic.
# ─────────────────────────────────────────────────────────────


def _make_session_with_messages(messages: list[dict[str, Any]]):
    """Fabricate a fresh Session containing the given messages.

    ``SessionStore.create`` is async (it acquires a registry lock and
    destroys any current session first); we drive it synchronously
    via ``asyncio.run(...)`` since this smoke is strictly
    single-threaded and doesn't need the outer event loop. The store
    is a module-level singleton, so calling ``create`` twice in a row
    naturally destroys the previous session and produces a fresh one
    — each C-case gets a clean slate without explicit teardown.

    The initial system prompt assembly touches PersonaManager /
    character_prompt fallbacks; default values suffice for this
    smoke (we only care about the runtime-messages segment of the
    wire, not the first ``{role: 'system', content: system_prompt}``
    entry's exact text).
    """
    from tests.testbench.session_store import get_session_store

    store = get_session_store()
    session = asyncio.run(store.create())
    # Fill the minimum persona fields build_prompt_bundle requires
    # (character_name is the hard gate; master_name empty is OK, it
    # just surfaces as a warning). See prompt_builder.py L416-425.
    session.persona = {
        "character_name": "ChokepointSmokeBot",
        "master_name": "tester",
        "language": "zh-CN",
    }
    session.messages.clear()
    session.messages.extend(messages)
    return session


def _check(cond: bool, label: str, detail: str = "") -> str | None:
    if cond:
        return None
    return f"[{label}] {detail}".rstrip()


# ─────────────────────────────────────────────────────────────
# Cases.
# ─────────────────────────────────────────────────────────────


def check_c_wire_role_chokepoint() -> list[str]:
    from tests.testbench.pipeline.prompt_builder import build_prompt_bundle
    from tests.testbench.chat_messages import make_message
    from datetime import datetime

    errors: list[str] = []

    def _add(err: str | None) -> None:
        if err:
            errors.append(err)

    ts0 = datetime(2026, 4, 23, 12, 0, 0)
    ts1 = datetime(2026, 4, 23, 12, 0, 1)
    ts2 = datetime(2026, 4, 23, 12, 0, 2)
    ts3 = datetime(2026, 4, 23, 12, 0, 3)

    # C1 — Empty session: wire length must be exactly 1 (just the
    # initial system_prompt). Zero runtime messages means the tail is
    # the initial system. chat_runner is supposed to guarantee a real
    # user role=user follows, but the chokepoint itself doesn't
    # synthesize nudges (that's simulated_user's domain — see
    # PROGRESS 2026-04-19 Gemini nudge entry for the ruleset).
    try:
        session = _make_session_with_messages([])
        bundle = build_prompt_bundle(session)
        _add(_check(
            len(bundle.wire_messages) == 1,
            "C1.wire_len_empty_session",
            f"expected 1 (just system_prompt), got "
            f"{len(bundle.wire_messages)}: {bundle.wire_messages!r}",
        ))
        _add(_check(
            bundle.wire_messages and bundle.wire_messages[0]["role"] == "system",
            "C1.first_entry_is_system",
            f"first wire entry role = "
            f"{bundle.wire_messages[0].get('role') if bundle.wire_messages else None}",
        ))
    except Exception as exc:
        errors.append(f"[C1.setup_crashed] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")

    # C2 — Single role=system runtime message. Must be rewritten to
    # role=user with "[system note] " prefix; the original content
    # must still be reachable in the rewritten payload for LLM
    # interpretation.
    try:
        sys_msg = make_message(
            role="system",
            content="be more concise",
            timestamp=ts0,
            source="manual",
        )
        session = _make_session_with_messages([sys_msg])
        bundle = build_prompt_bundle(session)

        _add(_check(
            len(bundle.wire_messages) == 2,
            "C2.wire_len",
            f"expected 2 (system_prompt + 1 rewritten), got "
            f"{len(bundle.wire_messages)}",
        ))
        if len(bundle.wire_messages) >= 2:
            entry = bundle.wire_messages[1]
            _add(_check(
                entry.get("role") == "user",
                "C2.role_rewritten_to_user",
                f"entry[1].role = {entry.get('role')!r}, "
                f"expected 'user' (L36 §7.25 chokepoint)",
            ))
            _add(_check(
                isinstance(entry.get("content"), str)
                and entry["content"].startswith("[system note] "),
                "C2.prefix_applied",
                f"entry[1].content doesn't start with '[system note] ': "
                f"{entry.get('content')!r}",
            ))
            _add(_check(
                isinstance(entry.get("content"), str)
                and entry["content"].endswith("be more concise"),
                "C2.original_preserved",
                f"entry[1].content tail doesn't preserve original: "
                f"{entry.get('content')!r}",
            ))
    except Exception as exc:
        errors.append(f"[C2.setup_crashed] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")

    # C3 — Mixed user / assistant / system messages; only role=system
    # entries are rewritten, order preserved, no entry dropped, no
    # phantom entry added.
    try:
        mixed = [
            make_message(role="user", content="hi", timestamp=ts0,
                         source="manual"),
            make_message(role="assistant", content="hello",
                         timestamp=ts1, source="llm"),
            make_message(role="system", content="switch to shorter mode",
                         timestamp=ts2, source="manual"),
            make_message(role="user", content="ok continue",
                         timestamp=ts3, source="manual"),
        ]
        session = _make_session_with_messages(mixed)
        bundle = build_prompt_bundle(session)

        # 1 initial system + 4 session messages = 5 entries expected.
        _add(_check(
            len(bundle.wire_messages) == 5,
            "C3.wire_len",
            f"expected 5 (system_prompt + 4 runtime), got "
            f"{len(bundle.wire_messages)}",
        ))
        if len(bundle.wire_messages) == 5:
            roles = [m.get("role") for m in bundle.wire_messages]
            # Position 0 = initial system prompt; positions 1-4 are
            # runtime entries. Only position 3 (the role=system
            # insertion) should be rewritten.
            _add(_check(
                roles == ["system", "user", "assistant", "user", "user"],
                "C3.role_sequence",
                f"expected [system, user, assistant, user, user], "
                f"got {roles!r}",
            ))
            # The rewritten entry content must carry the prefix.
            rewritten = bundle.wire_messages[3]
            _add(_check(
                isinstance(rewritten.get("content"), str)
                and rewritten["content"].startswith("[system note] "),
                "C3.rewritten_prefix",
                f"rewritten entry content = {rewritten.get('content')!r}",
            ))
            # Non-system entries must NOT gain the prefix.
            u0 = bundle.wire_messages[1]
            _add(_check(
                isinstance(u0.get("content"), str)
                and not u0["content"].startswith("[system note] "),
                "C3.user_entry_not_rewritten",
                f"user entry got spurious prefix: "
                f"{u0.get('content')!r}",
            ))
    except Exception as exc:
        errors.append(f"[C3.setup_crashed] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")

    # C4 — Multi-modal content (list-typed content). Prefix must be
    # prepended as a text-block entry, original blocks preserved.
    # ChatOpenAI._normalize_messages accepts either flat string or
    # list-of-blocks content; keep contract uniform.
    try:
        multimodal_payload = [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "https://example/x.png"}},
        ]
        sys_msg_mm = make_message(
            role="system",
            content=multimodal_payload,
            timestamp=ts0,
            source="manual",
        )
        session = _make_session_with_messages([sys_msg_mm])
        bundle = build_prompt_bundle(session)

        if len(bundle.wire_messages) >= 2:
            rewritten = bundle.wire_messages[1]
            _add(_check(
                rewritten.get("role") == "user",
                "C4.role_rewritten",
                f"entry[1].role = {rewritten.get('role')!r}",
            ))
            content = rewritten.get("content")
            _add(_check(
                isinstance(content, list),
                "C4.content_shape",
                f"expected list (multi-modal), got {type(content).__name__}",
            ))
            if isinstance(content, list):
                _add(_check(
                    len(content) == 3,
                    "C4.content_len",
                    f"expected 3 blocks (prefix + 2 original), got "
                    f"{len(content)}",
                ))
                _add(_check(
                    isinstance(content[0], dict)
                    and content[0].get("type") == "text"
                    and content[0].get("text") == "[system note] ",
                    "C4.prefix_block_first",
                    f"first block = {content[0]!r}",
                ))
                if len(content) >= 3:
                    # Original blocks preserved in-order.
                    _add(_check(
                        content[1] == multimodal_payload[0]
                        and content[2] == multimodal_payload[1],
                        "C4.original_blocks_preserved",
                        f"blocks[1:] = {content[1:]!r}, expected "
                        f"{multimodal_payload!r}",
                    ))
        else:
            errors.append(
                "[C4.wire_too_short] "
                f"wire_messages length = {len(bundle.wire_messages)}"
            )
    except Exception as exc:
        errors.append(f"[C4.setup_crashed] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")

    # C5 — Post-chokepoint invariant: for any combination of
    # messages, NO wire entry at index >= 1 may have role == 'system'.
    # Only wire_messages[0] (the initial system_prompt) is allowed to
    # carry role=system. This is the broad invariant that subsumes
    # C2/C3/C4 and catches "future drift" if someone adds a new
    # builder branch without running it through the rewrite.
    try:
        all_system = [
            make_message(role="system", content=f"sys #{i}",
                         timestamp=datetime(2026, 4, 23, 12, 0, i),
                         source="manual")
            for i in range(5)
        ]
        session = _make_system_heavy = _make_session_with_messages(all_system)
        bundle = build_prompt_bundle(session)
        tail_roles = [m.get("role") for m in bundle.wire_messages[1:]]
        _add(_check(
            all(r != "system" for r in tail_roles),
            "C5.no_runtime_system_in_wire",
            f"wire[1:] contains role=system (L36 §7.25 chokepoint "
            f"leak!): tail_roles = {tail_roles!r}",
        ))
        # Every rewritten entry must have the prefix for audit
        # traceability (so diagnostics log can correlate).
        for idx, entry in enumerate(bundle.wire_messages[1:], start=1):
            content = entry.get("content")
            if isinstance(content, str):
                _add(_check(
                    content.startswith("[system note] "),
                    f"C5.prefix_entry_{idx}",
                    f"entry[{idx}].content doesn't start with prefix: "
                    f"{content!r}",
                ))
    except Exception as exc:
        errors.append(f"[C5.setup_crashed] {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc()}")

    return errors


# ─────────────────────────────────────────────────────────────
# Runner.
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
    print(" P25 Day 2 polish r3 — Wire Role Chokepoint Smoke")
    print("=" * 66)
    started = time.perf_counter()

    _setup_env()

    total = 0
    total += _report(
        "C | build_prompt_bundle rewrites runtime role=system → user",
        check_c_wire_role_chokepoint(),
    )

    elapsed = time.perf_counter() - started
    print("")
    print("=" * 66)
    print(f" total elapsed: {elapsed:.2f}s")
    if total:
        print(f" [FAIL] {total} violation(s) in wire role chokepoint smoke.")
        return 1
    print("P25 WIRE ROLE CHOKEPOINT SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
