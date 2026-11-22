"""Contract tests verifying `docs/{,zh-CN/,ja/}api/rest/memory.md` cover all `/api/memory` routes (`R1111-P3-05`)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_ROUTER_PATH = REPO_ROOT / "main_routers/memory_router.py"
MEMORY_DOCS = (
    REPO_ROOT / "docs/api/rest/memory.md",
    REPO_ROOT / "docs/zh-CN/api/rest/memory.md",
    REPO_ROOT / "docs/ja/api/rest/memory.md",
)
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_SUMMARY_ROW_RE = re.compile(r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
_HEADING_ROUTE_RE = re.compile(r"^###\s*`(GET|POST|PUT|PATCH|DELETE)\s+([^`]+)`", re.MULTILINE)


def _extract_memory_router_routes() -> list[tuple[str, str]]:
    source = MEMORY_ROUTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MEMORY_ROUTER_PATH))
    prefix = ""
    routes: list[tuple[str, str]] = []

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
    """Sanity-check the AST extractor against `main_routers/memory_router.py`."""
    routes = _extract_memory_router_routes()
    assert len(routes) >= 20
    for _method, path in routes:
        assert path.startswith("/api/memory/")
        assert not path.endswith("/")


@pytest.mark.parametrize("doc_path", MEMORY_DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_memory_docs_mirrors_document_all_memory_router_routes(doc_path: Path):
    """Assert every route in `main_routers/memory_router.py` has a summary row and section heading."""
    expected_routes = set(_extract_memory_router_routes())
    text = doc_path.read_text(encoding="utf-8")

    summary_routes = {(m.upper(), p) for m, p in _SUMMARY_ROW_RE.findall(text)}
    heading_routes = {(m.upper(), p) for m, p in _HEADING_ROUTE_RE.findall(text)}

    missing_from_summary = expected_routes - summary_routes
    missing_from_headings = expected_routes - heading_routes

    assert not missing_from_summary, f"Routes missing from Endpoint summary in {doc_path}: {sorted(missing_from_summary)}"
    assert not missing_from_headings, f"Routes missing from reference sections in {doc_path}: {sorted(missing_from_headings)}"
