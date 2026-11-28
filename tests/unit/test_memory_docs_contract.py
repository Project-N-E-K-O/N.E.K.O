"""Contract tests verifying `docs/{,zh-CN/,ja/}api/rest/memory.md` cover all `/api/memory` routes (`R1111-P3-05`)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_ROUTER_DIR = REPO_ROOT / "main_routers/memory_router"
MEMORY_DOCS = (
    REPO_ROOT / "docs/api/rest/memory.md",
    REPO_ROOT / "docs/zh-CN/api/rest/memory.md",
    REPO_ROOT / "docs/ja/api/rest/memory.md",
)
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_SUMMARY_ROW_RE = re.compile(r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
_HEADING_ROUTE_RE = re.compile(r"^###\s*`(GET|POST|PUT|PATCH|DELETE)\s+([^`]+)`", re.MULTILINE)
# Baseline set of browser/settings routes documented by `docs/{,zh-CN/,ja/}api/rest/memory.md`.
# Using this verified baseline rather than asserting exact equality against the whole router avoids
# breaking PRs when separate feature PRs add new memory_router routes on `main`.
DOCUMENTED_MEMORY_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/api/memory/recent_files"),
    ("GET", "/api/memory/recent_file"),
    ("POST", "/api/memory/recent_file/save"),
    ("POST", "/api/memory/update_catgirl_name"),
    ("GET", "/api/memory/review_config"),
    ("POST", "/api/memory/review_config"),
    ("GET", "/api/memory/powerful_memory_config"),
    ("POST", "/api/memory/powerful_memory_config"),
    ("GET", "/api/memory/insight_characters"),
    ("POST", "/api/memory/repetition_insights"),
    ("POST", "/api/memory/repetition_effects/reset"),
    ("GET", "/api/memory/habitual_phrases"),
    ("POST", "/api/memory/habitual_phrases/override"),
    ("POST", "/api/memory/habitual_phrases/recompute"),
    ("POST", "/api/memory/external_import/preview"),
    ("POST", "/api/memory/external_import/commit"),
    ("GET", "/api/memory/external_import/progress"),
    ("GET", "/api/memory/farewell-summary"),
    ("GET", "/api/memory/legacy/scan"),
    ("POST", "/api/memory/legacy/purge"),
})


def _extract_memory_router_routes() -> list[tuple[str, str]]:
    prefix = ""
    routes: list[tuple[str, str]] = []
    source_paths = sorted(MEMORY_ROUTER_DIR.glob("*.py"))
    assert source_paths, f"No Python modules found under {MEMORY_ROUTER_DIR}"

    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "router"
                and isinstance(node.value, ast.Call)
            ):
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        prefix = kw.value.value

    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "router"
                        and dec.func.attr in _HTTP_METHODS
                        and dec.args
                        and isinstance(dec.args[0], ast.Constant)
                        and isinstance(dec.args[0].value, str)
                    ):
                        routes.append((dec.func.attr.upper(), prefix + dec.args[0].value))
    return routes


def test_memory_router_route_inventory_has_expected_routes_without_trailing_slash():
    """Sanity-check the AST extractor against `main_routers/memory_router/`."""
    routes = _extract_memory_router_routes()
    assert len(routes) >= 20
    for _method, path in routes:
        assert path.startswith("/api/memory/")
        assert not path.endswith("/")


@pytest.mark.parametrize("doc_path", MEMORY_DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_memory_docs_mirrors_document_all_memory_router_routes(doc_path: Path):
    """Assert documented routes exist in `main_routers/memory_router.py` and stay synchronized across mirrors."""
    router_routes = set(_extract_memory_router_routes())
    assert not (DOCUMENTED_MEMORY_ROUTES - router_routes), (
        f"Baseline routes missing from memory_router.py: {sorted(DOCUMENTED_MEMORY_ROUTES - router_routes)}"
    )

    text = doc_path.read_text(encoding="utf-8")

    summary_routes = {(m.upper(), p) for m, p in _SUMMARY_ROW_RE.findall(text)}
    heading_routes = {(m.upper(), p) for m, p in _HEADING_ROUTE_RE.findall(text)}

    missing_from_summary = DOCUMENTED_MEMORY_ROUTES - summary_routes
    missing_from_headings = DOCUMENTED_MEMORY_ROUTES - heading_routes
    stale_in_summary = summary_routes - router_routes
    stale_in_headings = heading_routes - router_routes

    assert not missing_from_summary, f"Routes missing from Endpoint summary in {doc_path}: {sorted(missing_from_summary)}"
    assert not missing_from_headings, f"Routes missing from reference sections in {doc_path}: {sorted(missing_from_headings)}"
    assert not stale_in_summary, f"Stale routes in Endpoint summary of {doc_path}: {sorted(stale_in_summary)}"
    assert not stale_in_headings, f"Stale routes in reference sections of {doc_path}: {sorted(stale_in_headings)}"
    assert summary_routes == heading_routes, (
        f"Summary and heading route mismatch in {doc_path}: {sorted(summary_routes ^ heading_routes)}"
    )
