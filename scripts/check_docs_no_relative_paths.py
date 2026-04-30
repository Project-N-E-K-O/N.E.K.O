#!/usr/bin/env python3
"""Static check: forbid relative-up (``..``) markdown links inside ``docs/``.

Why this exists
---------------
``docs/`` ships through VitePress, which serves pages from ``docs/`` as the
deploy root.  Any markdown link target that escapes the doc root with
``..`` (e.g. ``[foo](../../static/foo.js)``) breaks the VitePress build:
the path resolves outside the docs site and breaks deploy on every push.

We've fixed it more than once — a previous round of "just one ../static
link, this once" cost a doc-pipeline cleanup PR.  This lint exists so the
next attempt fails CI before merge.

What it flags
-------------
Markdown link patterns whose target starts with ``..`` (any number of
parent segments) inside any ``.md`` file under ``docs/``:

    [text](../foo)
    [text](../../bar/baz.md)
    [text](.../weird)            # leading ``..`` covers this too

Other ``..`` text (shell commands inside fenced code blocks, prose
mentions, etc.) is NOT flagged — only the ``](...)`` link target form.

Suppression
-----------
None.  If you genuinely need to reference a non-docs file, either inline
the path as code (`` `static/foo.js` ``) without a link, or move the
content into ``docs/``.  A per-line escape hatch would defeat the purpose.

Run
---
    python scripts/check_docs_no_relative_paths.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

# Match a markdown inline link whose target starts with "..".
# - Captures the link text and the offending target so the error is actionable.
# - Only the URL form ``](...)`` matters; collapsed/reference-style links
#   (``[foo][bar]`` + a separate definition) aren't a vitepress hazard.
LINK_PATTERN = re.compile(r"\]\((\.\.[^)]*)\)")


def main() -> int:
    if not DOCS_DIR.is_dir():
        # No docs folder = nothing to check.  Don't fail CI on repos that
        # haven't created the folder yet.
        return 0

    failures: list[tuple[Path, int, str]] = []
    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"::warning file={md_path}::could not read ({e})", file=sys.stderr)
            continue
        # Fenced code blocks frequently contain "bad-example" snippets the
        # docs are explicitly warning against (e.g. a sample of the very
        # link form this lint forbids).  Skip anything inside ``` / ~~~
        # fences so the "show, don't tell" pattern stays usable.  Indented
        # code blocks (4-space) are rare in this repo and not handled.
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in LINK_PATTERN.finditer(line):
                failures.append((md_path, lineno, m.group(1)))

    if not failures:
        return 0

    rel = lambda p: p.resolve().relative_to(REPO_ROOT).as_posix()
    print("Forbidden relative-up markdown links inside docs/:", file=sys.stderr)
    for path, lineno, target in failures:
        print(
            f"  {rel(path)}:{lineno}  ->  ({target})",
            file=sys.stderr,
        )
    print(
        "\nVitePress builds docs/ as the site root; any markdown link target "
        "starting with '..' resolves outside the site and breaks deploy.\n"
        "Fix: drop the link wrapper and inline the path as code, e.g.\n"
        "    [foo/bar.js](../../foo/bar.js)   ->   `foo/bar.js`\n"
        "    or move the referenced content into docs/.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
