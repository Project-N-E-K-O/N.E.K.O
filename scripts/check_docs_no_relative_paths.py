#!/usr/bin/env python3
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

"""Static check: forbid markdown links inside ``docs/`` that VitePress can't resolve.

Why this exists
---------------
``docs/`` ships through VitePress, which serves pages from ``docs/`` as the
deploy root and runs a dead-link check at build time: every markdown link is
resolved as a site page, and any target it can't resolve fails the build
(``[vitepress] N dead link(s) found`` → ``exit 1``).  A broken link therefore
breaks deploy on every push.  This lint catches the two recurring forms
*before* merge, so the build doesn't have to be the thing that notices.

What it flags
-------------
Both forms are markdown inline links (``](target)``) outside fenced code
blocks, inside any built ``.md`` file under ``docs/``:

1. **Relative-up** — target starts with ``..`` and escapes the doc root::

       [text](../foo)
       [text](../../static/foo.js)

   We've fixed this more than once — a previous "just one ../static link,
   this once" cost a doc-pipeline cleanup PR.

2. **Source-file** — a *relative* link to a repo source file (``.py``,
   ``.ts``, … optionally with a ``:line`` anchor) that has no doc page::

       [text](utils/token_tracker.py)
       [text](main_routers/system_router.py:194)

   VitePress resolves these against the current doc dir (e.g.
   ``docs/design/security/main_routers/...``), finds nothing, and aborts.
   This is the form that broke the build in the telemetry / local-mutation
   design docs — the ``..`` rule above missed it because the target has no
   leading ``..``.

Absolute URLs (``http(s)://…`` incl. GitHub ``blob`` links), site-absolute
paths (``/logo.jpg``), ``mailto:``, and in-page anchors (``#section``) are
fine and never flagged.  ``..`` text outside the ``](...)`` link form (shell
snippets, prose) is not flagged either.

Build-scope parity
------------------
Only files VitePress actually builds are inspected:
- ``node_modules/`` is skipped (third-party READMEs, never deployed).
- Runtime-only pages and README translations in ``SRC_EXCLUDE`` are skipped
  to mirror the ``srcExclude`` list in ``docs/.vitepress/config.ts`` — keep
  the two in sync if that list changes.

Suppression
-----------
None.  If you genuinely need to reference a non-docs file, either inline the
path as code (`` `utils/token_tracker.py:194` ``) without a link, or use a
full GitHub URL (``https://github.com/.../blob/main/utils/token_tracker.py``),
or move the content into ``docs/``.  A per-line escape hatch would defeat the
purpose.

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

# Mirror `srcExclude` in docs/.vitepress/config.ts — these aren't built, so a
# broken link in them can't break deploy.  Keep in sync if that list changes.
SRC_EXCLUDE = {
    "README_en.md",
    "README_ja.md",
    "README_ru.md",
    "zh-CN/guide/openclaw_guide.md",
    "zh-CN/guide/openclaw_guide.en.md",
    "zh-CN/guide/openclaw_guide.ja.md",
    "zh-CN/guide/openclaw_guide.ko.md",
    "zh-CN/guide/openclaw_guide.ru.md",
    "zh-CN/guide/openclaw_guide.zh-TW.md",
}

# Any markdown inline link target.  Reference-style links (``[foo][bar]``) and
# image-only refs aren't a vitepress page-resolution hazard, so only the URL
# form ``](...)`` matters.
LINK_PATTERN = re.compile(r"\]\(([^)]+)\)")

# Source-file extensions a doc might wrongly link to as if it were a page.
# A trailing ``:line`` / ``:line-line`` anchor (our code-reference convention)
# is part of the same hazard, so allow it after the extension.
SRC_FILE_PATTERN = re.compile(
    r"\.(?:py|js|mjs|cjs|ts|tsx|jsx|vue|css|scss|sass|less|html?|sh|bash|zsh"
    r"|bat|cmd|ps1|go|rs|rb|java|kt|swift|c|cc|cpp|cxx|h|hpp|toml|ini|cfg"
    r"|conf|ya?ml|sql|env)(?::\d+(?:-\d+)?)?$",
    re.IGNORECASE,
)

# Targets that resolve fine and must never be flagged.
_SAFE_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "/")


def _classify(target: str) -> str | None:
    """Return a violation kind for an offending link target, else ``None``."""
    if target.startswith(_SAFE_PREFIXES):
        return None
    if target.startswith(".."):
        return "relative-up"
    # Strip a query/fragment before testing the file extension.
    path_part = re.split(r"[?#]", target, maxsplit=1)[0]
    if SRC_FILE_PATTERN.search(path_part):
        return "source-file"
    return None


def main() -> int:
    if not DOCS_DIR.is_dir():
        # No docs folder = nothing to check.  Don't fail CI on repos that
        # haven't created the folder yet.
        return 0

    failures: list[tuple[Path, int, str, str]] = []
    for md_path in sorted(DOCS_DIR.rglob("*.md")):
        if "node_modules" in md_path.parts:
            continue
        source_path = md_path.relative_to(DOCS_DIR).as_posix()
        if source_path in SRC_EXCLUDE:
            continue
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
                target = m.group(1).strip()
                kind = _classify(target)
                if kind is not None:
                    failures.append((md_path, lineno, target, kind))

    if not failures:
        return 0

    rel = lambda p: p.resolve().relative_to(REPO_ROOT).as_posix()
    print("Unresolvable markdown links inside docs/:", file=sys.stderr)
    for path, lineno, target, kind in failures:
        print(f"  [{kind}] {rel(path)}:{lineno}  ->  ({target})", file=sys.stderr)
    print(
        "\nVitePress builds docs/ as the site root and dead-link-checks every "
        "link; the targets above don't resolve to a doc page and break deploy.\n"
        "Fix: drop the link wrapper and inline the path as code, e.g.\n"
        "    [utils/token_tracker.py:194](utils/token_tracker.py)  ->  `utils/token_tracker.py:194`\n"
        "    [foo/bar.js](../../foo/bar.js)                        ->  `foo/bar.js`\n"
        "or use a full GitHub URL (https://github.com/.../blob/main/<path>), "
        "or move the referenced content into docs/.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
