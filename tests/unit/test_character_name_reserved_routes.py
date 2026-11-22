"""Contract tests verifying RESERVED_ROUTE_NAMES covers all top-level routes and mounts."""

import ast
import re
from pathlib import Path

import pytest

from utils.character_name import RESERVED_ROUTE_NAMES, validate_character_name


ROOT = Path(__file__).resolve().parents[2]


def _extract_pages_router_single_segment_routes() -> set[str]:
    src = (ROOT / "main_routers" / "pages_router.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    routes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            func = dec.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "router":
                arg = dec.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    path = arg.value
                    if path == "/" or path == "/{lanlan_name}" or path.startswith("/api/"):
                        continue
                    parts = [p for p in path.split("/") if p]
                    if len(parts) == 1 and not parts[0].startswith("{"):
                        routes.add(parts[0])
    return routes


def _extract_web_app_single_segment_mounts_and_routes() -> set[str]:
    names: set[str] = set()
    for rel in ("app/main_server/web_app.py", "app/main_server/workshop_runtime.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "app"
                    and node.func.attr == "mount"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    parts = [p for p in node.args[0].value.split("/") if p]
                    if parts:
                        names.add(parts[0])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call) or not dec.args:
                        continue
                    func = dec.func
                    if (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "app"
                        and isinstance(dec.args[0], ast.Constant)
                        and isinstance(dec.args[0].value, str)
                    ):
                        path = dec.args[0].value
                        if path.startswith("/api/"):
                            continue
                        parts = [p for p in path.split("/") if p]
                        if parts and not parts[0].startswith("{"):
                            names.add(parts[0])
    return names


def _extract_js_set_strings(path: Path, const_name: str) -> set[str]:
    src = path.read_text(encoding="utf-8")
    pattern = rf"const\s+{re.escape(const_name)}\s*=\s*new\s+Set\(\[(.*?)\]\)"
    match = re.search(pattern, src, re.DOTALL)
    assert match, f"Could not find {const_name} in {path}"
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


@pytest.mark.unit
def test_reserved_route_names_cover_all_pages_mounts_and_frontend_paths():
    page_routes = _extract_pages_router_single_segment_routes()
    app_routes = _extract_web_app_single_segment_mounts_and_routes()
    index_reserved = _extract_js_set_strings(ROOT / "static" / "js" / "index.js", "RESERVED_PAGE_PATHS")

    assert not (page_routes - RESERVED_ROUTE_NAMES), f"Missing pages_router routes: {sorted(page_routes - RESERVED_ROUTE_NAMES)}"
    assert not (app_routes - RESERVED_ROUTE_NAMES), f"Missing web_app routes/mounts: {sorted(app_routes - RESERVED_ROUTE_NAMES)}"
    assert not (index_reserved - RESERVED_ROUTE_NAMES), f"Missing index.js paths: {sorted(index_reserved - RESERVED_ROUTE_NAMES)}"


@pytest.mark.unit
def test_frontend_character_profile_reserved_route_names_match_backend():
    frontend_reserved = _extract_js_set_strings(
        ROOT / "static" / "js" / "character_card_manager" / "character-data-and-transfer.js",
        "CHARACTER_PROFILE_RESERVED_ROUTE_NAMES",
    )
    assert RESERVED_ROUTE_NAMES <= frontend_reserved, (
        f"Frontend CHARACTER_PROFILE_RESERVED_ROUTE_NAMES missing: {sorted(RESERVED_ROUTE_NAMES - frontend_reserved)}"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "theater",
        "chara_manager",
        "visit_persona",
        "social_hub",
        "chat_full",
        "avatar_tool_editor",
        "web_chat_compact",
        "user_pngtuber",
        "user_avatar_tools",
        "market",
        "focus",
        "templates",
    ],
)
def test_newly_reserved_route_names_rejected_by_validator(name):
    res = validate_character_name(name)
    assert not res.ok
    assert res.code == "reserved_route_name"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["focus", "market", "theater", "templates"])
def test_existing_characters_with_reserved_route_names_allowed_on_data_paths(name):
    """Existing characters named after routes remain accessible for memory, cloudsave, migration, and import."""
    import memory as memory_pkg
    from fastapi import HTTPException
    from main_routers.memory_router import ExternalMemoryImportError, _prepare_external_import
    from app.memory_server._shared import validate_lanlan_name
    from utils.cloudsave_runtime._shared import audit_cloudsave_character_names

    assert validate_lanlan_name(name) == name
    with pytest.raises(HTTPException):
        validate_lanlan_name(f"../{name}")
    with pytest.raises(HTTPException):
        validate_lanlan_name("CON")

    audit = audit_cloudsave_character_names([name])
    assert audit["errors"] == []
    assert audit_cloudsave_character_names([f"../{name}"])["errors"][0]["type"] == "invalid_name"
    assert audit_cloudsave_character_names(["CON"])["errors"][0]["type"] == "invalid_name"

    assert memory_pkg._decoded_owner_is_safe(name) is True
    assert memory_pkg._decoded_owner_is_safe(f"../{name}") is False
    assert memory_pkg._decoded_owner_is_safe("CON") is False

    normalized, _analysis = _prepare_external_import({
        "character_name": name,
        "files": [{"path": "MEMORY.md", "content": "- tea lover"}],
    })
    assert normalized == name
    with pytest.raises(ExternalMemoryImportError, match="Invalid target character name"):
        _prepare_external_import({"character_name": f"../{name}", "files": []})
    with pytest.raises(ExternalMemoryImportError, match="Invalid target character name"):
        _prepare_external_import({"character_name": "CON", "files": []})
