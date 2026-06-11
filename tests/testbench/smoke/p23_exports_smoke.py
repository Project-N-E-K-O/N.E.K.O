"""P23 session export smoke — backend TestClient.

Run after touching ``pipeline/session_export.py`` or ``session_router``
export endpoints::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p23_exports_smoke.py

Exits non-zero on any assertion failure. No LLM calls (export is a
read-only transformation over in-memory session state + disk sandbox).

Covers:

  1. No active session → ``POST /api/session/export`` returns 404.
  2. All **11 valid (scope, format) combinations** return 200 with the
     right ``Content-Type`` + ``Content-Disposition: attachment``.
  3. **Filename contract**: ``tbsession_<safe_name>_<scope>_<ts>.<ext>``
     for normal exports, ``tbscript_<safe_name>_<ts>.json`` for
     ``format=dialog_template``; the safe-name filter keeps the
     archive loadable on Windows (no CJK / spaces / colons).
  4. **API key redaction is unconditional**. Even though the session
     has ``chat.api_key = "sk-LEAK"`` we assert the substring never
     appears in any exported body across all scopes/formats.
  5. **JSON envelope shape**: ``kind`` / ``scope`` / ``format`` /
     ``generated_at`` / ``session_ref`` / ``payload`` for every
     scope's JSON output.
  6. **dialog_template invariants**: validates against
     :func:`script_runner._normalize_template` — name/turns present,
     user turns carry ``time.advance`` derived from the ≥60s deltas
     we seeded, assistant turns carry ``expected``.
  7. **Markdown (conversation_evaluations)** contains the "By schema"
     header + the comparative gap-trajectory table (proof we reused
     :func:`judge_export.build_report_markdown`).
  8. **Illegal combinations** → 400 ``InvalidCombination``: every
     non-conversation scope × ``dialog_template``, plus unknown scope
     / unknown format. Also ``scope=evaluations + dialog_template``
     (the spec's canonical negative case).
  9. **include_memory**: ``scope=full + format=json + include_memory=True``
     places a base64 ``memory_tarball_b64`` + ``memory_sha256`` into
     the envelope; silently omitted for non-supported combinations.
 10. **Round-trip**: the ``scope=full + format=json + include_memory=True``
     envelope mirrors :func:`persistence.export_to_payload` well enough
     that the ``payload`` + ``memory_tarball_b64`` substructure can be
     fed into ``/api/session/import`` after a small shape adapter
     (exercised via ``persistence.import_from_payload``).
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


FILENAME_REGEX_NORMAL = re.compile(
    r"^tbsession_[A-Za-z0-9._-]+_[A-Za-z0-9._-]+_\d{8}_\d{6}\.(json|md)$",
)
FILENAME_REGEX_DIALOG = re.compile(
    r"^tbscript_[A-Za-z0-9._-]+_\d{8}_\d{6}\.json$",
)

# ── fixture helpers ─────────────────────────────────────────────────


def _setup_env() -> Path:
    """Point all testbench data roots at a temp dir before imports."""
    tmp_data = Path(tempfile.mkdtemp(prefix="p23_smoke_"))
    os.environ["TESTBENCH_DATA_DIR"] = str(tmp_data)
    from tests.testbench import config as tb_config
    tb_config.DATA_DIR = tmp_data
    tb_config.SAVED_SESSIONS_DIR = tmp_data / "saved_sessions"
    tb_config.AUTOSAVE_DIR = tmp_data / "saved_sessions" / "_autosave"
    tb_config.LOGS_DIR = tmp_data / "logs"
    tb_config.SANDBOXES_DIR = tmp_data / "sandboxes"
    tb_config.EXPORTS_DIR = tmp_data / "exports"
    for d in [
        tb_config.SAVED_SESSIONS_DIR,
        tb_config.AUTOSAVE_DIR,
        tb_config.LOGS_DIR,
        tb_config.SANDBOXES_DIR,
        tb_config.EXPORTS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
    return tmp_data


def _seed_session() -> "object":
    """Create a session and populate messages + persona + eval_results.

    Messages are spaced 2 hours apart to produce non-trivial
    ``time.advance`` values in the ``dialog_template`` export. The
    ``chat.api_key`` carries a canary substring we assert never leaks.
    """
    from tests.testbench.session_store import get_session_store
    from tests.testbench.chat_messages import make_message

    session = get_session_store().require()
    base_ts = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)
    # user → assistant → user (2h gap) → assistant pattern: exercises
    # both "first user turn (no time field)" and "subsequent user turn
    # carries time.advance" branches in build_dialog_template.
    deltas_min = [0, 1, 121, 122]
    roles = ["user", "assistant", "user", "assistant"]
    contents = [
        "早上好, 今天天气真好.",
        "早上好 master! 是的, 阳光明媚.",
        "我们下午去散步吧?",
        "好的! 我想去公园看花.",
    ]
    for role, d, content in zip(roles, deltas_min, contents):
        ts = base_ts + timedelta(minutes=d)
        session.messages.append(
            make_message(
                role=role,
                content=content,
                timestamp=ts.isoformat(timespec="seconds"),
            ),
        )
    session.persona = {
        "master_name": "Master",
        "character_name": "NEKO",
        "language": "zh",
        "system_prompt": "You are NEKO, a cheerful cat-girl assistant.",
    }
    session.model_config = {
        "chat": {
            "provider": "openai",
            "api_key": "sk-LEAK-CANARY-SHOULD-NEVER-APPEAR",
            "model": "gpt-4o",
        },
        "judge": {
            "provider": "openai",
            "api_key": "",
            "model": "gpt-4o",
        },
    }
    # Two comparative eval_results so the aggregate Markdown has a
    # "By schema" + "Gap trajectory" block we can grep against.
    session.eval_results.extend([
        {
            "id": "eval-001",
            "created_at": "2026-04-21T10:30:00+00:00",
            "schema_id": "human_like_v1",
            "mode": "comparative",
            "granularity": "single",
            "scores": {
                "a": {"empathy": 8.0, "fluency": 7.0, "overall_score": 75.0},
                "b": {"empathy": 6.0, "fluency": 7.5, "overall_score": 68.0},
                "overall_a": 75.0,
                "overall_b": 68.0,
            },
            "gap": 7.0,
            "verdict": "A_better",
            "passed": True,
            "duration_ms": 1234,
            "problem_patterns": ["too_robotic"],
            "schema_snapshot": {
                "dimensions": [
                    {"key": "empathy", "label": "共情"},
                    {"key": "fluency", "label": "流畅度"},
                ],
            },
        },
        {
            "id": "eval-002",
            "created_at": "2026-04-21T12:45:00+00:00",
            "schema_id": "human_like_v1",
            "mode": "comparative",
            "granularity": "single",
            "scores": {
                "a": {"empathy": 9.0, "fluency": 8.0, "overall_score": 82.0},
                "b": {"empathy": 8.0, "fluency": 8.0, "overall_score": 78.0},
                "overall_a": 82.0,
                "overall_b": 78.0,
            },
            "gap": 4.0,
            "verdict": "A_better",
            "passed": True,
            "duration_ms": 1100,
            "problem_patterns": [],
            "schema_snapshot": {
                "dimensions": [
                    {"key": "empathy", "label": "共情"},
                    {"key": "fluency", "label": "流畅度"},
                ],
            },
        },
    ])
    return session


# ── 1. 404 on missing session ───────────────────────────────────────


def check_no_session(client) -> None:
    r = client.post(
        "/api/session/export",
        json={"scope": "full", "format": "json"},
    )
    assert r.status_code == 404, r.text
    print("P23: no-session export returns 404")


# ── 2–5. Valid combinations sweep ───────────────────────────────────


VALID_COMBINATIONS = [
    ("full", "json", "application/json"),
    ("full", "markdown", "text/markdown"),
    ("persona_memory", "json", "application/json"),
    ("persona_memory", "markdown", "text/markdown"),
    ("conversation", "json", "application/json"),
    ("conversation", "markdown", "text/markdown"),
    ("conversation", "dialog_template", "application/json"),
    ("conversation_evaluations", "json", "application/json"),
    ("conversation_evaluations", "markdown", "text/markdown"),
    ("evaluations", "json", "application/json"),
    ("evaluations", "markdown", "text/markdown"),
]

CANARY = "sk-LEAK-CANARY-SHOULD-NEVER-APPEAR"


def check_all_valid_combinations(client) -> dict[tuple[str, str], dict]:
    """Hit every (scope, format) combo and collect (body, headers) map."""
    results: dict[tuple[str, str], dict] = {}
    for scope, fmt, ct_prefix in VALID_COMBINATIONS:
        r = client.post(
            "/api/session/export",
            json={"scope": scope, "format": fmt},
        )
        assert r.status_code == 200, (
            f"scope={scope} format={fmt} failed: status={r.status_code} "
            f"body={r.text[:500]}"
        )
        ct = r.headers.get("content-type", "")
        assert ct.startswith(ct_prefix), (
            f"scope={scope} format={fmt}: expected content-type starting with "
            f"{ct_prefix!r}, got {ct!r}"
        )
        cd = r.headers.get("content-disposition", "")
        assert cd.startswith("attachment;"), (
            f"scope={scope} format={fmt}: missing Content-Disposition attachment "
            f"({cd!r})"
        )
        m = re.search(r'filename="([^"]+)"', cd)
        assert m, f"no filename in Content-Disposition: {cd!r}"
        filename = m.group(1)
        if fmt == "dialog_template":
            assert FILENAME_REGEX_DIALOG.match(filename), (
                f"dialog_template filename {filename!r} does not match regex"
            )
        else:
            assert FILENAME_REGEX_NORMAL.match(filename), (
                f"normal filename {filename!r} does not match regex"
            )
        body_bytes = r.content
        body_text = body_bytes.decode("utf-8")
        # API key redaction is unconditional across every scope/format.
        assert CANARY not in body_text, (
            f"scope={scope} format={fmt}: api_key canary LEAKED into export body"
        )
        results[(scope, fmt)] = {
            "body_text": body_text,
            "filename": filename,
            "content_type": ct,
        }
        print(
            f"P23: scope={scope:<26s} format={fmt:<16s} "
            f"→ 200 {len(body_text):>6d}B file={filename}",
        )
    return results


# ── 6. JSON envelope shape ─────────────────────────────────────────


def check_json_envelopes(results) -> None:
    for scope, fmt, _ in VALID_COMBINATIONS:
        if fmt != "json":
            continue
        body = json.loads(results[(scope, fmt)]["body_text"])
        assert body.get("kind") == "testbench_session_export", body
        assert body.get("scope") == scope, body
        assert body.get("format") == "json", body
        assert body.get("schema_version") == 1, body
        assert "generated_at" in body, body
        ref = body.get("session_ref") or {}
        assert ref.get("session_name") == "p23_smoke", ref
        assert ref.get("message_count") == 4, ref
        assert ref.get("eval_count") == 2, ref
        assert "payload" in body, body
        payload = body["payload"]
        if scope == "full":
            # Full payload mirrors SessionArchive.to_json_dict shape.
            assert payload.get("archive_kind") == "testbench_session", payload
            assert payload.get("session", {}).get("messages"), payload
        elif scope == "persona_memory":
            assert "persona" in payload and "model_config" in payload, payload
            assert "messages" not in payload, (
                "persona_memory scope must NOT embed messages"
            )
        elif scope == "conversation":
            assert payload.get("messages"), payload
            assert "eval_results" not in payload, payload
        elif scope == "conversation_evaluations":
            assert payload.get("messages"), payload
            assert payload.get("eval_results"), payload
            assert payload.get("aggregate"), payload
        elif scope == "evaluations":
            assert payload.get("eval_results"), payload
            assert payload.get("aggregate"), payload
            assert "messages" not in payload, (
                "evaluations scope must NOT embed messages"
            )
    print("P23: all JSON envelopes have correct kind/scope/format/session_ref")


# ── 7. dialog_template invariants ──────────────────────────────────


def check_dialog_template(results) -> None:
    body = results[("conversation", "dialog_template")]["body_text"]
    tpl = json.loads(body)
    assert tpl.get("name"), tpl
    assert isinstance(tpl.get("turns"), list), tpl
    turns = tpl["turns"]
    assert len(turns) == 4, f"expected 4 turns, got {len(turns)}: {turns}"
    # Sequence: user (no time) → assistant → user (time.advance ≈ 2h) → assistant
    assert turns[0]["role"] == "user", turns[0]
    assert turns[0].get("content"), turns[0]
    assert "time" not in turns[0], (
        "first user turn must omit time (bootstrap carries first anchor)"
    )
    assert turns[1]["role"] == "assistant", turns[1]
    assert turns[1].get("expected"), turns[1]
    assert turns[2]["role"] == "user", turns[2]
    # 2h gap between msg #1 (t=1min) and msg #2 (t=121min) = 7200s.
    t = turns[2].get("time") or {}
    advance = t.get("advance")
    assert advance == "2h", (
        f"expected time.advance='2h' for the 120-min gap, got {advance!r}"
    )
    assert turns[3]["role"] == "assistant", turns[3]
    # Validate against the real parser.
    from tests.testbench.pipeline import script_runner
    normalized = script_runner._normalize_template(
        tpl, source="smoke", path=Path("p23_dialog_template.json"),
    )
    assert normalized.get("name") == tpl["name"], normalized
    assert len(normalized.get("turns") or []) == 4, normalized
    print(
        "P23: dialog_template 4 turns, 2h advance correct, parses through "
        "script_runner._normalize_template",
    )


# ── 8. Markdown conversation_evaluations content ───────────────────


def check_markdown_contents(results) -> None:
    md = results[("conversation_evaluations", "markdown")]["body_text"]
    # Header + Context block
    assert "# Testbench Session Export" in md, md[:400]
    assert "## Conversation" in md, md[:400]
    # Evaluations block reuses judge_export.build_report_markdown body,
    # which emits "## 按 schema 分组" and a "Gap 轨迹" sub-header for
    # comparative results. 2026-04-22 Day 8 #4: markdown 全文中文化, 断言
    # 跟着改到中文版标签.
    assert "按 schema 分组" in md, (
        "P23: conversation_evaluations Markdown should include 按 schema 分组 "
        "block from judge_export.build_report_markdown"
    )
    assert "Gap 轨迹" in md, (
        "P23: comparative Gap 轨迹 table missing from Markdown"
    )
    # Persona / clock sections always appear.
    assert "## Persona" in md, md[:400]
    # Messages rendered as numbered sections.
    for turn_text in [
        "早上好, 今天天气真好", "我们下午去散步吧",
    ]:
        assert turn_text in md, f"message content {turn_text!r} not in markdown"
    print(
        "P23: conversation_evaluations Markdown includes Persona + Messages + "
        "By schema + Gap trajectory",
    )


# ── 9. Illegal combinations (400) ──────────────────────────────────


ILLEGAL_COMBOS = [
    # dialog_template only applies to conversation
    ("full", "dialog_template"),
    ("persona_memory", "dialog_template"),
    ("conversation_evaluations", "dialog_template"),
    ("evaluations", "dialog_template"),
]


def check_illegal_combinations(client) -> None:
    for scope, fmt in ILLEGAL_COMBOS:
        r = client.post(
            "/api/session/export",
            json={"scope": scope, "format": fmt},
        )
        assert r.status_code == 400, (
            f"scope={scope} format={fmt}: expected 400, got {r.status_code} "
            f"{r.text[:300]}"
        )
        detail = r.json().get("detail") or {}
        assert detail.get("error_type") == "InvalidCombination", detail
    # Unknown scope.
    r = client.post(
        "/api/session/export",
        json={"scope": "not_a_scope", "format": "json"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_type"] == "UnknownScope", r.json()
    # Unknown format.
    r = client.post(
        "/api/session/export",
        json={"scope": "full", "format": "pdf"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error_type"] == "UnknownFormat", r.json()
    print(
        "P23: 4 illegal (scope,dialog_template) combos + unknown scope / "
        "format all 400",
    )


# ── 10. include_memory behaviour ───────────────────────────────────


def check_include_memory(client) -> None:
    # full + json + include_memory=True → envelope has base64 tarball.
    r = client.post(
        "/api/session/export",
        json={"scope": "full", "format": "json", "include_memory": True},
    )
    assert r.status_code == 200, r.text
    env = json.loads(r.text)
    assert env.get("memory_tarball_b64"), (
        "include_memory=True + full + json must embed memory_tarball_b64"
    )
    assert env.get("memory_sha256"), env.keys()
    # Decodes to valid bytes.
    tar_bytes = base64.b64decode(env["memory_tarball_b64"])
    assert len(tar_bytes) > 0, "tarball bytes should be non-empty"
    # SHA matches (hash was computed by persistence.compute_memory_sha256).
    import hashlib
    assert (
        hashlib.sha256(tar_bytes).hexdigest() == env["memory_sha256"]
    ), "memory_sha256 in envelope does not match base64 body"

    # persona_memory + json + include_memory=True → also embedded.
    r = client.post(
        "/api/session/export",
        json={
            "scope": "persona_memory",
            "format": "json",
            "include_memory": True,
        },
    )
    assert r.status_code == 200, r.text
    env2 = json.loads(r.text)
    assert env2.get("memory_tarball_b64"), (
        "persona_memory + json should also honour include_memory"
    )

    # conversation + json + include_memory=True → silently ignored
    # (no memory_tarball_b64 key in envelope).
    r = client.post(
        "/api/session/export",
        json={
            "scope": "conversation",
            "format": "json",
            "include_memory": True,
        },
    )
    assert r.status_code == 200, r.text
    env3 = json.loads(r.text)
    assert "memory_tarball_b64" not in env3, (
        "include_memory must be silently ignored for conversation scope"
    )

    # markdown + include_memory=True → ignored (Markdown has no envelope).
    r = client.post(
        "/api/session/export",
        json={
            "scope": "full",
            "format": "markdown",
            "include_memory": True,
        },
    )
    assert r.status_code == 200, r.text
    assert "memory_tarball_b64" not in r.text, (
        "Markdown export must not inline base64 tarball"
    )
    print(
        "P23: include_memory embeds tarball only for (full|persona_memory) "
        "+ json; silently ignored elsewhere",
    )


# ── 11. Filename sanitisation ─────────────────────────────────────


def check_filename_sanitisation() -> None:
    from tests.testbench.pipeline import session_export
    # Names with CJK / spaces / slashes get stripped to ascii-safe tokens.
    bad_names = [
        "我的测试会话",            # all CJK → fallback 'session'
        "demo run 01",            # spaces
        "x/./../etc/passwd",      # traversal tokens
        "a:b|c",                  # windows-forbidden chars
        "",                       # empty
    ]
    now = datetime(2026, 4, 21, 9, 0, 0)
    for n in bad_names:
        fn = session_export.session_export_filename(
            session_name=n, scope="full", fmt="json", now=now,
        )
        assert FILENAME_REGEX_NORMAL.match(fn), (
            f"sanitised filename {fn!r} (from {n!r}) not ascii-safe"
        )
        # Ensure none of the tricky chars survived.
        for bad in "我 :/.|":
            # '.' is legal in our allowed set, so skip that one.
            if bad == ".":
                continue
            assert bad not in fn, f"forbidden char {bad!r} survived in {fn!r}"
    print("P23: filename sanitiser drops CJK / spaces / traversal / reserved chars")


# ── 12. Round-trip: scope=full + json + include_memory → import ────


def check_round_trip_import(client) -> None:
    """The ``full`` + ``json`` + ``include_memory`` envelope should be
    reducible to a ``persistence.import_from_payload`` payload with a
    trivial adapter (extract ``payload`` → ``archive`` + keep tarball).
    This protects the promise made in :mod:`session_export` module
    docstring's "export -> import 的往返性" note.
    """
    from tests.testbench.pipeline import persistence as p
    r = client.post(
        "/api/session/export",
        json={"scope": "full", "format": "json", "include_memory": True},
    )
    assert r.status_code == 200, r.text
    env = json.loads(r.text)
    # The `full` payload IS the SessionArchive.to_json_dict shape, so
    # import_from_payload can consume `{archive: <payload>, tarball_b64: ...}`.
    adapter = {
        "archive": env["payload"],
        "tarball_b64": env["memory_tarball_b64"],
    }
    saved_path = p.import_from_payload(
        adapter, name="p23_roundtrip", overwrite=True,
    )
    assert isinstance(saved_path, Path), saved_path
    assert saved_path.name == "p23_roundtrip.json", saved_path
    # Load it back — messages and eval_results survived.
    r = client.post("/api/session/load/p23_roundtrip")
    assert r.status_code == 200, r.text
    from tests.testbench.session_store import get_session_store
    reloaded = get_session_store().require()
    assert len(reloaded.messages) == 4, reloaded.messages
    assert len(reloaded.eval_results) == 2, reloaded.eval_results
    # api_key was redacted in export, so imported archive keeps redacted.
    assert reloaded.model_config["chat"]["api_key"] == "<redacted>", (
        reloaded.model_config
    )
    print("P23: full+json+include_memory export round-trips through import")


# ── main ───────────────────────────────────────────────────────────


def main() -> int:
    _setup_env()
    from fastapi.testclient import TestClient
    from tests.testbench.server import create_app

    app = create_app()
    client = TestClient(app)

    check_no_session(client)

    r = client.post("/api/session", json={"name": "p23_smoke"})
    assert r.status_code == 201, r.text
    _seed_session()

    results = check_all_valid_combinations(client)
    check_json_envelopes(results)
    check_dialog_template(results)
    check_markdown_contents(results)
    check_illegal_combinations(client)
    check_include_memory(client)
    check_filename_sanitisation()
    check_round_trip_import(client)

    client.delete("/api/session")
    print("P23 EXPORTS SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
