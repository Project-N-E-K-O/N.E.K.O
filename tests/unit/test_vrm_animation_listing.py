import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.responses import JSONResponse
import pytest

pytestmark = pytest.mark.unit_fast


def test_animation_endpoint_excludes_only_bundled_movement(tmp_path):
    # Execute the real endpoint without importing unrelated workshop services.
    source_path = Path(__file__).resolve().parents[2] / "main_routers/vrm_router.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    endpoint = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "get_vrm_animations")
    endpoint.decorator_list = []
    static_dir = tmp_path / "static/vrm/animation"
    user_dir = tmp_path / "user/animation"
    static_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    for name in ["world-walk.vrma.gz", "world-walk.vrma", "wait01.vrma.gz", "pose.vrma"]:
        (static_dir / name).write_bytes(b"fixture")
    (user_dir / "world-walk.vrma").write_bytes(b"user animation")
    config = SimpleNamespace(project_root=tmp_path, vrm_animation_dir=user_dir,
                             ensure_vrm_directory=lambda: None)
    namespace = {"get_config_manager": lambda: config, "JSONResponse": JSONResponse,
                 "logger": Mock(), "VRM_STATIC_ANIMATION_PATH": "/static/vrm/animation"}
    exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(source_path), "exec"), namespace)
    response = namespace["get_vrm_animations"]()
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["success"] is True
    assert {item["url"] for item in payload["animations"]} == {
        "/static/vrm/animation/wait01.vrma.gz", "/static/vrm/animation/pose.vrma",
        "/user_vrm/animation/world-walk.vrma",
    }
    assert (static_dir / "world-walk.vrma.gz").exists()
