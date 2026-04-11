import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_main_routers_exports_workshop_router_module_instead_of_router_alias():
    init_source = (REPO_ROOT / "main_routers" / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(init_source)

    has_module_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module is None
        and any(alias.name == "workshop_router" for alias in node.names)
        for node in tree.body
    )
    has_router_alias_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "workshop_router"
        and any(alias.name == "router" and alias.asname == "workshop_router" for alias in node.names)
        for node in tree.body
    )

    assert has_module_import is True
    assert has_router_alias_import is False


@pytest.mark.unit
def test_main_server_uses_importlib_for_workshop_background_task_state():
    server_source = (REPO_ROOT / "main_server.py").read_text(encoding="utf-8")
    tree = ast.parse(server_source)

    has_importlib_dynamic_import = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "main_routers.workshop_router"
        for node in ast.walk(tree)
    )
    has_module_alias_import = any(
        (
            isinstance(node, ast.Import)
            and any(alias.name == "main_routers.workshop_router" for alias in node.names)
        )
        for node in tree.body
    )

    assert has_importlib_dynamic_import is True
    assert has_module_alias_import is False
