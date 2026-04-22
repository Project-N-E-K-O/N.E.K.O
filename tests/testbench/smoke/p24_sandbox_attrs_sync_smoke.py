"""P24 Sandbox-attrs sync smoke — §14A.4.

Guards the invariant that every mutable ``*_dir`` / ``*_path``
attribute on the main-program ``ConfigManager`` either:

  (a) appears in ``tests.testbench.sandbox._PATCHED_ATTRS`` so the
      sandbox explicitly redirects it on ``apply()`` / ``restore()``,
      OR
  (b) is a ``@property`` whose path is **dynamically computed** from
      an already-patched attribute (e.g. ``cloudsave_dir`` = ``self.app_docs_dir / "cloudsave"``),
      so the redirection happens transparently via attribute lookup,
      OR
  (c) is explicitly whitelisted below as a CFA-only fallback /
      runtime-derived path that has no sandbox equivalent.

This check protects against the failure mode discovered during
Day 9 main-program sync audit: a future main-program commit adds
``self.foo_dir = self.docs_dir / "foo"`` directly (case a) without
updating ``_PATCHED_ATTRS``, and the sandbox silently leaks writes
into the real user Documents folder.

Source-level AST scan (no TestClient, no imports of the full
testbench app) — fast and CI-friendly.

Usage::

    .venv/Scripts/python.exe tests/testbench/smoke/p24_sandbox_attrs_sync_smoke.py

Exits 0 on clean, 1 on any sync violation.
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path


# Force utf-8 on stdout so unicode bullets don't crash on Windows GBK.
if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_MANAGER_PATH = _PROJECT_ROOT / "utils" / "config_manager.py"
_SANDBOX_PATH = _PROJECT_ROOT / "tests" / "testbench" / "sandbox.py"


# ── Known whitelist: @property or fallback paths that MUST NOT be
# in _PATCHED_ATTRS because they're computed dynamically ────────────

# Attributes that are always-dynamic @property getters. These follow
# the sandbox transparently via their dependency on an already-patched
# attribute (``app_docs_dir`` / ``config_dir`` / ``memory_dir`` etc).
# Ideally we'd AST-confirm each one reads a patched attr, but that's
# a heavy check — the audit below (DYNAMIC_DEPENDENCY_CHECK) does a
# source-level sanity check on their bodies.
_DYNAMIC_PROPERTY_WHITELIST: frozenset[str] = frozenset({
    # Cloud save hierarchy — all derive from app_docs_dir / cloudsave_dir.
    "cloudsave_dir",
    "cloudsave_catalog_dir",
    "cloudsave_profiles_dir",
    "cloudsave_bindings_dir",
    "cloudsave_memory_dir",
    "cloudsave_overrides_dir",
    "cloudsave_meta_dir",
    "cloudsave_workshop_meta_dir",
    "cloudsave_manifest_path",
    "cloudsave_staging_dir",
    "cloudsave_backups_dir",
    # Local state (not cloud-synced), also under app_docs_dir.
    "local_state_dir",
    "root_state_path",
    "cloudsave_local_state_path",
    "character_tombstones_state_path",
})


# Attributes deliberately NOT sandboxed (CFA fallback reads original
# Documents\<app>\live2d for read-only asset access).
_INTENTIONAL_NOT_PATCHED: frozenset[str] = frozenset({
    "readable_live2d_dir",  # CFA fallback, testbench never exercises
})


# Source-level regexes for scanning.
_SELF_ASSIGN_RE = re.compile(
    r"^\s+self\.(\w+_(dir|path))\s*="
)


# ── AST-based introspection ─────────────────────────────────────────

def _parse_tree() -> ast.Module:
    return ast.parse(
        _CONFIG_MANAGER_PATH.read_text(encoding="utf-8"),
        filename=str(_CONFIG_MANAGER_PATH),
    )


def collect_direct_assignments(tree: ast.Module) -> set[str]:
    """Walk ConfigManager.__init__ and collect every ``self.<x>_dir``
    or ``self.<x>_path`` assignment target name.

    Excludes private attrs (``_steam_workshop_path``) and short-lived
    buffer variables (prefix ``_``).
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "ConfigManager":
            continue
        for stmt in ast.walk(node):
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if not isinstance(target, ast.Attribute):
                    continue
                if not (isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    continue
                name = target.attr
                if not (name.endswith("_dir") or name.endswith("_path")):
                    continue
                if name.startswith("_"):
                    continue
                names.add(name)
        break
    return names


def collect_property_names(tree: ast.Module) -> set[str]:
    """Walk ConfigManager and collect every @property-decorated
    method whose name ends in ``_dir`` or ``_path``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "ConfigManager":
            continue
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (stmt.name.endswith("_dir") or stmt.name.endswith("_path")):
                continue
            decorated_as_property = any(
                (isinstance(d, ast.Name) and d.id == "property")
                or (isinstance(d, ast.Attribute) and d.attr == "property")
                for d in stmt.decorator_list
            )
            if decorated_as_property:
                names.add(stmt.name)
        break
    return names


def collect_patched_attrs() -> set[str]:
    """Parse the _PATCHED_ATTRS tuple literal out of sandbox.py.

    Supports both ``_PATCHED_ATTRS = (...)`` and the annotated form
    ``_PATCHED_ATTRS: tuple[str, ...] = (...)`` (the one actually used).
    """
    source = _SANDBOX_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SANDBOX_PATH))

    for node in ast.walk(tree):
        # Case 1: plain ``_PATCHED_ATTRS = (...)`` assignment.
        if isinstance(node, ast.Assign):
            name_targets = [
                t for t in node.targets
                if isinstance(t, ast.Name) and t.id == "_PATCHED_ATTRS"
            ]
            if not name_targets:
                continue
            value = node.value
        # Case 2: annotated ``_PATCHED_ATTRS: tuple[str, ...] = (...)``.
        elif isinstance(node, ast.AnnAssign):
            if not (isinstance(node.target, ast.Name)
                    and node.target.id == "_PATCHED_ATTRS"):
                continue
            value = node.value
        else:
            continue

        if not isinstance(value, ast.Tuple):
            continue
        names: set[str] = set()
        for elt in value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                names.add(elt.value)
        return names

    raise RuntimeError(
        f"_PATCHED_ATTRS tuple not found in {_SANDBOX_PATH}"
    )


# ── Sanity check: @property whitelist entries are grounded in an
# already-patched attribute ─────────────────────────────────────────

def check_property_whitelist_grounding(tree: ast.Module,
                                        patched: set[str]) -> list[str]:
    """For each entry in ``_DYNAMIC_PROPERTY_WHITELIST``, assert its
    body references ``self.<some_patched_or_whitelisted_attr>`` so
    the property does actually follow the sandbox on redirection.

    Without this, someone could mark a new property as "whitelisted"
    here while its body reads a non-sandboxed path, defeating the
    audit.
    """
    errors: list[str] = []
    # Set of names whose redirection chain is considered "safe":
    # patched attributes (direct) + previously-verified whitelist props
    # (transitive) + "app_name" (string, not a path, so references to
    # it aren't a leak risk).
    chain_safe = patched | _DYNAMIC_PROPERTY_WHITELIST | {"app_name"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "ConfigManager":
            continue
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if stmt.name not in _DYNAMIC_PROPERTY_WHITELIST:
                continue
            # Scan body for `self.<x>` accesses where x is a path-ish
            # attribute (ends in _dir / _path).
            refs: set[str] = set()
            for expr in ast.walk(stmt):
                if not isinstance(expr, ast.Attribute):
                    continue
                if not (isinstance(expr.value, ast.Name)
                        and expr.value.id == "self"):
                    continue
                if expr.attr.endswith("_dir") or expr.attr.endswith("_path"):
                    refs.add(expr.attr)
            if not refs:
                errors.append(
                    f"[property-grounding] @property '{stmt.name}' "
                    f"doesn't reference any self.<_dir|_path> — "
                    f"cannot verify it follows sandbox"
                )
                continue
            ungrounded = [r for r in refs if r not in chain_safe]
            if ungrounded:
                errors.append(
                    f"[property-grounding] @property '{stmt.name}' "
                    f"references non-sandboxed path(s): {ungrounded} — "
                    f"either add to _PATCHED_ATTRS or whitelist in "
                    f"p24_sandbox_attrs_sync_smoke.py"
                )
        break
    return errors


# ── Main orchestrator ───────────────────────────────────────────────

def _report(title: str, errors: list[str]) -> int:
    print("")
    print(f"* {title}")
    if not errors:
        print("  [ok] no violations")
        return 0
    print(f"  [ERR] {len(errors)} violation(s):")
    for line in errors:
        print(f"    {line}")
    return len(errors)


def main() -> int:
    print("=" * 66)
    print(" P24 Sandbox-Attrs Sync Smoke  "
          "(_PATCHED_ATTRS vs ConfigManager *_dir / *_path)")
    print("=" * 66)

    tree = _parse_tree()
    direct_attrs = collect_direct_assignments(tree)
    property_attrs = collect_property_names(tree)
    patched = collect_patched_attrs()

    total = 0

    # Check 1: every direct assignment must be covered by _PATCHED_ATTRS.
    missing = direct_attrs - patched
    errs1: list[str] = []
    if missing:
        errs1.append(
            f"[coverage] ConfigManager directly-assigned attr(s) "
            f"not in _PATCHED_ATTRS: {sorted(missing)}"
        )
        errs1.append(
            f"  -> fix by adding to "
            f"'tests/testbench/sandbox.py::_PATCHED_ATTRS' AND also "
            f"swapping the attr in Sandbox.apply() body. "
            f"(Directly-assigned *_dir/*_path attrs hold a path *value* "
            f"that doesn't update when docs_dir changes; only @property "
            f"getters do that transparently.)"
        )
    total += _report(
        f"1 | ConfigManager directly-assigned *_dir/*_path "
        f"({len(direct_attrs)} found) all in _PATCHED_ATTRS "
        f"({len(patched)} entries)",
        errs1,
    )

    # Check 2: every entry in _PATCHED_ATTRS must correspond to a real
    # directly-assigned attr — catches typos / removed fields left in
    # the tuple.
    stale = patched - direct_attrs
    errs2: list[str] = []
    if stale:
        errs2.append(
            f"[stale] _PATCHED_ATTRS entries don't match any "
            f"directly-assigned self.<attr>=: {sorted(stale)}"
        )
        errs2.append(
            f"  -> remove from _PATCHED_ATTRS, or verify the attr was "
            f"renamed / moved to @property."
        )
    total += _report(
        "2 | _PATCHED_ATTRS entries all correspond to real "
        "ConfigManager self-assignments",
        errs2,
    )

    # Check 3: every @property ending in _dir / _path must either be
    # in the dynamic whitelist OR flagged as intentional-not-patched.
    unclassified = property_attrs - (
        _DYNAMIC_PROPERTY_WHITELIST | _INTENTIONAL_NOT_PATCHED
    )
    errs3: list[str] = []
    if unclassified:
        errs3.append(
            f"[property-unclassified] @property *_dir/*_path not in "
            f"whitelist or intentional-exempt list: {sorted(unclassified)}"
        )
        errs3.append(
            f"  -> review the property body; if it follows sandbox via "
            f"app_docs_dir / docs_dir / etc., add to "
            f"_DYNAMIC_PROPERTY_WHITELIST here; if it intentionally "
            f"doesn't (e.g. CFA fallback), add to "
            f"_INTENTIONAL_NOT_PATCHED here with a comment."
        )
    total += _report(
        f"3 | ConfigManager @property *_dir/*_path "
        f"({len(property_attrs)} found) all classified",
        errs3,
    )

    # Check 4: whitelist entries actually reference a patched attribute
    # transitively.
    errs4 = check_property_whitelist_grounding(tree, patched)
    total += _report(
        f"4 | @property whitelist entries "
        f"({len(_DYNAMIC_PROPERTY_WHITELIST)}) actually ground in a "
        f"patched attribute",
        errs4,
    )

    # Check 5: sanity — _PATCHED_ATTRS is non-empty and covers the
    # known minimum (docs_dir + app_docs_dir + config_dir).
    minimum_expected = {"docs_dir", "app_docs_dir", "config_dir", "memory_dir"}
    errs5 = []
    if not minimum_expected.issubset(patched):
        errs5.append(
            f"[sanity] _PATCHED_ATTRS missing core attrs: "
            f"{sorted(minimum_expected - patched)}"
        )
    total += _report("5 | _PATCHED_ATTRS covers core minimum set", errs5)

    print("")
    print("=" * 66)
    if total:
        print(f" [FAIL] {total} violation(s) — sandbox sync out of date.")
        return 1
    print(" [PASS] Sandbox attrs sync clean.")
    print(
        f"   Summary: {len(direct_attrs)} direct assignments all patched, "
        f"{len(property_attrs)} @property paths all classified "
        f"(whitelist + exempt)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
