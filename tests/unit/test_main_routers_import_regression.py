from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_main_routers_exports_workshop_router_module_instead_of_router_alias():
    init_source = (REPO_ROOT / "main_routers" / "__init__.py").read_text(encoding="utf-8")

    assert "from . import workshop_router" in init_source
    assert "from .workshop_router import router as workshop_router" not in init_source


@pytest.mark.unit
def test_main_server_uses_importlib_for_workshop_background_task_state():
    server_source = (REPO_ROOT / "main_server.py").read_text(encoding="utf-8")

    assert 'importlib.import_module("main_routers.workshop_router")' in server_source
    assert "import main_routers.workshop_router as _wr" not in server_source
