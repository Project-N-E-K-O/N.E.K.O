# -*- coding: utf-8 -*-
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

"""Stop new i18n prompt dicts from landing without a 'zh-TW' key (issue #2500).

A dict under config/prompts/ that has an 'en' key plus 'zh' or 'zh-CN' is a
localized prompt table. Most of them predate Traditional Chinese support, and
`_loc` falls back to 'en' rather than 'zh' on a missing key, so a zh-TW user who
reaches such a dict gets an English prompt.

Backfilling the existing tables is a batched effort tracked in issue #2500. This
gate only stops the hole from growing.

How the ratchet works
=====================
It counts offending dicts at the merge-base and at HEAD. HEAD having more than
the base is what fails the check. That is the whole decision — no source lines,
no per-dict identity, no grouping.

Getting here took three wrong turns, each of which broke a case the simple count
handles for free:

  * **By diff line.** A pre-existing ``{'en': ..., 'ja': ...}`` table that a PR
    turns into a localized one by adding a single ``'zh'`` line never has its own
    definition line in the diff — the gate would miss the very case it exists
    for. And renaming a module with no content change marks every line of the
    new path as added, reporting the whole file's existing backlog.
  * **By whole key set.** Then adding an unrelated locale (a new ``'fr'``
    template) to a pre-existing offender reads as a brand-new table, failing a
    PR that did not grow the backlog.
  * **By Simplified-key scheme** ('zh' vs 'zh-CN' counted separately). Then
    migrating a table from ``'zh'`` to ``'zh-CN'`` shows up as one scheme losing
    a table and the other gaining one, and Counter subtraction only keeps the
    positive side — so it reports growth that did not happen. issue #2500's own
    endgame is exactly that rename across all ~339 tables, i.e. this gate would
    have blocked the migration it exists to serve.

A plain total is invariant under all three: renames, copy edits, added locales,
and scheme migrations all leave it alone, while a table newly subject to the rule
raises it.

The tradeoff: a PR removing one offending table while adding another nets to zero
and passes. That is a deliberate accept — the alternative is matching dicts
across revisions by identity, and every candidate for that identity (position,
key set, scheme) is what the three wrong turns above already tried.

Usage:
    python scripts/check_prompt_zh_tw.py [--base origin/main]
    python scripts/check_prompt_zh_tw.py --full     # list the whole backlog
    python scripts/check_prompt_zh_tw.py --count    # backlog size only

Escape hatch: put ``# noqa: PROMPT_ZH_TW`` on the dict's opening line.
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_SUBDIR = "config/prompts"
PROMPTS_DIR = REPO_ROOT / "config" / "prompts"

CODE = "PROMPT_ZH_TW"

SIMPLIFIED_KEYS = ("zh", "zh-CN")
TRADITIONAL_KEY = "zh-TW"
ANCHOR_KEY = "en"


def _has_noqa(line: str) -> bool:
    return bool(re.search(rf"#\s*noqa:\s*{CODE}\b", line))


def resolve_keys(node: ast.AST) -> set[str] | None:
    """Statically resolve a mapping expression's key set, or None if unknowable.

    Merges are resolved *through*, not skipped: ``{"en": ...} | {"zh": ...}``
    resolves to ``{en, zh}``, so the assembled table is judged even though neither
    half is a table on its own. That is what stops a compliant
    ``{"en", "zh"} | {"zh-TW"}`` from being reported as two fragments, and equally
    stops a non-compliant ``{"en"} | {"zh"}`` from slipping through as neither.

    ``None`` means some part is not statically knowable — a spread of a name, a
    non-constant key, ``dict()`` over a variable. The gate stays silent on those
    rather than guessing, because the unknowable part is exactly where a
    ``'zh-TW'`` entry could be hiding, and a gate that cries wolf gets worked
    around rather than satisfied.

    Note the ``dict(...)`` call form cannot express ``zh-CN`` or ``zh-TW`` as
    keywords (neither is an identifier), but it can express ``zh``, so a table
    written that way is still subject to the rule.
    """
    if isinstance(node, ast.Dict):
        keys: set[str] = set()
        for key, value in zip(node.keys, node.values):
            if key is None:
                inner = resolve_keys(value)
                if inner is None:
                    return None
                keys |= inner
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:
                return None
        return keys
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        keys = set()
        for arg in node.args:
            inner = resolve_keys(arg)
            if inner is None:
                return None
            keys |= inner
        for kw in node.keywords:
            if kw.arg is None:
                inner = resolve_keys(kw.value)
                if inner is None:
                    return None
                keys |= inner
            else:
                keys.add(kw.arg)
        return keys
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = resolve_keys(node.left)
        right = resolve_keys(node.right)
        if left is None or right is None:
            return None
        return left | right
    if isinstance(node, ast.DictComp):
        return _comprehension_keys(node)
    return None


def _comprehension_keys(node: ast.DictComp) -> set[str] | None:
    """Keys of a dict comprehension over an inline literal, or None.

    Resolves the two shapes whose keys are fully determined by the source::

        {loc: build(loc) for loc in ("en", "zh", "ja")}
        {k: v for k, v in (("en", "hello"), ("zh", "你好"))}

    The first is a realistic way to write a localized table — one template per
    locale off a literal locale list — so leaving it unresolved would be a real
    blind spot, not a theoretical one.

    Deliberately does NOT follow a name to its definition. `{lang: build(lang)
    for lang in _L10N}` stays unresolved: chasing the symbol would mean judging
    derived structures whose keys are a language list rather than templates, and
    the three comprehensions that exist under config/prompts today are all of
    that kind.
    """
    if len(node.generators) != 1:
        return None
    gen = node.generators[0]
    if gen.ifs or gen.is_async:
        return None
    if not isinstance(node.key, ast.Name):
        return None
    if not isinstance(gen.iter, (ast.Tuple, ast.List, ast.Set)):
        return None

    if isinstance(gen.target, ast.Name):
        if gen.target.id != node.key.id:
            return None
        keys: set[str] = set()
        for element in gen.iter.elts:
            if not (isinstance(element, ast.Constant)
                    and isinstance(element.value, str)):
                return None
            keys.add(element.value)
        return keys

    if isinstance(gen.target, ast.Tuple):
        names = [e.id for e in gen.target.elts if isinstance(e, ast.Name)]
        if len(names) != len(gen.target.elts) or node.key.id not in names:
            return None
        index = names.index(node.key.id)
        keys = set()
        for element in gen.iter.elts:
            if not isinstance(element, (ast.Tuple, ast.List)):
                return None
            if index >= len(element.elts):
                return None
            item = element.elts[index]
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            keys.add(item.value)
        return keys

    return None


def _merge_operands(node: ast.AST) -> list[ast.AST]:
    """The sub-mappings a merged construction composes, or ``[]`` if not a merge.

    ``{**a, **b}``, ``dict(BASE, zh=...)`` and ``a | b`` compose their keys out of
    other mappings, so each operand is a *fragment*: the ``'zh-TW'`` entry may
    live in any one of them and none can be judged alone.

    Only the operands themselves. A value keyed normally alongside a spread
    (``{**COMMON, "new": {...}}``) is not a fragment — it is an independent table
    that happens to sit in a merged container.
    """
    if isinstance(node, ast.Dict):
        return [v for k, v in zip(node.keys, node.values) if k is None]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        return list(node.args) + [kw.value for kw in node.keywords if kw.arg is None]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [node.left, node.right]
    return []


def _table_nodes(tree: ast.AST) -> Iterator[tuple[ast.AST, set[str]]]:
    """Yield ``(node, keys)`` for every mapping expression with knowable keys.

    Three failure modes this threads between, each of which was shipped and then
    reported:

      * Plain ``ast.walk`` judges each operand of a merge on its own, so
        ``{**{"en": ..., "zh": ...}, **{"zh-TW": ...}}`` reports the first half as
        missing zh-TW even though the assembled mapping has it.
      * Pruning a merge's whole subtree instead loses the independent tables
        inside it — ``{**COMMON, "new": {"en": ..., "zh": ...}}`` would never
        check ``"new"``.
      * Suppressing operands without resolving the merge lets the *result* escape:
        ``{"en": ...} | {"zh": ...}`` has both halves suppressed and the enclosing
        BinOp is not a dict node, so nothing was judged at all.

    So: a resolvable expression is judged as a whole, and its merge operands are
    then suppressed as fragments of something already accounted for. An
    unresolvable one is not judged, but traversal continues through it so the
    independent tables it holds are still found.
    """
    suppressed: set[int] = set()
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if id(node) not in suppressed:
            keys = resolve_keys(node)
            if keys is not None:
                yield node, keys
        # Suppress operands whether or not the merge resolved. An operand is a
        # fragment by definition, so judging it alone is wrong either way: if the
        # merge resolved, the operand's keys are already counted in the result; if
        # it did not (`BASE | {"en": ..., "zh": ...}`), the unknown side is exactly
        # where zh-TW could be. Unconditional also handles nesting — the operands
        # of a suppressed operand are deeper fragments still.
        for operand in _merge_operands(node):
            suppressed.add(id(operand))
        stack.extend(ast.iter_child_nodes(node))


def find_violations(tree: ast.Module, source_lines: list[str]) -> list[int]:
    """Return the line number of every localized dict with no zh-TW key."""
    out: list[int] = []
    for node, keys in _table_nodes(tree):
        if ANCHOR_KEY not in keys:
            continue
        if not any(k in keys for k in SIMPLIFIED_KEYS):
            continue
        if TRADITIONAL_KEY in keys:
            continue
        lineno = node.lineno
        if 1 <= lineno <= len(source_lines) and _has_noqa(source_lines[lineno - 1]):
            continue
        out.append(lineno)
    # _table_nodes walks depth-first off a stack, so restore source order.
    return sorted(out)


def _parse_source(source: str, origin: str) -> tuple[ast.Module | None, list[str]]:
    try:
        return ast.parse(source), source.splitlines()
    except SyntaxError as exc:
        sys.stderr.write(f"{origin}: syntax error ({exc})\n")
        return None, []


def count_offenders(sources: dict[str, str]) -> int:
    """How many localized prompt tables in a {path: source} mapping lack zh-TW."""
    total = 0
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        total += len(find_violations(tree, lines))
    return total


def locate_touched(
    sources: dict[str, str],
    touched: dict[str, set[int]] | None = None,
) -> tuple[list[str], int]:
    """Split offending dicts into (touched by this diff, count of the rest).

    Purely for the error message: the pass/fail decision is the count comparison.
    A total says *that* the backlog grew but not *where*, and 339 pre-existing
    offenders is far too many to print, so the diff's own lines are what make the
    failure actionable.
    """
    likely: list[str] = []
    other = 0
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        by_line = {node.lineno: node for node, _keys in _table_nodes(tree)}
        added = (touched or {}).get(path, set())
        for lineno in find_violations(tree, lines):
            node = by_line.get(lineno)
            span = range(lineno, (getattr(node, "end_lineno", lineno) or lineno) + 1)
            if added and any(ln in added for ln in span):
                likely.append(f"{path}:{lineno}")
            else:
                other += 1
    return likely, other


# ---------------------------------------------------------------------------
# git plumbing (mirrors scripts/check_docstring_no_cjk.py)
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    """Run git and return stdout as text.

    ``errors="replace"`` because this decodes git's own reporting (diff headers,
    path lists); a malformed byte there should not abort the gate. Source blobs go
    through ``_git_bytes`` instead, so they can honour a PEP 263 declaration.
    """
    return _git_bytes(*args).decode("utf-8", errors="replace")


def _git_bytes(*args: str) -> bytes:
    """Run git and return raw stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        sys.exit(2)
    return result.stdout


def _decode_source(raw: bytes, origin: str) -> str | None:
    """Decode Python source, honouring a PEP 263 coding declaration.

    A module carrying ``# coding: latin-1`` is valid Python that a plain UTF-8
    read would reject; skipping it would mean silently not checking a real prompt
    module. Only genuinely undecodable bytes are skipped, with a diagnostic.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError:
        encoding = "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        sys.stderr.write(f"{origin}: cannot decode as {encoding} ({exc})\n")
        return None


def _merge_base(base: str) -> str:
    return _git("merge-base", base, "HEAD").strip()


SYMLINK_MODE = "120000"


def _prompt_files_at(rev: str) -> list[str]:
    """Prompt modules present at `rev`: recursive, subpackages included, no symlinks.

    ``-z`` keeps paths verbatim; without it git wraps non-ASCII paths in quotes and
    octal-escapes the bytes, which would not resolve as a path. Modes are read
    rather than using ``--name-only`` so symlinks can be excluded — see
    ``_sources_on_disk`` for why both sides must agree on that.
    """
    out = _git("ls-tree", "-r", "-z", rev, "--", PROMPTS_SUBDIR)
    paths: list[str] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition("\t")
        if not path.endswith(".py"):
            continue
        if meta.split(" ", 1)[0] == SYMLINK_MODE:
            sys.stderr.write(f"{rev}:{path}: symlink, not scanned\n")
            continue
        paths.append(path.replace("\\", "/"))
    return paths


def _sources_at(rev: str) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in _prompt_files_at(rev):
        text = _decode_source(_git_bytes("show", f"{rev}:{path}"), f"{rev}:{path}")
        if text is not None:
            sources[path] = text
    return sources


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _touched_lines(rev: str) -> dict[str, set[int]]:
    """Lines the diff added per prompt file — a hint for error messages only.

    ``-M`` keeps a pure rename from reporting every line of the new path as
    added, which would make the hint point at the whole file.
    """
    # core.quotePath=false: git otherwise C-quotes non-ASCII paths in the `+++`
    # header (`+++ "b/config/prompts/\344\270\255.py"`), which would not match the
    # real path and would drop that file's hints. Same class of problem as the
    # `-z` on ls-tree, different output channel.
    diff = _git(
        "-c", "core.quotePath=false",
        "diff", "-M", "--unified=0", f"{rev}...HEAD", "--", PROMPTS_SUBDIR,
    )
    touched: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip().replace("\\", "/")
            continue
        match = _HUNK_HEADER_RE.match(line)
        if not match or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count == 0:
            # Deletion-only hunk (`@@ -4 +3,0 @@`): nothing was added, so a plain
            # range() is empty and a table that just *lost* its 'zh-TW' entry —
            # a real way for the total to grow — would have no location at all.
            # Record the lines flanking the deletion point so the enclosing table
            # is still recognisable.
            touched.setdefault(current, set()).update({max(1, start), start + 1})
        else:
            touched.setdefault(current, set()).update(range(start, start + count))
    return touched


def _sources_on_disk() -> dict[str, str]:
    """Read every prompt module off disk, decoding per PEP 263.

    Reads bytes and hands them to ``_decode_source``, the same path the base side
    uses, so a module with a coding declaration is checked rather than skipped and
    an unreadable one is reported rather than fatal.
    """
    sources: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.is_symlink():
            # Must match _prompt_files_at, which skips mode 120000. Reading here
            # would follow the link and scan its target, while `git show` on the
            # base side yields the link's target *path* as blob content — the two
            # sides would then disagree about the same path forever, and every
            # later PR would fail on a difference no PR introduced.
            sys.stderr.write(f"{rel}: symlink, not scanned\n")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            sys.stderr.write(f"{rel}: cannot read ({exc})\n")
            continue
        text = _decode_source(raw, rel)
        if text is not None:
            sources[rel] = text
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require 'zh-TW' on newly added localized prompt dicts "
            "(signature ratchet against --base; --full scans everything)."
        )
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PROMPT_ZH_TW_BASE", "origin/main"),
        help="Base ref for the ratchet (default: origin/main).",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="List every offending dict, not just newly added ones.",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the backlog size and exit 0.",
    )
    args = parser.parse_args(argv)

    # Force UTF-8 on our own streams. When stdout is a pipe, Python encodes with
    # the locale encoding — cp1252 on the Windows CI runner — so printing a
    # non-ASCII path, or a SyntaxError whose text carries CJK source, would raise
    # UnicodeEncodeError from inside the gate. Callers decode as UTF-8 to match.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    disk = _sources_on_disk()

    if args.count:
        print(f"localized prompt dicts missing '{TRADITIONAL_KEY}': "
              f"{count_offenders(disk)}")
        return 0

    if args.full:
        found = 0
        for path, source in sorted(disk.items()):
            tree, lines = _parse_source(source, path)
            if tree is None:
                continue
            for lineno in find_violations(tree, lines):
                print(f"{path}:{lineno}: [{CODE}] missing '{TRADITIONAL_KEY}'")
                found += 1
        if found:
            print(f"\n{found} localized prompt dict(s) missing '{TRADITIONAL_KEY}'.")
            print("This is the issue #2500 backlog; --full is informational.")
        return 1 if found else 0

    merge_base = _merge_base(args.base)
    grew = count_offenders(disk) - count_offenders(_sources_at(merge_base))
    if grew <= 0:
        return 0

    likely, other = locate_touched(disk, _touched_lines(merge_base))
    print(f"[{CODE}] {grew} more localized prompt dict(s) lack "
          f"'{TRADITIONAL_KEY}' than at the merge-base.")
    if likely:
        print(f"        in lines this change touched: {', '.join(likely)}")
    else:
        print("        no touched table is missing it — the new table may have "
              "moved in from elsewhere; run --full to see every offender")
    print(f"        ({other} pre-existing offender(s) are exempt)")
    print(
        "\nA prompt dict with 'en' + 'zh'/'zh-CN' needs 'zh-TW' too: _loc falls "
        "back to 'en', not 'zh', so Traditional Chinese users would get an "
        "English prompt. Add the template, or put '# noqa: PROMPT_ZH_TW' on the "
        "dict's opening line if it genuinely does not need one.\n"
        "The ratchet compares totals rather than source lines, so the locations "
        "above are narrowed to the tables this change touched.\n"
        f"(Set $PROMPT_ZH_TW_BASE or pass --base to override the base ref.)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
