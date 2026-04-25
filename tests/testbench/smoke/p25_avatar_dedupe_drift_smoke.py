"""P25 avatar-dedupe drift smoke — byte + behaviour equivalence between
``main_logic/cross_server.py`` and ``tests/testbench/pipeline/avatar_dedupe.py``.

Per LESSONS_LEARNED L30 ("external-system pure helper cross-package: copy +
drift smoke, not import"), the testbench does *not* ``import`` from
``main_logic`` (which carries ssl/aiohttp/event-bus side effects). Instead
``pipeline/avatar_dedupe.py`` holds a **byte-equivalent copy** of the upstream
``_should_persist_avatar_interaction_memory`` helper and its constant. This
file is the other half of the L30 contract: without it, the copy would
silently drift from the upstream source.

Rules enforced (any failure → exit 1 with a diagnostic message):

- **R1 — Sentinel presence**. ``avatar_dedupe.py`` must contain both the
  ``BEGIN COPY`` and ``END COPY`` sentinel comments, with ``BEGIN`` strictly
  before ``END``.
- **R2 — Constant literal equality**. Both files must contain a line
  matching ``AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = 8000`` (token
  equivalence, leading/trailing whitespace tolerated).
- **R3 — Byte hash equality (core)**. The constant line plus the full body
  of ``_should_persist_avatar_interaction_memory`` from
  ``cross_server.py`` must be byte-equivalent to the content between the
  sentinels in ``avatar_dedupe.py``, after the normalisation steps listed
  below. A SHA-256 over the normalised bytes must be identical on both
  sides; otherwise we print a unified diff and both hashes.

  Normalisation (applied identically to both sides):
    1. Line endings unified to ``\\n`` (all ``\\r\\n`` → ``\\n``).
    2. Leading and trailing whitespace stripped from every line.
    3. Empty lines discarded.
    4. **No semantic rewrite**: no variable renaming, no parenthesis
       flattening, no token reordering — that would stop being a byte
       hash.

- **R4 — Import equivalence**. ``AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS``
  and ``_should_persist_avatar_interaction_memory`` must both be
  importable from ``tests.testbench.pipeline.avatar_dedupe``, and the
  constant must equal ``8000``.
- **R5 — Behavioural equivalence (5 scenarios)**. We load the main-program
  function via ``importlib`` off a synthetic module (so ``main_logic``'s
  side-effect-heavy ``__init__`` is not triggered) and run five scenarios
  against both implementations, each with its own cache dict so the two
  sides do not collide. We compare return values and the resulting cache
  key sets; ``ts`` values will differ slightly because ``time.time()`` is
  called twice, that is intentional and not a drift signal.
- **R6 — No stray copies outside sentinels**. ``avatar_dedupe.py`` must
  not re-define ``AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS`` or
  ``_should_persist_avatar_interaction_memory`` after ``END COPY`` (a late
  "override version" would silently shadow the copy-protected one).

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p25_avatar_dedupe_drift_smoke.py

Exits 0 on all-clean, 1 on any rule violation.

Note: this file is the **only** testbench module allowed to touch
``main_logic/*``; every other pipeline / router / smoke must not.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import io
import re
import sys
import time
import types
from pathlib import Path

# Force utf-8 on stdout so unicode box-drawing / arrows don't crash on Windows GBK.
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_UPSTREAM_PATH = _PROJECT_ROOT / "main_logic" / "cross_server.py"
_COPY_PATH = _PROJECT_ROOT / "tests" / "testbench" / "pipeline" / "avatar_dedupe.py"

_SUCCESS_BANNER = "P25 AVATAR DEDUPE DRIFT SMOKE OK"

_CONSTANT_NAME = "AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS"
_CONSTANT_RE = re.compile(
    rf"^\s*{re.escape(_CONSTANT_NAME)}\s*=\s*8000\s*$"
)
_FUNC_NAME = "_should_persist_avatar_interaction_memory"

_BEGIN_SENTINEL = "BEGIN COPY"
_END_SENTINEL = "END COPY"


# ─────────────────────────────────────────────────────────────────────
# File reading helpers (all byte-level, \r\n stripped at the seam)
# ─────────────────────────────────────────────────────────────────────


def _read_text(path: Path) -> str:
    """Read the file as UTF-8 text, normalising line endings to ``\\n``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"[FAIL] source file missing: {path}")
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _normalise_block(text: str) -> str:
    """Apply R3 normalisation: strip per line, drop empty lines, join with ``\\n``."""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        out.append(stripped)
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────
# R1: sentinel presence
# ─────────────────────────────────────────────────────────────────────


def check_r1_sentinels(copy_text: str) -> tuple[bool, int, int, str]:
    """Return (ok, begin_idx, end_idx, msg).

    ``begin_idx`` / ``end_idx`` are character offsets, -1 if missing.
    ``ok`` requires both sentinels present AND ``begin_idx < end_idx``.
    """
    begin_idx = copy_text.find(_BEGIN_SENTINEL)
    end_idx = copy_text.find(_END_SENTINEL)
    if begin_idx < 0:
        return False, begin_idx, end_idx, (
            f"R1 FAIL: missing '{_BEGIN_SENTINEL}' sentinel in {_COPY_PATH.name}"
        )
    if end_idx < 0:
        return False, begin_idx, end_idx, (
            f"R1 FAIL: missing '{_END_SENTINEL}' sentinel in {_COPY_PATH.name}"
        )
    if begin_idx >= end_idx:
        return False, begin_idx, end_idx, (
            f"R1 FAIL: '{_BEGIN_SENTINEL}' appears after '{_END_SENTINEL}' in {_COPY_PATH.name}"
        )
    return True, begin_idx, end_idx, "R1 OK: sentinels present and in order"


def extract_between_sentinels(copy_text: str, begin_idx: int, end_idx: int) -> str:
    """Return the content strictly between the two sentinel *lines*.

    We work in line space, not character space, so the comment lines that
    contain the sentinels are themselves dropped. The sentinel comments
    are not part of the copy contract.
    """
    lines = copy_text.split("\n")
    begin_line_no = -1
    end_line_no = -1
    for i, line in enumerate(lines):
        if begin_line_no == -1 and _BEGIN_SENTINEL in line:
            begin_line_no = i
        elif end_line_no == -1 and _END_SENTINEL in line:
            end_line_no = i
            break
    if begin_line_no == -1 or end_line_no == -1 or begin_line_no >= end_line_no:
        raise RuntimeError(
            f"extract_between_sentinels: bad sentinel positions "
            f"begin={begin_line_no} end={end_line_no}"
        )
    between = lines[begin_line_no + 1 : end_line_no]
    return "\n".join(between)


# ─────────────────────────────────────────────────────────────────────
# Upstream extraction: constant line + full function body by line scan
# ─────────────────────────────────────────────────────────────────────


def _find_constant_line(text: str) -> tuple[int, str] | None:
    """Return (line_no_0_indexed, raw_line) of the constant definition, or None."""
    for i, line in enumerate(text.split("\n")):
        if _CONSTANT_RE.match(line):
            return i, line
    return None


def _find_function_block(text: str, func_name: str) -> tuple[int, int, list[str]] | None:
    """Find ``def func_name(...)`` and return (start_line_0idx, end_line_exclusive, lines).

    Uses ``ast`` rather than naive indentation sniffing because Python
    allows the ``def`` signature to span multiple lines (closing ``)``
    lives on its own line at column 0 for our target), which defeats
    any "first line with indent <= def-indent terminates the block"
    heuristic. ``ast.FunctionDef`` gives us ``lineno`` / ``end_lineno``
    (1-indexed, inclusive) that correctly cover the full def.
    """
    all_lines = text.split("\n")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            start_line = node.lineno - 1
            end_line = (node.end_lineno or node.lineno)  # already 1 past the last in slice terms
            return start_line, end_line, all_lines[start_line:end_line]
    return None


def extract_upstream_block(upstream_text: str) -> str:
    """Return constant-line + function body, joined with ``\\n``.

    This is the "upstream side" input to the byte-hash comparison.
    """
    const_hit = _find_constant_line(upstream_text)
    if const_hit is None:
        raise RuntimeError(
            f"extract_upstream_block: constant {_CONSTANT_NAME} not found in upstream"
        )
    const_line = const_hit[1]

    func_hit = _find_function_block(upstream_text, _FUNC_NAME)
    if func_hit is None:
        raise RuntimeError(
            f"extract_upstream_block: function {_FUNC_NAME} not found in upstream"
        )
    func_lines = func_hit[2]

    return const_line + "\n" + "\n".join(func_lines)


# ─────────────────────────────────────────────────────────────────────
# R2: constant literal present on both sides
# ─────────────────────────────────────────────────────────────────────


def check_r2_constant(upstream_text: str, copy_text: str) -> tuple[bool, str]:
    up = _find_constant_line(upstream_text)
    cp = _find_constant_line(copy_text)
    if up is None:
        return False, f"R2 FAIL: constant {_CONSTANT_NAME}=8000 missing in {_UPSTREAM_PATH.name}"
    if cp is None:
        return False, f"R2 FAIL: constant {_CONSTANT_NAME}=8000 missing in {_COPY_PATH.name}"
    return True, "R2 OK: constant literal present on both sides"


# ─────────────────────────────────────────────────────────────────────
# R3: byte-hash equivalence of normalised block
# ─────────────────────────────────────────────────────────────────────


def check_r3_byte_hash(
    upstream_text: str,
    copy_text: str,
    begin_idx: int,
    end_idx: int,
) -> tuple[bool, str]:
    upstream_block = extract_upstream_block(upstream_text)
    copy_block = extract_between_sentinels(copy_text, begin_idx, end_idx)

    normalised_upstream = _normalise_block(upstream_block)
    normalised_copy = _normalise_block(copy_block)

    hash_upstream = hashlib.sha256(normalised_upstream.encode("utf-8")).hexdigest()
    hash_copy = hashlib.sha256(normalised_copy.encode("utf-8")).hexdigest()

    if hash_upstream == hash_copy:
        return True, f"R3 OK: sha256 match ({hash_upstream[:16]}...)"

    diff = list(
        difflib.unified_diff(
            normalised_upstream.splitlines(),
            normalised_copy.splitlines(),
            fromfile=f"{_UPSTREAM_PATH.name} (normalised)",
            tofile=f"{_COPY_PATH.name} (normalised, between sentinels)",
            lineterm="",
        )
    )
    msg = [
        "R3 FAIL: byte-hash drift detected between upstream and copy.",
        f"  upstream sha256: {hash_upstream}",
        f"  copy sha256:     {hash_copy}",
        "  unified diff (normalised):",
    ]
    for line in diff:
        msg.append("    " + line)
    msg.append(
        "  hint: if this is an intentional upstream change, update "
        f"{_COPY_PATH.relative_to(_PROJECT_ROOT)} to match and re-run this smoke."
    )
    return False, "\n".join(msg)


# ─────────────────────────────────────────────────────────────────────
# R4: import works, constant value matches
# ─────────────────────────────────────────────────────────────────────


def check_r4_import() -> tuple[bool, str, object | None]:
    """Import from the testbench package and sanity-check the constant."""
    sys.path.insert(0, str(_PROJECT_ROOT))
    try:
        from tests.testbench.pipeline.avatar_dedupe import (  # type: ignore
            AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS,
            _should_persist_avatar_interaction_memory,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"R4 FAIL: cannot import from tests.testbench.pipeline.avatar_dedupe: {exc!r}", None
    if AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS != 8000:
        return (
            False,
            (
                "R4 FAIL: imported AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS = "
                f"{AVATAR_INTERACTION_MEMORY_DEDUPE_WINDOW_MS!r} != 8000"
            ),
            None,
        )
    return True, "R4 OK: import succeeded, constant == 8000", _should_persist_avatar_interaction_memory


# ─────────────────────────────────────────────────────────────────────
# R5: behavioural equivalence across 5 scenarios
# ─────────────────────────────────────────────────────────────────────


def _load_upstream_function() -> object:
    """Load ``_should_persist_avatar_interaction_memory`` from the upstream
    source **without triggering** ``main_logic``'s module-level side effects.

    We AST-parse the file, select only the constant assignment and the
    function definition, compile the resulting tiny module, and exec it
    against a fresh namespace that carries just ``time`` as an import.
    This is the only mechanism we know that honours L30's "do not import
    main_logic" while still giving us the *live* upstream function body
    for a behavioural comparison.
    """
    text = _read_text(_UPSTREAM_PATH)
    tree = ast.parse(text)
    wanted_nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            if any(t.id == _CONSTANT_NAME for t in targets):
                wanted_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == _FUNC_NAME:
            wanted_nodes.append(node)
    if not wanted_nodes:
        raise RuntimeError("_load_upstream_function: required nodes not found")

    synthetic_module = ast.Module(body=wanted_nodes, type_ignores=[])
    ast.fix_missing_locations(synthetic_module)
    compiled = compile(synthetic_module, filename=str(_UPSTREAM_PATH), mode="exec")

    namespace: dict[str, object] = {"time": time}
    exec(compiled, namespace)
    fn = namespace.get(_FUNC_NAME)
    if fn is None:
        raise RuntimeError("_load_upstream_function: exec did not produce the function")
    return fn


def _run_scenarios(up_fn, cp_fn) -> tuple[bool, list[str]]:
    """Run the 5 scenarios; return (ok, log_lines).

    Each scenario uses one cache dict per side — we explicitly do not
    share cache instances across the two functions, because the ``ts``
    value stamped by ``time.time()`` will differ between the two calls
    (they are two separate wall-clock reads). We therefore compare
    **return values** and **cache key sets**, not ``ts`` numbers.
    """
    results: list[str] = []
    all_ok = True

    def _compare(scenario: str, up_ret, cp_ret, up_cache, cp_cache) -> bool:
        ok = (up_ret == cp_ret) and (set(up_cache.keys()) == set(cp_cache.keys()))
        results.append(
            f"  [{scenario}] upstream={up_ret} copy={cp_ret} "
            f"upstream_keys={sorted(up_cache.keys())} "
            f"copy_keys={sorted(cp_cache.keys())} "
            f"-> {'OK' if ok else 'MISMATCH'}"
        )
        return ok

    # S1: empty cache + non-empty note → both True, both store same key
    up_cache: dict[str, dict[str, int | str]] = {}
    cp_cache: dict[str, dict[str, int | str]] = {}
    up_ret = up_fn(up_cache, "hi")
    cp_ret = cp_fn(cp_cache, "hi")
    all_ok &= _compare("S1 first-write", up_ret, cp_ret, up_cache, cp_cache)

    # S2: same key + same rank twice → second call False on both sides
    up_cache = {}
    cp_cache = {}
    up_fn(up_cache, "pat", dedupe_key="k1", dedupe_rank=1)
    cp_fn(cp_cache, "pat", dedupe_key="k1", dedupe_rank=1)
    up_ret = up_fn(up_cache, "pat", dedupe_key="k1", dedupe_rank=1)
    cp_ret = cp_fn(cp_cache, "pat", dedupe_key="k1", dedupe_rank=1)
    s2_ok = (up_ret is False) and (cp_ret is False)
    all_ok &= _compare(
        f"S2 duplicate-suppressed (expect both False, got up={up_ret} cp={cp_ret})",
        up_ret, cp_ret, up_cache, cp_cache,
    ) and s2_ok
    if not s2_ok:
        results.append("    FAIL: S2 expected both False")

    # S3: same key + rank upgrade (1 → 2) → second call True on both sides
    up_cache = {}
    cp_cache = {}
    up_fn(up_cache, "pat", dedupe_key="k2", dedupe_rank=1)
    cp_fn(cp_cache, "pat", dedupe_key="k2", dedupe_rank=1)
    up_ret = up_fn(up_cache, "pat", dedupe_key="k2", dedupe_rank=2)
    cp_ret = cp_fn(cp_cache, "pat", dedupe_key="k2", dedupe_rank=2)
    s3_ok = (up_ret is True) and (cp_ret is True)
    all_ok &= _compare(
        f"S3 rank-upgrade (expect both True, got up={up_ret} cp={cp_ret})",
        up_ret, cp_ret, up_cache, cp_cache,
    ) and s3_ok
    if not s3_ok:
        results.append("    FAIL: S3 expected both True")

    # S4: empty memory_note → both False, no cache entry on either side
    up_cache = {}
    cp_cache = {}
    up_ret = up_fn(up_cache, "")
    cp_ret = cp_fn(cp_cache, "")
    s4_ok = (up_ret is False) and (cp_ret is False) and (not up_cache) and (not cp_cache)
    all_ok &= _compare(
        f"S4 empty-note (expect both False + empty cache)",
        up_ret, cp_ret, up_cache, cp_cache,
    ) and s4_ok
    if not s4_ok:
        results.append("    FAIL: S4 expected both False with empty cache")

    # S5: rank = "abc" (non-int) → TypeError/ValueError fallback to 1 on both sides
    # Behaviourally this should match S1's first-write shape.
    up_cache = {}
    cp_cache = {}
    up_ret = up_fn(up_cache, "hi", dedupe_key="k5", dedupe_rank="abc")  # type: ignore[arg-type]
    cp_ret = cp_fn(cp_cache, "hi", dedupe_key="k5", dedupe_rank="abc")  # type: ignore[arg-type]
    s5_ok = (up_ret is True) and (cp_ret is True)
    # Rank stored should be 1 on both sides (the fallback).
    up_stored_rank = int((up_cache.get("k5") or {}).get("rank", -1))
    cp_stored_rank = int((cp_cache.get("k5") or {}).get("rank", -1))
    s5_rank_ok = (up_stored_rank == 1) and (cp_stored_rank == 1)
    all_ok &= _compare(
        f"S5 rank-str-fallback (up_rank={up_stored_rank} cp_rank={cp_stored_rank})",
        up_ret, cp_ret, up_cache, cp_cache,
    ) and s5_ok and s5_rank_ok
    if not (s5_ok and s5_rank_ok):
        results.append("    FAIL: S5 expected both True with stored rank == 1")

    return all_ok, results


def check_r5_behaviour(cp_fn) -> tuple[bool, str]:
    try:
        up_fn = _load_upstream_function()
    except Exception as exc:  # noqa: BLE001
        return False, f"R5 FAIL: cannot synthesise upstream function: {exc!r}"

    ok, lines = _run_scenarios(up_fn, cp_fn)
    header = "R5 OK: 5/5 scenarios equivalent" if ok else "R5 FAIL: behavioural drift detected"
    return ok, header + "\n" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# R6: no stray re-assignment outside sentinels
# ─────────────────────────────────────────────────────────────────────


def check_r6_no_stray(copy_text: str, end_idx: int) -> tuple[bool, str]:
    """After END COPY, neither the constant nor the function may be re-defined.

    We scan the post-sentinel substring line by line. A re-definition would
    be an ``X = ...`` assignment or a ``def X(...)`` line for either the
    constant or the function name.
    """
    post = copy_text[end_idx:]
    post_lines = post.split("\n")

    constant_redef = re.compile(rf"^\s*{re.escape(_CONSTANT_NAME)}\s*=")
    func_redef = re.compile(rf"^\s*def\s+{re.escape(_FUNC_NAME)}\s*\(")

    violations: list[str] = []
    for i, line in enumerate(post_lines):
        if constant_redef.match(line):
            violations.append(f"    line-after-END:{i}: {line.rstrip()}")
        elif func_redef.match(line):
            violations.append(f"    line-after-END:{i}: {line.rstrip()}")

    if violations:
        return False, (
            "R6 FAIL: stray re-definition(s) of copy-protected names after END COPY:\n"
            + "\n".join(violations)
        )
    return True, "R6 OK: no stray copy-protected names after END COPY"


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    print("=" * 66)
    print(" P25 Avatar-Dedupe Drift Smoke (L30: copy + drift smoke pair)")
    print("=" * 66)
    print(f" upstream: {_UPSTREAM_PATH.relative_to(_PROJECT_ROOT)}")
    print(f" copy:     {_COPY_PATH.relative_to(_PROJECT_ROOT)}")
    print("")

    upstream_text = _read_text(_UPSTREAM_PATH)
    copy_text = _read_text(_COPY_PATH)

    failures: list[str] = []

    # R1
    r1_ok, begin_idx, end_idx, r1_msg = check_r1_sentinels(copy_text)
    print(r1_msg)
    if not r1_ok:
        failures.append(r1_msg)
        # R3 and R6 both need sentinel offsets to do anything useful;
        # bail early with a clear summary.
        _print_summary(failures)
        return 1

    # R2
    r2_ok, r2_msg = check_r2_constant(upstream_text, copy_text)
    print(r2_msg)
    if not r2_ok:
        failures.append(r2_msg)

    # R3
    r3_ok, r3_msg = check_r3_byte_hash(upstream_text, copy_text, begin_idx, end_idx)
    print(r3_msg)
    if not r3_ok:
        failures.append(r3_msg)

    # R4
    r4_ok, r4_msg, cp_fn = check_r4_import()
    print(r4_msg)
    if not r4_ok:
        failures.append(r4_msg)

    # R5 depends on R4 (we need ``cp_fn``)
    if r4_ok and cp_fn is not None:
        r5_ok, r5_msg = check_r5_behaviour(cp_fn)
        print(r5_msg)
        if not r5_ok:
            failures.append(r5_msg)
    else:
        skip_msg = "R5 SKIP: depends on R4 (import) which failed"
        print(skip_msg)
        failures.append(skip_msg)

    # R6
    r6_ok, r6_msg = check_r6_no_stray(copy_text, end_idx)
    print(r6_msg)
    if not r6_ok:
        failures.append(r6_msg)

    _print_summary(failures)
    return 0 if not failures else 1


def _print_summary(failures: list[str]) -> None:
    print("")
    print("=" * 66)
    if failures:
        print(f" [FAIL] {len(failures)} rule(s) failed:")
        for f in failures:
            first_line = f.split("\n", 1)[0]
            print(f"   - {first_line}")
        return
    print(f" [PASS] all 6 rules clean.")
    print(f" {_SUCCESS_BANNER}")


if __name__ == "__main__":
    sys.exit(main())
