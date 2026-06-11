"""P25 Day 2 polish r7 — wire-display semantic partition smoke.

Purpose
-------
r7 broke the monolithic "one wire panel = last LLM call" contract into
**domain-partitioned** preview surfaces:

* **Chat page Preview Panel** — only shows wires from *conversational*
  LLM calls (``chat.send`` / ``auto_dialog_target`` / three external-
  event sources). Memory / judge / simuser wires do **not** surface
  here anymore.
* **Memory sub-pages** — each op ([recent.compress] / [facts.extract]
  / [reflect] / [persona.resolve_corrections]) grows a [预览 prompt]
  button next to its Dry-run button, backed by
  ``POST /api/memory/prompt_preview/{op}``.
* **Evaluation / Run page** — [预览 prompt] next to [运行评分], backed
  by ``POST /api/judge/run_prompt_preview``.
* **Simulated user** — wires are ``NOSTAMP(wire_tracker)``; never show
  up anywhere.

This smoke **statically** locks the invariants that underpin the above
partitioning. It's intentionally static (no live server) so that it
catches regressions that slip past dynamic smokes when a dev flips a
flag in one place but forgets the other.

Contracts
---------
    R7.A  ``wire_tracker.KNOWN_SOURCES`` does not contain
          ``simulated_user`` / ``auto_dialog_simuser``. (Their stamp
          is gone; leaving them in the whitelist would be a stale
          declaration and would let a future regression silently
          re-enable SimUser stamping.)

    R7.B  ``simulated_user.generate_simuser_message`` does **not**
          call ``record_last_llm_wire`` or
          ``update_last_llm_wire_reply``. Uses a source-text regex
          check: cheap, and self-documenting when it fails.

    R7.C  ``simulated_user.generate_simuser_message`` does **not**
          take a ``wire_source`` kwarg. (Caught at AST level to fail
          the smoke if a refactor accidentally re-adds it.)

    R7.D  ``auto_dialog.py`` does not pass ``wire_source=`` to
          ``generate_simuser_message`` call sites.

    R7.E  Chat ``preview_panel.js`` CHAT_VISIBLE_SOURCES does NOT
          include ``memory.llm`` / ``judge.llm`` / ``simulated_user``
          / ``auto_dialog_simuser``. Chat panel is chat-only.

    R7.F  Memory router exposes ``/prompt_preview/{op}`` endpoint.

    R7.G  Judge router exposes ``/run_prompt_preview`` endpoint.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p25_r7_wire_partition_smoke.py

Exits non-zero on any violation.
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path


if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


REPO_ROOT = Path(__file__).resolve().parents[3]
TESTBENCH_ROOT = REPO_ROOT / "tests" / "testbench"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── R7.A — KNOWN_SOURCES minus simuser ──────────────────────────────

def check_known_sources_no_simuser() -> list[str]:
    from tests.testbench.pipeline.wire_tracker import KNOWN_SOURCES

    errors: list[str] = []
    banned = {"simulated_user", "auto_dialog_simuser"}
    leaked = banned & set(KNOWN_SOURCES)
    if leaked:
        errors.append(
            f"[R7.A] wire_tracker.KNOWN_SOURCES still contains "
            f"{sorted(leaked)!r}; r7 removed SimUser stamping. Delete "
            f"from KNOWN_SOURCES."
        )
    return errors


# ── R7.B — simuser does not call record_last_llm_wire ──────────────

def check_simuser_no_record() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "pipeline" / "simulated_user.py"
    if not path.exists():
        return [f"[R7.B] simulated_user.py missing at {path}"]

    source = _read(path)
    if re.search(r"\brecord_last_llm_wire\s*\(", source):
        errors.append(
            "[R7.B] simulated_user.py still calls record_last_llm_wire; "
            "r7 marked the SimUser LLM call NOSTAMP(wire_tracker). "
            "Remove the stamp and add the NOSTAMP sentinel."
        )
    if re.search(r"\bupdate_last_llm_wire_reply\s*\(", source):
        errors.append(
            "[R7.B] simulated_user.py still calls "
            "update_last_llm_wire_reply; remove it (r7 no-stamp policy)."
        )
    if "NOSTAMP(wire_tracker)" not in source:
        errors.append(
            "[R7.B] simulated_user.py lacks the NOSTAMP(wire_tracker) "
            "sentinel; the stamp-coverage smoke will flag the LLM call "
            "site as unstamped."
        )
    return errors


# ── R7.C — generate_simuser_message signature has no wire_source ───

def check_simuser_no_wire_source_param() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "pipeline" / "simulated_user.py"
    if not path.exists():
        return errors

    tree = ast.parse(_read(path), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name != "generate_simuser_message":
            continue
        args = node.args
        all_args = list(args.args) + list(args.kwonlyargs) + list(args.posonlyargs)
        for a in all_args:
            if a.arg == "wire_source":
                errors.append(
                    f"[R7.C] generate_simuser_message still accepts a "
                    f"'wire_source' parameter (line {node.lineno}); "
                    f"r7 removed SimUser stamping."
                )
    return errors


# ── R7.D — auto_dialog.py doesn't pass wire_source= ────────────────

def check_auto_dialog_no_wire_source_kwarg() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "pipeline" / "auto_dialog.py"
    if not path.exists():
        return errors

    tree = ast.parse(_read(path), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = None
        if isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        elif isinstance(node.func, ast.Name):
            callee = node.func.id
        if callee != "generate_simuser_message":
            continue
        for kw in node.keywords or []:
            if kw.arg == "wire_source":
                errors.append(
                    f"[R7.D] auto_dialog.py:{node.lineno} still passes "
                    f"wire_source= to generate_simuser_message; remove "
                    f"(r7)."
                )
    return errors


# ── R7.E — preview_panel.js CHAT_VISIBLE_SOURCES excludes memory/judge/simuser

def check_preview_panel_chat_only() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "static" / "ui" / "chat" / "preview_panel.js"
    if not path.exists():
        return [f"[R7.E] preview_panel.js missing at {path}"]

    source = _read(path)
    # Find the Set declaration. Accept multi-line, anchor on the Set
    # body up to the closing ``]);``.
    m = re.search(
        r"const\s+CHAT_VISIBLE_SOURCES\s*=\s*new\s+Set\s*\(\s*\[(?P<body>[^\]]*)\]\s*\)",
        source,
        flags=re.DOTALL,
    )
    if not m:
        errors.append(
            "[R7.E] preview_panel.js does not declare "
            "`CHAT_VISIBLE_SOURCES = new Set([...])`; chat-only "
            "filtering must stay in place (r7)."
        )
        return errors
    body = m.group("body")
    banned = ("memory.llm", "judge.llm", "simulated_user", "auto_dialog_simuser")
    for slug in banned:
        # Check for the slug as a quoted literal ('memory.llm' or "memory.llm")
        pattern = r"['\"]" + re.escape(slug) + r"['\"]"
        if re.search(pattern, body):
            errors.append(
                f"[R7.E] preview_panel.js CHAT_VISIBLE_SOURCES contains "
                f"{slug!r}; Chat page Preview Panel must not surface "
                f"non-conversational wires (r7)."
            )
    return errors


# ── R7.F — memory_router has /prompt_preview/{op} ─────────────────

def check_memory_router_prompt_preview() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "memory_router.py"
    source = _read(path)
    if not re.search(r"""@router\.post\(\s*["']\/prompt_preview\/\{op\}["']""",
                     source):
        errors.append(
            "[R7.F] memory_router.py missing "
            "@router.post('/prompt_preview/{op}') — Memory sub-pages' "
            "[预览 prompt] button needs this endpoint."
        )
    return errors


# ── R7.G — judge_router has /run_prompt_preview ───────────────────

def check_judge_router_prompt_preview() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "judge_router.py"
    source = _read(path)
    if not re.search(r"""@router\.post\(\s*["']\/run_prompt_preview["']""",
                     source):
        errors.append(
            "[R7.G] judge_router.py missing "
            "@router.post('/run_prompt_preview') — Evaluation/Run page "
            "[预览 prompt] button needs this endpoint."
        )
    return errors


# ── entry point ───────────────────────────────────────────────────

CHECKS = (
    ("R7.A — KNOWN_SOURCES no simuser", check_known_sources_no_simuser),
    ("R7.B — simuser not stamped", check_simuser_no_record),
    ("R7.C — no wire_source param", check_simuser_no_wire_source_param),
    ("R7.D — auto_dialog no wire_source kwarg", check_auto_dialog_no_wire_source_kwarg),
    ("R7.E — chat panel chat-only", check_preview_panel_chat_only),
    ("R7.F — memory prompt_preview endpoint", check_memory_router_prompt_preview),
    ("R7.G — judge run_prompt_preview endpoint", check_judge_router_prompt_preview),
)


def main() -> int:
    print("[p25_r7_wire_partition_smoke] r7 semantic-partition invariants")
    print(f"  REPO_ROOT = {REPO_ROOT}")
    total_violations = 0
    for name, fn in CHECKS:
        try:
            errs = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name}] CRASHED: {type(exc).__name__}: {exc}")
            total_violations += 1
            continue
        status = "OK" if not errs else f"FAIL ({len(errs)})"
        print(f"  [{name}] {status}")
        for e in errs:
            print(f"     - {e}")
        total_violations += len(errs)

    if total_violations:
        print(f"FAIL {total_violations} violation(s)")
        return 1
    print("OK all r7 wire-partition contracts hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
