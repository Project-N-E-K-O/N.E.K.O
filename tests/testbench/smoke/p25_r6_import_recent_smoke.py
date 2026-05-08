"""P25 Day 2 polish r6 — "save current chat to recent.json" smoke.

Purpose
-------
Guard the one-click shortcut endpoint ``POST /api/memory/recent/
import_from_session`` (new in P25 Day 2 polish r6) that lets the Chat
workspace dump ``session.messages`` into ``memory/<character>/recent.
json`` without going through the ``recent.compress`` LLM flow.

Contracts under test
--------------------
R6A — **happy path, append mode**:
      POST with mode=append on a 3-message session creates recent.json
      with exactly 3 entries in LangChain canonical shape
      (``{type: human|ai|system, data: {content}}``). A second POST
      doubles the count (no dedup — append semantics).

R6B — **happy path, replace mode**:
      POST with mode=replace on a 2-message session overwrites the
      4-entry file from R6A and leaves exactly 2 entries.

R6C — **banner filter**:
      Session containing a SOURCE_EXTERNAL_EVENT_BANNER message
      (``role=system``, ``source=external_event_banner``) plus one real
      user message → POST returns ``added=1``, ``skipped.banner=1``; the
      banner does NOT appear in recent.json. This is the regression
      test against "banner leaked into recent, got re-injected into
      next /chat/send wire".

R6D — **empty / whitespace content filter**:
      Session with one valid user message + one message that is
      ``content=""`` + one ``content="   "`` → ``added=1``,
      ``skipped.empty_content=2``.

R6E — **round-trip** with ``messages_from_dict`` — each written entry
      must reconstitute into a ``HumanMessage``/``AIMessage``/
      ``SystemMessage`` whose ``.content`` equals the original.

R6F — **error mapping**:
      - No active session → 404.
      - Invalid mode → 400 InvalidMode.
      - Session with **only** banners / empty / invalid role → 409
        NoMessagesToImport (tester pressed button on an "effectively
        empty" chat).

Environment isolation: mirrors p25_external_events_smoke.py.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p25_r6_import_recent_smoke.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── Env setup — must run before any testbench import ────────────────────


def _setup_env() -> Path:
    tmp_data = Path(tempfile.mkdtemp(prefix="p25_r6_import_recent_"))
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


# ── Helpers ─────────────────────────────────────────────────────────────


class _AssertFail(Exception):
    pass


def _check(cond: bool, label: str, msg: str = "") -> None:
    if not cond:
        detail = f" — {msg}" if msg else ""
        raise _AssertFail(f"[{label}]{detail}")


def _create_session(client, name: str = "p25_r6_import") -> None:
    r = client.post("/api/session", json={"name": name})
    assert r.status_code == 201, f"create session failed: {r.status_code} {r.text}"
    r = client.put("/api/persona", json={
        "character_name": "NEKO",
        "master_name": "Master",
        "language": "zh-CN",
        "system_prompt": "You are {LANLAN_NAME}. You address the user as {MASTER_NAME}.",
    })
    assert r.status_code == 200, f"persona PUT failed: {r.text}"


def _delete_session(client) -> None:
    try:
        client.delete("/api/session")
    except Exception:
        pass


def _seed_messages(session, specs: list[dict[str, Any]]) -> None:
    """Populate ``session.messages`` directly, bypassing prompt pipeline.

    Each spec: ``{role, content, source?}``. We reuse ``make_message`` so
    ids / timestamps / field shape match what the real chat.send / inject
    paths produce; ``append_message`` ensures monotonic timestamps and
    runs through the single writer (L36 chokepoint).
    """
    from tests.testbench.chat_messages import make_message, SOURCE_MANUAL
    from tests.testbench.pipeline.messages_writer import append_message
    now = datetime.now(timezone.utc)
    for i, s in enumerate(specs):
        # Spread timestamps 1s apart so append_message's monotonic check
        # never has to coerce (would introduce a "timestamp_coerced"
        # warning that could confuse future debug).
        ts = now.replace(microsecond=i * 1000)
        msg = make_message(
            role=s["role"],
            content=s.get("content", ""),
            timestamp=ts,
            source=s.get("source", SOURCE_MANUAL),
        )
        append_message(session, msg, on_violation="coerce")


def _recent_json_path(character_name: str) -> Path:
    from utils.config_manager import get_config_manager
    cm = get_config_manager()
    return Path(str(cm.memory_dir)) / character_name / "recent.json"


def _read_recent(character_name: str) -> list[dict[str, Any]]:
    p = _recent_json_path(character_name)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    assert isinstance(data, list), f"{p} top-level is not list: {type(data).__name__}"
    return data


def _call_import(client, mode: str | None = None) -> Any:
    body: dict[str, Any] = {}
    if mode is not None:
        body["mode"] = mode
    return client.post(
        "/api/memory/recent/import_from_session",
        json=body,
    )


# ── Cases ───────────────────────────────────────────────────────────────


def check_r6a_happy_append(client) -> list[str]:
    """R6A: 3-message session → append → recent.json has 3 canonical
    entries; second POST doubles the count (no dedup).
    """
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "r6a_append")
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        _seed_messages(session, [
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": "hello master"},
            {"role": "user", "content": "how are you"},
        ])

        # First import.
        r = _call_import(client, "append")
        _check(r.status_code == 200, "R6A.status1",
               f"{r.status_code} {r.text[:200]}")
        data = r.json()
        _check(data.get("mode") == "append", "R6A.mode1",
               f"mode={data.get('mode')!r}")
        _check(data.get("added") == 3, "R6A.added1",
               f"added={data.get('added')!r}")
        _check(data.get("existing") == 0, "R6A.existing1",
               f"existing={data.get('existing')!r}")
        _check(data.get("total") == 3, "R6A.total1",
               f"total={data.get('total')!r}")

        on_disk = _read_recent("NEKO")
        _check(len(on_disk) == 3, "R6A.disk_len1", f"len={len(on_disk)}")
        _check(
            on_disk[0].get("type") == "human"
            and on_disk[0].get("data", {}).get("content") == "hi there",
            "R6A.shape_head",
            f"head={on_disk[0]!r}",
        )
        _check(
            on_disk[1].get("type") == "ai"
            and on_disk[1].get("data", {}).get("content") == "hello master",
            "R6A.shape_mid",
            f"mid={on_disk[1]!r}",
        )

        # Second import — append semantics: no dedup, count doubles.
        r = _call_import(client, "append")
        _check(r.status_code == 200, "R6A.status2", f"{r.status_code}")
        data = r.json()
        _check(data.get("added") == 3, "R6A.added2",
               f"added={data.get('added')!r}")
        _check(data.get("existing") == 3, "R6A.existing2",
               f"existing={data.get('existing')!r}")
        _check(data.get("total") == 6, "R6A.total2",
               f"total={data.get('total')!r}")
        on_disk = _read_recent("NEKO")
        _check(len(on_disk) == 6, "R6A.disk_len2", f"len={len(on_disk)}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6A.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    return errors


def check_r6b_happy_replace(client) -> list[str]:
    """R6B: fresh session of 2 messages, mode=replace → overwrites."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "r6b_replace")
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()

        # First: seed 3 messages + append so recent.json is populated.
        _seed_messages(session, [
            {"role": "user", "content": "first run A"},
            {"role": "assistant", "content": "first run B"},
            {"role": "user", "content": "first run C"},
        ])
        r = _call_import(client, "append")
        _check(r.status_code == 200, "R6B.seed_status", f"{r.status_code}")
        _check(len(_read_recent("NEKO")) == 3, "R6B.seed_len",
               f"{len(_read_recent('NEKO'))}")

        # Now wipe session.messages (like tester pressed 清空), seed 2 new.
        session.messages = []
        _seed_messages(session, [
            {"role": "user", "content": "new A"},
            {"role": "assistant", "content": "new B"},
        ])
        r = _call_import(client, "replace")
        _check(r.status_code == 200, "R6B.status", f"{r.status_code}")
        data = r.json()
        _check(data.get("mode") == "replace", "R6B.mode",
               f"mode={data.get('mode')!r}")
        _check(data.get("added") == 2, "R6B.added",
               f"added={data.get('added')!r}")
        _check(data.get("existing") == 0, "R6B.existing",
               f"existing={data.get('existing')!r}")
        _check(data.get("total") == 2, "R6B.total",
               f"total={data.get('total')!r}")
        on_disk = _read_recent("NEKO")
        _check(len(on_disk) == 2, "R6B.disk_len", f"len={len(on_disk)}")
        _check(
            on_disk[0].get("data", {}).get("content") == "new A"
            and on_disk[1].get("data", {}).get("content") == "new B",
            "R6B.contents",
            f"on_disk={on_disk!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6B.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    return errors


def check_r6c_banner_filter(client) -> list[str]:
    """R6C: external_event_banner gets filtered, real messages are kept."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "r6c_banner")
        from tests.testbench.chat_messages import SOURCE_EXTERNAL_EVENT_BANNER
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        _seed_messages(session, [
            {"role": "user", "content": "real user line"},
            {"role": "system",
             "content": "[测试事件] 测试用户触发了一次 Agent 回调事件",
             "source": SOURCE_EXTERNAL_EVENT_BANNER},
            {"role": "assistant", "content": "real ai line"},
        ])

        r = _call_import(client, "append")
        _check(r.status_code == 200, "R6C.status", f"{r.status_code}")
        data = r.json()
        _check(data.get("added") == 2, "R6C.added",
               f"added={data.get('added')!r}")
        skipped = data.get("skipped", {})
        _check(skipped.get("banner") == 1, "R6C.skipped_banner",
               f"skipped={skipped!r}")

        on_disk = _read_recent("NEKO")
        _check(len(on_disk) == 2, "R6C.disk_len", f"len={len(on_disk)}")
        contents = [e.get("data", {}).get("content") for e in on_disk]
        _check(
            "测试用户触发了一次 Agent 回调事件" not in " ".join(
                str(c) for c in contents),
            "R6C.no_banner_content",
            f"banner leaked into recent.json: {contents!r}",
        )
        _check(
            contents == ["real user line", "real ai line"],
            "R6C.real_content",
            f"contents={contents!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6C.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    return errors


def check_r6d_empty_filter(client) -> list[str]:
    """R6D: empty string + whitespace-only are filtered."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "r6d_empty")
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        _seed_messages(session, [
            {"role": "user", "content": "real message"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "   \t\n  "},
        ])

        r = _call_import(client, "append")
        _check(r.status_code == 200, "R6D.status", f"{r.status_code}")
        data = r.json()
        _check(data.get("added") == 1, "R6D.added",
               f"added={data.get('added')!r}")
        skipped = data.get("skipped", {})
        _check(skipped.get("empty_content") == 2, "R6D.skipped_empty",
               f"skipped={skipped!r}")
        on_disk = _read_recent("NEKO")
        _check(len(on_disk) == 1, "R6D.disk_len", f"len={len(on_disk)}")
        _check(
            on_disk[0].get("data", {}).get("content") == "real message",
            "R6D.content",
            f"on_disk={on_disk!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6D.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    return errors


def check_r6e_round_trip(client) -> list[str]:
    """R6E: written entries must round-trip through messages_from_dict."""
    errors: list[str] = []
    try:
        _delete_session(client)
        _create_session(client, "r6e_rt")
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        _seed_messages(session, [
            {"role": "user", "content": "rt user"},
            {"role": "assistant", "content": "rt ai"},
            {"role": "system", "content": "rt sys note"},
        ])

        r = _call_import(client, "replace")
        _check(r.status_code == 200, "R6E.status", f"{r.status_code}")
        on_disk = _read_recent("NEKO")

        from utils.llm_client import (
            AIMessage, HumanMessage, SystemMessage, messages_from_dict,
        )
        rehydrated = messages_from_dict(on_disk)
        _check(len(rehydrated) == 3, "R6E.len", f"{len(rehydrated)}")
        _check(
            isinstance(rehydrated[0], HumanMessage)
            and rehydrated[0].content == "rt user",
            "R6E.human",
            f"{type(rehydrated[0]).__name__}={rehydrated[0].content!r}",
        )
        _check(
            isinstance(rehydrated[1], AIMessage)
            and rehydrated[1].content == "rt ai",
            "R6E.ai",
            f"{type(rehydrated[1]).__name__}={rehydrated[1].content!r}",
        )
        _check(
            isinstance(rehydrated[2], SystemMessage)
            and rehydrated[2].content == "rt sys note",
            "R6E.system",
            f"{type(rehydrated[2]).__name__}={rehydrated[2].content!r}",
        )
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6E.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
    return errors


def check_r6f_error_mapping(client) -> list[str]:
    """R6F: 404 no-session, 400 invalid mode, 409 no-messages."""
    errors: list[str] = []
    try:
        # 404 — no session.
        _delete_session(client)
        r = _call_import(client, "append")
        _check(r.status_code == 404, "R6F.no_session_status",
               f"{r.status_code} {r.text[:200]}")

        # 400 — invalid mode (needs an active session with real messages
        # to reach the validator — the validator is the FIRST line in the
        # handler, so session state doesn't actually matter, but we make
        # it a fresh session to be safe).
        _create_session(client, "r6f_invalid_mode")
        r = _call_import(client, "archive")
        _check(r.status_code == 400, "R6F.bad_mode_status",
               f"{r.status_code} {r.text[:200]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "InvalidMode", "R6F.bad_mode_type",
               f"error_type={err_type!r}, body={r.text[:200]!r}")

        # 409 — session with only banner / empty content.
        _delete_session(client)
        _create_session(client, "r6f_no_msgs")
        from tests.testbench.chat_messages import SOURCE_EXTERNAL_EVENT_BANNER
        from tests.testbench.session_store import get_session_store
        session = get_session_store().require()
        _seed_messages(session, [
            {"role": "system", "content": "[测试事件] banner only",
             "source": SOURCE_EXTERNAL_EVENT_BANNER},
            {"role": "user", "content": "   "},
        ])
        r = _call_import(client, "append")
        _check(r.status_code == 409, "R6F.no_msgs_status",
               f"{r.status_code} {r.text[:200]}")
        detail = (r.json() or {}).get("detail", {})
        err_type = detail.get("error_type") if isinstance(detail, dict) else None
        _check(err_type == "NoMessagesToImport", "R6F.no_msgs_type",
               f"error_type={err_type!r}")
    except _AssertFail as exc:
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        import traceback
        errors.append(
            f"[R6F.crash] {type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        )
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
    print(" P25 Day 2 polish r6 — import_recent_from_session smoke")
    print("=" * 66)

    _setup_env()

    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    total = 0
    total += _report(
        "R6A — happy append (3 msgs × 2 calls → 6 on disk)",
        check_r6a_happy_append(client),
    )
    total += _report(
        "R6B — happy replace (overwrites prior entries)",
        check_r6b_happy_replace(client),
    )
    total += _report(
        "R6C — external_event_banner filter (banner never lands)",
        check_r6c_banner_filter(client),
    )
    total += _report(
        "R6D — empty / whitespace content filter",
        check_r6d_empty_filter(client),
    )
    total += _report(
        "R6E — round-trip through messages_from_dict",
        check_r6e_round_trip(client),
    )
    total += _report(
        "R6F — error mapping (404 / 400 / 409)",
        check_r6f_error_mapping(client),
    )

    _delete_session(client)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) in import_recent smoke.")
        return 1
    print(" [PASS] import_recent_from_session contract holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
