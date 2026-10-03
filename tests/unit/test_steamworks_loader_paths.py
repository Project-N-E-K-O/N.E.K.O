import ast
import ctypes
import importlib
import os
import platform
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _contains_os_getcwd_call(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr == "getcwd"
        for node in ast.walk(tree)
    )


def _contains_literal(source: str, needle: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Constant) and isinstance(node.value, str) and needle in node.value
        for node in ast.walk(tree)
    )


def _function_returns_path_from_file(source: str, function_name: str, *, parent_dirs: int) -> bool:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Return):
                continue
            segment = ast.get_source_segment(source, child.value) or ""
            expected = "os.path.abspath(__file__)"
            if expected not in segment:
                continue
            return segment.count("os.path.dirname(") == parent_dirs
    return False


@pytest.mark.unit
def test_main_server_source_mode_app_root_no_longer_uses_cwd():
    source = (REPO_ROOT / "app" / "main_server" / "__init__.py").read_text(encoding="utf-8")

    assert _contains_os_getcwd_call(source) is False
    # __init__.py is at <repo>/app/main_server/__init__.py, so _get_app_root()
    # must climb three levels (dirname × 3) to land at the repo root.
    assert _function_returns_path_from_file(source, "_get_app_root", parent_dirs=3) is True


@pytest.mark.unit
def test_steamworks_source_mode_app_root_no_longer_uses_cwd():
    source = (REPO_ROOT / "steamworks" / "__init__.py").read_text(encoding="utf-8")

    assert _contains_os_getcwd_call(source) is False
    assert _function_returns_path_from_file(source, "_get_app_root", parent_dirs=2) is True


@pytest.mark.unit
def test_steamworks_macos_load_error_includes_gatekeeper_guidance():
    source = (REPO_ROOT / "steamworks" / "__init__.py").read_text(encoding="utf-8")

    assert _contains_literal(source, "macOS may be blocking") is True
    assert _contains_literal(source, "xattr -dr com.apple.quarantine") is True
    assert _contains_literal(source, "codesign --force --sign -") is True


@pytest.mark.unit
def test_config_manager_source_mode_project_root_ignores_cwd(tmp_path):
    import utils.config_manager as config_manager_module
    from utils.config_manager import ConfigManager

    fake_cwd = tmp_path / "elsewhere"
    fake_cwd.mkdir(parents=True, exist_ok=True)

    with patch.object(config_manager_module.Path, "cwd", return_value=fake_cwd), patch.object(
        ConfigManager,
        "_get_documents_directory",
        return_value=tmp_path,
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ):
        cm = ConfigManager("N.E.K.O")

    assert cm.project_root == REPO_ROOT
    assert cm.project_memory_dir == REPO_ROOT / "memory" / "store"


@pytest.mark.unit
def test_api_config_loader_source_mode_root_ignores_cwd(tmp_path):
    import utils.api_config_loader as api_config_loader

    fake_cwd = tmp_path / "elsewhere"
    fake_cwd.mkdir(parents=True, exist_ok=True)

    with patch.object(api_config_loader.Path, "cwd", return_value=fake_cwd):
        assert api_config_loader._get_app_root() == REPO_ROOT
        assert api_config_loader._get_config_file_path() == REPO_ROOT / "config" / "api_providers.json"


@pytest.mark.unit
def test_logger_config_source_mode_root_ignores_cwd(tmp_path):
    import utils.logger_config as logger_config

    fake_cwd = tmp_path / "elsewhere"
    fake_cwd.mkdir(parents=True, exist_ok=True)

    with patch.object(logger_config.Path, "cwd", return_value=fake_cwd):
        assert logger_config._get_application_root() == REPO_ROOT


@pytest.mark.unit
def test_importing_steamworks_does_not_mutate_library_search_path(monkeypatch):
    import steamworks as steamworks_module

    monkeypatch.setenv("LD_LIBRARY_PATH", os.pathsep.join(("/existing/lib", "/fallback/lib")))
    importlib.reload(steamworks_module)

    assert os.environ["LD_LIBRARY_PATH"].split(os.pathsep) == [
        "/existing/lib",
        "/fallback/lib",
    ]


@pytest.mark.unit
@pytest.mark.skipif(
    sys.platform not in ("linux", "linux2") or platform.machine() not in ("x86_64", "amd64"),
    reason="bundled Steamworks libraries are Linux x86_64 binaries",
)
def test_linux_steamworks_absolute_preload_does_not_need_ld_library_path(monkeypatch):
    steamworks_dir = REPO_ROOT / "steamworks"
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    steam_api = ctypes.CDLL(
        str(steamworks_dir / "libsteam_api.so"),
        mode=os.RTLD_GLOBAL | os.RTLD_LAZY,
    )
    wrapper = ctypes.CDLL(
        str(steamworks_dir / "SteamworksPy.so"),
        mode=os.RTLD_GLOBAL | os.RTLD_LAZY,
    )

    assert steam_api is not None
    assert wrapper is not None
