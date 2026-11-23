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
        "emote_maker",
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


class _DummyJsonRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_name", ["focus", "market"])
async def test_update_master_allows_unchanged_legacy_reserved_route_name_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_name: str
):
    """Existing legacy master profiles named after reserved routes can still update non-name fields, while creation and renames reject them."""
    import copy
    import json
    from fastapi.responses import JSONResponse
    from main_routers.characters_router import crud

    characters_file = tmp_path / "characters.json"

    class _LocalConfigManager:
        def load_characters(self):
            return json.loads(characters_file.read_text(encoding="utf-8"))

        async def aload_characters(self):
            return self.load_characters()

        async def asave_characters(self, data):
            characters_file.write_text(
                json.dumps(copy.deepcopy(data), ensure_ascii=False),
                encoding="utf-8",
            )

    cm = _LocalConfigManager()
    characters_file.write_text(
        json.dumps({"主人": {"档案名": legacy_name, "昵称": "旧名"}, "猫娘": {}, "当前猫娘": "小猫"}, ensure_ascii=False),
        encoding="utf-8",
    )

    async def _noop_init():
        return None

    monkeypatch.setattr(crud, "get_config_manager", lambda: cm)
    monkeypatch.setattr(crud, "get_initialize_character_data", lambda: _noop_init)

    # 1. Ordinary update without renaming keeps legacy reserved name working.
    ok_res = await crud.update_master(_DummyJsonRequest({"昵称": "小名"}))
    assert ok_res == {"success": True}
    assert cm.load_characters()["主人"]["档案名"] == legacy_name
    assert cm.load_characters()["主人"]["昵称"] == "小名"

    # 2. Renaming an existing master profile or creating a catgirl with a reserved route name is rejected.
    rename_res = await crud.rename_master(legacy_name, _DummyJsonRequest({"new_name": legacy_name}))
    assert isinstance(rename_res, JSONResponse)
    assert rename_res.status_code == 400

    catgirl_res = await crud.add_catgirl(_DummyJsonRequest({"档案名": legacy_name}))
    assert isinstance(catgirl_res, JSONResponse)
    assert catgirl_res.status_code == 400

    # 3. Creating a new master profile with a reserved route name is still rejected.
    characters_file.write_text(
        json.dumps({"主人": {}, "猫娘": {}, "当前猫娘": "小猫"}, ensure_ascii=False),
        encoding="utf-8",
    )
    create_res = await crud.update_master(_DummyJsonRequest({"档案名": legacy_name, "昵称": "小名"}))
    assert isinstance(create_res, JSONResponse)
    assert create_res.status_code == 400

    # 4. Repairing a legacy path-separator name via body fallback to a reserved route name is rejected,
    # and leaving an unsafe path-separator name unfixed also stays rejected.
    characters_file.write_text(
        json.dumps({"主人": {"档案名": "bad/name"}, "猫娘": {}, "当前猫娘": "小猫"}, ensure_ascii=False),
        encoding="utf-8",
    )
    fallback_res = await crud.update_master(_DummyJsonRequest({"档案名": legacy_name, "昵称": "小名"}))
    assert isinstance(fallback_res, JSONResponse)
    assert fallback_res.status_code == 400

    unchanged_unsafe_res = await crud.update_master(_DummyJsonRequest({"昵称": "小名"}))
    assert isinstance(unchanged_unsafe_res, JSONResponse)
    assert unchanged_unsafe_res.status_code == 400


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_name", ["focus", "market"])
async def test_save_character_card_allows_overwriting_existing_reserved_catgirl_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_name: str
):
    """Overwriting an existing catgirl card named after a reserved route succeeds, while saving a new one returns 400."""
    import copy
    import json
    from fastapi.responses import JSONResponse
    from main_routers.characters_router import cards

    characters_file = tmp_path / "characters.json"

    class _LocalConfigManager:
        def load_characters(self):
            return json.loads(characters_file.read_text(encoding="utf-8"))

        async def aload_characters(self):
            return self.load_characters()

        async def asave_characters(self, data):
            characters_file.write_text(
                json.dumps(copy.deepcopy(data), ensure_ascii=False),
                encoding="utf-8",
            )

        def get_character_data(self):
            return ("主人", {}, {}, {}, {}, {}, {}, {}, {})

    cm = _LocalConfigManager()
    monkeypatch.setattr(cards, "get_config_manager", lambda: cm)

    async def _noop_refresh(*_args, **_kwargs):
        return {"context_refreshed": False}

    async def _noop_greeting(*_args, **_kwargs):
        return (True, "")

    monkeypatch.setattr(cards, "_refresh_catgirl_context_after_profile_change", _noop_refresh)
    monkeypatch.setattr(cards, "_mark_new_character_greeting_pending_safe", _noop_greeting)

    # 1. Existing legacy catgirl card can be overwritten via /character-card/save.
    characters_file.write_text(
        json.dumps({"主人": {}, "猫娘": {legacy_name: {"档案名": legacy_name, "昵称": "old"}}, "当前猫娘": legacy_name}, ensure_ascii=False),
        encoding="utf-8",
    )
    ok_res = await cards._save_character_card_serialized({
        "character_card_name": legacy_name,
        "charaData": {"档案名": legacy_name, "昵称": "updated"},
    })
    assert isinstance(ok_res, dict) and ok_res.get("success") is True
    assert cm.load_characters()["猫娘"][legacy_name]["昵称"] == "updated"

    # 2. Saving a brand-new catgirl card named after a reserved route still returns 400.
    characters_file.write_text(
        json.dumps({"主人": {}, "猫娘": {}, "当前猫娘": "小猫"}, ensure_ascii=False),
        encoding="utf-8",
    )
    bad_res = await cards._save_character_card_serialized({
        "character_card_name": legacy_name,
        "charaData": {"档案名": legacy_name, "昵称": "new"},
    })
    assert isinstance(bad_res, JSONResponse)
    assert bad_res.status_code == 400
