"""P26 Commit 1 — public docs endpoint + version metadata invariants.

Purpose
-------
P26 Commit 1 introduced four docs-related invariants:

* ``tb_config.TESTBENCH_VERSION`` / ``TESTBENCH_PHASE`` are the single
  source of truth for the app's self-reported version & dev stage; both
  ``server.py`` and ``/api/version`` must consume these constants (not
  re-hardcode a separate string).
* ``GET /docs/{doc_name}`` serves a *small, hand-picked* whitelist of
  tester-facing Markdown docs under ``DOCS_DIR`` with two-mode content
  negotiation (``text/markdown`` raw vs rendered HTML). Everything else
  returns 404 with ``error_type=unknown_doc``.
* When a whitelisted doc hasn't been authored yet (e.g. ``USER_MANUAL``
  is scheduled for Commit 3 but we're between commits), the endpoint
  returns 404 ``error_type=file_missing`` *with a friendly message*,
  not a generic "file not found". The dual-semantics distinction
  (unknown vs file-missing) is **per L46** a cross-project lesson.
* Settings → About page consumes the ``/docs/{name}`` endpoint via
  whitelisted URL segments, matching the set declared in
  ``_PUBLIC_DOCS``.

This smoke is intentionally **static** (AST / regex / filesystem
checks, no live server) — regressions here surface at CI time, faster
than round-tripping through uvicorn.

Contracts
---------
    D1  ``tb_config.TESTBENCH_VERSION`` exists and is semver-shaped
        (MAJOR.MINOR.PATCH, all digits). ``TESTBENCH_PHASE`` exists and
        is a non-empty user-facing string. ``TESTBENCH_LAST_UPDATED``
        exists and matches ISO-8601 date format (``YYYY-MM-DD``) —
        this is the "最后更新日期" value rendered on Settings → About
        so testers can see at a glance when the build was cut. (The
        phase-prefix sub-check previously enforced ``P<digits>``
        mirroring internal blueprint phase codes; that prefix leaked
        developer-internal nomenclature into the About page and
        tooltips. Since the UI polish that strips Pxx identifiers from
        tester-visible text, ``TESTBENCH_PHASE`` is a human-readable
        release name — the contract now just verifies it's a non-empty
        string. In practice the About page no longer renders phase
        directly, but keeping the constant non-empty prevents
        accidental regressions if any code path consumes it.)

    D2  ``server.py`` wires FastAPI ``version=`` to
        ``tb_config.TESTBENCH_VERSION`` (not a hard-coded string).

    D3  ``routers/health_router.py::version()`` returns keys
        ``version`` + ``phase`` sourced from ``tb_config.TESTBENCH_*``
        (not re-hardcoded).

    D4  ``routers/health_router.py`` declares a
        ``@router.get("/docs/{doc_name}")`` endpoint.

    D5  ``_PUBLIC_DOCS`` whitelist contains the four tester-facing
        entries we ship with v1.1.0: ``testbench_USER_MANUAL``,
        ``testbench_ARCHITECTURE_OVERVIEW``, ``external_events_guide``,
        ``CHANGELOG``.

    D6  Each whitelist value maps to a file under ``DOCS_DIR`` (file
        may or may not exist — that's the dual-semantics contract —
        but the basename must match a known filename pattern).

    D7  The endpoint handler distinguishes two 404 error_types:
        ``unknown_doc`` (not in whitelist) vs ``file_missing``
        (whitelist entry but file on disk absent). Both variants must
        appear as literal strings in the handler source — this is the
        L46 "dual 404 semantics" invariant.

    D8  Settings → About page (``static/ui/settings/page_about.js``)
        references ``/docs/`` URL path at least once (so the
        whitelisted docs are actually deep-linked from the UI — not
        just a dead endpoint).

    D9  At least one of the whitelisted docs exists on disk (sanity
        check — if all four are missing, the whole endpoint is dead
        and the invariant is vacuous).

    D10 ``CHANGELOG.md`` file content begins with a ``## v1.1.0`` (or
        ``## [v1.1.0]``) heading matching ``TESTBENCH_VERSION``. This
        is the "version-metadata ↔ CHANGELOG" alignment invariant — if
        the version constant is bumped, the CHANGELOG must get a new
        section for it, and vice versa.

    D11 The rendered HTML stamps every heading with an ``id`` attribute
        (GitHub-style slug). Without this, the TOC links
        ``[§1.1](#11-...)`` in both the USER_MANUAL and the
        ARCHITECTURE_OVERVIEW go nowhere — which broke user manual
        testing of P26 Commit 3. Probe by running the real renderer
        over a small fixture and asserting ``<h2 id="..."`` appears.

    D12 The rendered HTML strips ``.md`` suffix off intra-whitelist
        cross-doc links. Markdown authors naturally write
        ``[arch](testbench_ARCHITECTURE_OVERVIEW.md)``, but the
        endpoint only whitelists the stem — the browser resolves the
        link relative to ``/docs/testbench_USER_MANUAL`` and hits
        ``/docs/testbench_ARCHITECTURE_OVERVIEW.md`` which 404s on
        ``unknown_doc``. D12 verifies the renderer rewrites such
        links to the whitelist-correct stem.

    D13 Every in-doc anchor link (``[label](#slug)``) in the two
        tester-facing long-form docs (USER_MANUAL, ARCHITECTURE_OVERVIEW)
        must resolve to some heading's slug under the same
        ``_slugify_heading`` algorithm the server uses at render time.
        This catches "author guessed the slug" bugs where the heading
        contains punctuation (``/``, ``+``, ``(...)``) that the slug
        algorithm drops outright rather than replacing with ``-``, so
        the link's ``--`` double-hyphen never matches the rendered
        ``-`` single-hyphen and the TOC click silently does nothing.

    D14 USER_MANUAL high-value tester-fact alignment. A tight set of
        discrete facts that the manual claims about the live UI / runtime
        must still match the code:

          * Number of top-level workspaces (tabs) matches
            ``static/app.js::WORKSPACES``.
          * Number of Setup sub-pages matches
            ``static/ui/workspace_setup.js::PAGES`` filtered by
            ``kind == 'page'``.
          * Number of Evaluation / Diagnostics / Settings sub-pages
            match the respective ``PAGES`` arrays.
          * Number of Memory trigger ops (the "触发操作" panel) matches
            ``static/ui/setup/memory_trigger_panel.js::OPS``.
          * The manual's data directory path claim resolves to the
            same ``DATA_DIR`` basename as ``tb_config.DATA_DIR``.

        Scope is **intentionally narrow** — only countable / path-literal
        facts that (a) are already stated in the manual with an exact
        value and (b) have a single point of truth in code. Prose /
        behavior claims are out of scope; those rely on D13's
        hand-test pass (§7.29 Defense 3) instead of a static linter
        to avoid the "fact-extraction linter becomes a format pedant"
        anti-pattern.

Usage::

    .venv\\Scripts\\python.exe tests/testbench/smoke/p26_docs_endpoint_smoke.py

Exits non-zero on any violation. Fast (< 1s) — no network, no LLM.
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
DOCS_DIR = TESTBENCH_ROOT / "docs"


_EXPECTED_WHITELIST_KEYS = {
    "testbench_USER_MANUAL",
    "testbench_ARCHITECTURE_OVERVIEW",
    "external_events_guide",
    "CHANGELOG",
}

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# TESTBENCH_PHASE is a human-readable release nickname; the About page
# no longer renders it, but it stays in the /version endpoint for
# programmatic consumers. Any non-whitespace character is acceptable —
# we just guard against "None" / "" / "   ".
_PHASE_NON_EMPTY_RE = re.compile(r"\S")
# TESTBENCH_LAST_UPDATED is rendered on Settings → About as
# "最后更新日期: YYYY-MM-DD". Enforce strict ISO-8601 date (not full
# datetime) — the About page shows dates only, and accepting a
# datetime string would render awkwardly in the kv list.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── D1 — version metadata shape ────────────────────────────────────

def check_version_metadata_shape() -> list[str]:
    from tests.testbench import config as tb_config  # noqa: PLC0415

    errors: list[str] = []

    version = getattr(tb_config, "TESTBENCH_VERSION", None)
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        errors.append(
            f"[D1] tb_config.TESTBENCH_VERSION must be a semver string "
            f"(MAJOR.MINOR.PATCH, digits only). Got: {version!r}"
        )

    phase = getattr(tb_config, "TESTBENCH_PHASE", None)
    if not isinstance(phase, str) or not _PHASE_NON_EMPTY_RE.search(phase):
        errors.append(
            f"[D1] tb_config.TESTBENCH_PHASE must be a non-empty "
            f"string (exposed via /version for programmatic consumers). "
            f"Got: {phase!r}"
        )

    last_updated = getattr(tb_config, "TESTBENCH_LAST_UPDATED", None)
    if not isinstance(last_updated, str) or not _ISO_DATE_RE.match(last_updated):
        errors.append(
            f"[D1] tb_config.TESTBENCH_LAST_UPDATED must be an "
            f"ISO-8601 date (YYYY-MM-DD) — shown on Settings → About "
            f"as '最后更新日期'. Got: {last_updated!r}"
        )
    return errors


# ── D2 — server.py wires FastAPI version= to tb_config ─────────────

def check_server_version_sourced_from_config() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "server.py"
    if not path.exists():
        return [f"[D2] server.py missing at {path}"]
    src = _read(path)
    if "tb_config.TESTBENCH_VERSION" not in src:
        errors.append(
            "[D2] server.py does not read tb_config.TESTBENCH_VERSION; "
            "FastAPI app `version=` must consume the central constant "
            "(not a hard-coded string). See P26 Commit 1."
        )
    # Red-flag any literal semver in the FastAPI init that would
    # indicate someone re-hardcoded version= without going through the
    # constant.
    m = re.search(r"FastAPI\s*\([^)]*version\s*=\s*['\"]\d+\.\d+\.\d+['\"]", src, flags=re.DOTALL)
    if m:
        errors.append(
            f"[D2] server.py FastAPI(...) has a hardcoded version= string "
            f"({m.group(0)!r}); must reference tb_config.TESTBENCH_VERSION."
        )
    return errors


# ── D3 — health_router.version() reads tb_config ───────────────────

def check_health_router_version_endpoint() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    if not path.exists():
        return [f"[D3] health_router.py missing at {path}"]
    src = _read(path)
    if "tb_config.TESTBENCH_VERSION" not in src:
        errors.append(
            "[D3] health_router.py does not read "
            "tb_config.TESTBENCH_VERSION (the /api/version endpoint "
            "must expose the central constant)."
        )
    if "tb_config.TESTBENCH_PHASE" not in src:
        errors.append(
            "[D3] health_router.py does not read tb_config.TESTBENCH_PHASE "
            "(the /api/version endpoint must expose the phase tag)."
        )
    if "tb_config.TESTBENCH_LAST_UPDATED" not in src:
        errors.append(
            "[D3] health_router.py does not read "
            "tb_config.TESTBENCH_LAST_UPDATED (the /api/version endpoint "
            "must expose the release cut date — consumed by Settings → "
            "About's '最后更新日期' field)."
        )
    return errors


# ── D4 — /docs/{doc_name} endpoint present ─────────────────────────

def check_docs_endpoint_declared() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    src = _read(path)
    if not re.search(r"""@router\.get\(\s*["']\/docs\/\{doc_name\}["']""", src):
        errors.append(
            "[D4] health_router.py missing "
            "@router.get('/docs/{doc_name}') — the public docs endpoint "
            "must stay reachable for the About-page deep links."
        )
    return errors


# ── D5 — _PUBLIC_DOCS whitelist content ────────────────────────────

def _extract_public_docs_dict(src: str) -> dict[str, str] | None:
    """Parse ``_PUBLIC_DOCS: dict[str, str] = {...}`` via AST."""
    tree = ast.parse(src)
    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        for t in targets:
            if t.id == "_PUBLIC_DOCS" and isinstance(value, ast.Dict):
                out: dict[str, str] = {}
                for k, v in zip(value.keys, value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                        if isinstance(k.value, str) and isinstance(v.value, str):
                            out[k.value] = v.value
                return out
    return None


def check_public_docs_whitelist() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    src = _read(path)
    mapping = _extract_public_docs_dict(src)
    if mapping is None:
        errors.append(
            "[D5] health_router.py does not declare `_PUBLIC_DOCS: dict[str, str] = {...}` "
            "at module level (AST could not locate it)."
        )
        return errors
    missing = _EXPECTED_WHITELIST_KEYS - mapping.keys()
    if missing:
        errors.append(
            f"[D5] _PUBLIC_DOCS is missing expected whitelist keys for "
            f"v1.1.0: {sorted(missing)}. Expected at least: "
            f"{sorted(_EXPECTED_WHITELIST_KEYS)}."
        )
    return errors


# ── D6 — whitelist values look like .md filenames ──────────────────

def check_whitelist_values_are_markdown_filenames() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    src = _read(path)
    mapping = _extract_public_docs_dict(src) or {}
    for key, filename in mapping.items():
        if not filename.endswith(".md"):
            errors.append(
                f"[D6] _PUBLIC_DOCS[{key!r}] = {filename!r}; value must "
                f"be a ``*.md`` filename under DOCS_DIR."
            )
        if "/" in filename or "\\" in filename:
            errors.append(
                f"[D6] _PUBLIC_DOCS[{key!r}] = {filename!r} contains a "
                f"path separator; must be a bare filename (DOCS_DIR is "
                f"joined by the handler)."
            )
    return errors


# ── D7 — dual 404 error_type strings present in handler ────────────

def check_dual_404_semantics() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    src = _read(path)
    for sentinel in ('"unknown_doc"', "'unknown_doc'"):
        if sentinel in src:
            break
    else:
        errors.append(
            "[D7] health_router.py missing the ``unknown_doc`` error_type "
            "sentinel — the /docs/{doc_name} handler must distinguish "
            "'not in whitelist' (unknown_doc) from 'on whitelist but not "
            "yet authored' (file_missing) per L46."
        )
    for sentinel in ('"file_missing"', "'file_missing'"):
        if sentinel in src:
            break
    else:
        errors.append(
            "[D7] health_router.py missing the ``file_missing`` error_type "
            "sentinel — the /docs/{doc_name} handler must distinguish "
            "'not in whitelist' (unknown_doc) from 'on whitelist but not "
            "yet authored' (file_missing) per L46."
        )
    return errors


# ── D8 — Settings → About page references /docs/ ───────────────────

def check_about_page_consumes_docs_endpoint() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "static" / "ui" / "settings" / "page_about.js"
    if not path.exists():
        errors.append(f"[D8] page_about.js missing at {path}")
        return errors
    src = _read(path)
    if "/docs/" not in src:
        errors.append(
            "[D8] page_about.js does not reference the /docs/ URL path; "
            "the About-page doc links must deep-link into the public "
            "docs endpoint (P26 Commit 1 integration)."
        )
    return errors


# ── D9 — at least one whitelisted doc exists on disk ───────────────

def check_at_least_one_doc_on_disk() -> list[str]:
    errors: list[str] = []
    path = TESTBENCH_ROOT / "routers" / "health_router.py"
    src = _read(path)
    mapping = _extract_public_docs_dict(src) or {}
    if not mapping:
        return errors  # D5 already flagged
    present = [fn for fn in mapping.values() if (DOCS_DIR / fn).is_file()]
    if not present:
        errors.append(
            f"[D9] None of the whitelisted docs are present on disk under "
            f"{DOCS_DIR}. Whitelist claims {sorted(mapping.values())} but "
            f"zero files exist — the /docs/ endpoint is effectively dead."
        )
    return errors


# ── D10 — CHANGELOG heading matches TESTBENCH_VERSION ──────────────

def check_changelog_has_current_version() -> list[str]:
    from tests.testbench import config as tb_config  # noqa: PLC0415

    errors: list[str] = []
    changelog = DOCS_DIR / "CHANGELOG.md"
    if not changelog.is_file():
        errors.append(
            f"[D10] CHANGELOG.md not found at {changelog}. P26 Commit 1 "
            f"must ship a CHANGELOG aligned with TESTBENCH_VERSION."
        )
        return errors
    version = getattr(tb_config, "TESTBENCH_VERSION", None) or ""
    src = _read(changelog)
    pat = re.compile(r"^#{1,3}\s*\[?v?" + re.escape(version) + r"\]?\b", re.MULTILINE)
    if not pat.search(src):
        errors.append(
            f"[D10] CHANGELOG.md has no heading matching TESTBENCH_VERSION "
            f"({version!r}). Expected a top-level heading like "
            f"'## v{version}' or '## [v{version}]'. Keeping these in sync "
            f"is a P26 invariant — bumping the version constant without "
            f"adding a CHANGELOG section is a silent regression."
        )
    return errors


# ── D11 — renderer stamps heading ids ──────────────────────────────

def check_rendered_headings_have_ids() -> list[str]:
    errors: list[str] = []
    try:
        from tests.testbench.routers.health_router import (  # noqa: PLC0415
            _render_markdown_html,
        )
    except ImportError as exc:
        return [f"[D11] cannot import _render_markdown_html: {exc}"]
    md = "# Top heading\n\n## Sub 1.1\n\ntext"
    html = _render_markdown_html(md, title="fixture")
    if 'id="top-heading"' not in html.lower() and "id='top-heading'" not in html.lower():
        errors.append(
            "[D11] _render_markdown_html output has no ``id`` attr on "
            "the top-level heading. Expected the renderer to slug each "
            "heading so in-doc TOC anchors work. Without this, "
            "clicking '目录 → §1.1' in USER_MANUAL / ARCHITECTURE_OVERVIEW "
            "produces no navigation."
        )
    if 'id="sub-11"' not in html.lower() and "id='sub-11'" not in html.lower():
        errors.append(
            "[D11] slug for 'Sub 1.1' is not 'sub-11' — the slug "
            "algorithm changed? Check _slugify_heading(). Author-side "
            "anchors in the shipped docs rely on this exact form."
        )
    return errors


# ── D12 — renderer rewrites .md cross-doc links ────────────────────

def check_rendered_md_links_stripped() -> list[str]:
    errors: list[str] = []
    try:
        from tests.testbench.routers.health_router import (  # noqa: PLC0415
            _render_markdown_html,
        )
    except ImportError as exc:
        return [f"[D12] cannot import _render_markdown_html: {exc}"]
    md = (
        "see [arch](testbench_ARCHITECTURE_OVERVIEW.md) "
        "and [arch section](testbench_ARCHITECTURE_OVERVIEW.md#11-foo) "
        "and [external](external_events_guide.md#q5)."
    )
    html = _render_markdown_html(md, title="fixture")
    if "testbench_ARCHITECTURE_OVERVIEW.md" in html:
        errors.append(
            "[D12] rendered HTML still contains 'testbench_ARCHITECTURE_OVERVIEW.md' "
            "— the link rewriter should strip the .md suffix on whitelisted "
            "stems so the browser doesn't hit /docs/xxx.md and 404."
        )
    if 'href="testbench_ARCHITECTURE_OVERVIEW#11-foo"' not in html:
        errors.append(
            "[D12] expected rewritten href "
            "'testbench_ARCHITECTURE_OVERVIEW#11-foo' in rendered HTML "
            "(anchor fragment must be preserved across the .md-suffix strip)."
        )
    return errors


# ── D13 — all in-doc anchor links resolve to a real heading ────────
#
# Background: authors frequently write TOC links by eyeballing the
# heading text and guessing the slug (``[准备事项](#1-准备事项-启动--配置--首次打开)``).
# The live ``_slugify_heading`` drops punctuation entirely rather than
# replacing it with ``-``, so ``/ (...)`` evaporates and ``--`` double
# hyphens in links never match the single-``-`` headings actually
# rendered. Users see link clicks that silently do nothing. This check
# scans the two public .md docs for ``](#anchor)`` links and verifies
# each one matches some real heading's slug.
#
# Scoped to ``testbench_USER_MANUAL`` and ``testbench_ARCHITECTURE_OVERVIEW``
# because those are the two tester-facing long-form docs with real TOCs;
# CHANGELOG / external_events_guide are short enough that they don't
# bother with cross-section links.

def check_in_doc_anchors_resolve() -> list[str]:
    import re as _re  # noqa: PLC0415
    errors: list[str] = []
    try:
        from tests.testbench.routers.health_router import (  # noqa: PLC0415
            _slugify_heading,
        )
    except ImportError as exc:
        return [f"[D13] cannot import _slugify_heading: {exc}"]

    docs_dir = REPO_ROOT / "tests" / "testbench" / "docs"
    targets = ["testbench_USER_MANUAL.md", "testbench_ARCHITECTURE_OVERVIEW.md"]
    for name in targets:
        path = docs_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        heading_slugs: set[str] = set()
        for m in _re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, flags=_re.M):
            slug = _slugify_heading(m.group(1).strip())
            if slug:
                heading_slugs.add(slug)
        for m in _re.finditer(r"\[([^\]\n]+)\]\(#([^)]+)\)", text):
            label, anchor = m.group(1), m.group(2)
            if anchor not in heading_slugs:
                line = text[: m.start()].count("\n") + 1
                errors.append(
                    f"[D13] {name}:L{line} link '[{label}](#{anchor})' "
                    f"doesn't match any heading slug. Check for `/`, `+`, "
                    f"`(` / `)` in the heading — those get dropped by "
                    f"_slugify_heading, they do NOT become `-`."
                )
    return errors


# ── D14 — USER_MANUAL high-value tester-fact alignment ─────────────
#
# Background: LESSONS_LEARNED §7.29 Defense 3 (multi-round hand-test)
# is the primary guard against doc drift, but a small set of countable
# facts in the manual are cheap to lock at CI time. This check locks
# exactly those — anything where the manual already states a number /
# path literal AND the code has a single point of truth. Prose /
# behavioral claims are deliberately excluded (Defense 3's job).
#
# Scoped to ``testbench_USER_MANUAL`` only — the other whitelisted
# docs are short enough that a terminology sweep during authoring
# catches drift.

def _count_dict_entries_in_array(src: str, array_name: str, kind_filter: str | None = None) -> int | None:
    """Count top-level dict literals inside ``const <array_name> = [ ... ]``.

    If ``kind_filter`` is given (e.g. ``'page'``), only entries whose
    ``kind: '<kind_filter>'`` property matches are counted. Returns
    None if the array declaration cannot be located.
    """
    pat = re.compile(
        r"const\s+" + re.escape(array_name) + r"\s*=\s*\[(?P<body>.*?)\];",
        re.DOTALL,
    )
    m = pat.search(src)
    if not m:
        return None
    body = m.group("body")
    count = 0
    depth = 0
    chunk_start: int | None = None
    for i, ch in enumerate(body):
        if ch == "{":
            if depth == 0:
                chunk_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and chunk_start is not None:
                chunk = body[chunk_start : i + 1]
                if kind_filter is None:
                    count += 1
                else:
                    if re.search(r"kind\s*:\s*['\"]" + re.escape(kind_filter) + r"['\"]", chunk):
                        count += 1
                chunk_start = None
    return count


def _count_memory_ops(src: str) -> int | None:
    """Count literals of the form ``{ op: '<something>', ... }``."""
    hits = re.findall(r"\{\s*op\s*:\s*['\"][a-z._]+['\"]", src)
    return len(hits) if hits else None


def check_user_manual_high_value_facts() -> list[str]:
    from tests.testbench import config as tb_config  # noqa: PLC0415

    errors: list[str] = []
    manual = DOCS_DIR / "testbench_USER_MANUAL.md"
    if not manual.is_file():
        # USER_MANUAL is whitelisted but may not be authored in a given
        # checkpoint (dual-semantics, D7). Silently skip when missing.
        return errors
    manual_src = _read(manual)
    static_root = TESTBENCH_ROOT / "static"

    # Fact 1 — workspace count (app.js::WORKSPACES)
    app_src = _read(static_root / "app.js")
    ws_count = _count_dict_entries_in_array(app_src, "WORKSPACES")
    if ws_count is None:
        errors.append("[D14] cannot locate `const WORKSPACES = [...]` in static/app.js")
    else:
        if not re.search(rf"\b{ws_count}\s*个\s*workspace\b", manual_src):
            errors.append(
                f"[D14] USER_MANUAL does not state '{ws_count} 个 workspace' "
                f"anywhere, but static/app.js::WORKSPACES has exactly "
                f"{ws_count} entries. Update the manual's §2.1 + TOC if "
                f"workspaces were added/removed."
            )

    # Fact 2 — Setup sub-page count (workspace_setup.js::PAGES, kind='page')
    setup_src = _read(static_root / "ui" / "workspace_setup.js")
    setup_pages = _count_dict_entries_in_array(setup_src, "PAGES", kind_filter="page")
    if setup_pages is None:
        errors.append("[D14] cannot locate `const PAGES = [...]` in static/ui/workspace_setup.js")
    elif not re.search(rf"\b{setup_pages}\s*(?:个)?\s*子页", manual_src):
        errors.append(
            f"[D14] USER_MANUAL does not state '{setup_pages} 子页' / "
            f"'{setup_pages} 个子页' for the Setup workspace, but "
            f"workspace_setup.js::PAGES has {setup_pages} kind='page' "
            f"entries. Check the manual's §3 + the overview table in §2.1."
        )

    # Fact 3 — Evaluation sub-page count
    eval_src = _read(static_root / "ui" / "workspace_evaluation.js")
    eval_pages = _count_dict_entries_in_array(eval_src, "PAGES", kind_filter="page")
    if eval_pages is None:
        errors.append("[D14] cannot locate `const PAGES = [...]` in static/ui/workspace_evaluation.js")
    elif eval_pages == 4:
        if not re.search(r"四\s*子页|\b4\s*(?:个)?\s*子页", manual_src):
            errors.append(
                "[D14] USER_MANUAL does not state 'Evaluation 四子页' / "
                "'4 个子页' but workspace_evaluation.js::PAGES has exactly 4 entries."
            )
    else:
        errors.append(
            f"[D14] workspace_evaluation.js::PAGES has {eval_pages} entries "
            f"(expected 4 for v1.1.0 — Schemas/Run/Results/Aggregate). If "
            f"this is an intentional change, update the manual's §5 and "
            f"this smoke's expected value together."
        )

    # Fact 4 — Diagnostics sub-page count
    diag_src = _read(static_root / "ui" / "workspace_diagnostics.js")
    diag_pages = _count_dict_entries_in_array(diag_src, "PAGES", kind_filter="page")
    if diag_pages is None:
        errors.append("[D14] cannot locate `const PAGES = [...]` in static/ui/workspace_diagnostics.js")
    elif diag_pages == 5:
        if not re.search(r"五\s*子页|\b5\s*(?:个)?\s*子页", manual_src):
            errors.append(
                "[D14] USER_MANUAL does not state 'Diagnostics 五子页' / "
                "'5 个子页' but workspace_diagnostics.js::PAGES has exactly 5 entries."
            )
    else:
        errors.append(
            f"[D14] workspace_diagnostics.js::PAGES has {diag_pages} entries "
            f"(expected 5 for v1.1.0). If intentional, update §7 + smoke together."
        )

    # Fact 5 — Settings sub-page count
    settings_src = _read(static_root / "ui" / "workspace_settings.js")
    # workspace_settings.js PAGES entries are plain dicts without kind;
    # count unconditionally.
    settings_pages = _count_dict_entries_in_array(settings_src, "PAGES")
    if settings_pages is None:
        errors.append("[D14] cannot locate `const PAGES = [...]` in static/ui/workspace_settings.js")
    elif settings_pages == 6:
        if not re.search(r"六\s*子页|\b6\s*(?:个)?\s*子页", manual_src):
            errors.append(
                "[D14] USER_MANUAL does not state 'Settings 六子页' / "
                "'6 个子页' but workspace_settings.js::PAGES has exactly 6 entries."
            )
    else:
        errors.append(
            f"[D14] workspace_settings.js::PAGES has {settings_pages} entries "
            f"(expected 6 for v1.1.0). If intentional, update §8 + smoke together."
        )

    # Fact 6 — Memory trigger op count (memory_trigger_panel.js)
    mem_src = _read(static_root / "ui" / "setup" / "memory_trigger_panel.js")
    mem_ops = _count_memory_ops(mem_src)
    if mem_ops is None:
        errors.append("[D14] cannot locate `{ op: '...' }` literals in memory_trigger_panel.js")
    elif mem_ops == 5:
        if not re.search(r"\b5\s*个\s*(?:LLM\s*)?[Oo]p\b", manual_src):
            errors.append(
                "[D14] USER_MANUAL does not state '5 个 LLM Op' but "
                "memory_trigger_panel.js has exactly 5 op literals "
                "(recent.compress / facts.extract / reflect / persona.add_fact / "
                "persona.resolve_corrections). Check §4.2."
            )
    else:
        errors.append(
            f"[D14] memory_trigger_panel.js has {mem_ops} op literals "
            f"(expected 5 for v1.1.0). If intentional, update §4.2 + smoke together."
        )

    # Fact 7 — DATA_DIR path literal
    data_dir = getattr(tb_config, "DATA_DIR", None)
    if data_dir is not None:
        rel = f"tests/{data_dir.name}/"
        if rel not in manual_src:
            errors.append(
                f"[D14] USER_MANUAL does not contain the literal path "
                f"{rel!r} but tb_config.DATA_DIR resolves to "
                f"'{data_dir}'. The manual's §1.1 'data directory' note "
                f"must use the real runtime path, not a guess."
            )

    return errors


# ── entry point ────────────────────────────────────────────────────

CHECKS = (
    ("D1 — version metadata shape", check_version_metadata_shape),
    ("D2 — server.py version sourced from config", check_server_version_sourced_from_config),
    ("D3 — health_router /version endpoint sources config", check_health_router_version_endpoint),
    ("D4 — /docs/{doc_name} endpoint declared", check_docs_endpoint_declared),
    ("D5 — _PUBLIC_DOCS contains expected keys", check_public_docs_whitelist),
    ("D6 — whitelist values are .md basenames", check_whitelist_values_are_markdown_filenames),
    ("D7 — dual 404 error_type sentinels", check_dual_404_semantics),
    ("D8 — About page references /docs/", check_about_page_consumes_docs_endpoint),
    ("D9 — at least one whitelisted doc on disk", check_at_least_one_doc_on_disk),
    ("D10 — CHANGELOG has current version heading", check_changelog_has_current_version),
    ("D11 — rendered headings have ids", check_rendered_headings_have_ids),
    ("D12 — renderer strips .md suffix on whitelist cross-links", check_rendered_md_links_stripped),
    ("D13 — all in-doc anchor links resolve to a heading", check_in_doc_anchors_resolve),
    ("D14 — USER_MANUAL high-value tester-facts aligned", check_user_manual_high_value_facts),
)


def main() -> int:
    print("[p26_docs_endpoint_smoke] P26 Commit 1 docs-endpoint invariants")
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
    print("OK all P26 docs-endpoint contracts hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
