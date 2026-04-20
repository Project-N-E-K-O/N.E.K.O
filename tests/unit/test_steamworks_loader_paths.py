import ast
import os
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
    source = (REPO_ROOT / "main_server.py").read_text(encoding="utf-8")

    assert _contains_os_getcwd_call(source) is False
    assert _function_returns_path_from_file(source, "_get_app_root", parent_dirs=1) is True


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
def test_steamworks_prepend_env_path_preserves_existing_entries_without_duplicates(monkeypatch):
    import steamworks as steamworks_module

    monkeypatch.setenv("LD_LIBRARY_PATH", os.pathsep.join(("/existing/lib", "/fallback/lib")))

    steamworks_module._prepend_env_path("LD_LIBRARY_PATH", "/new/lib")
    first_pass = os.environ["LD_LIBRARY_PATH"].split(os.pathsep)
    assert first_pass == ["/new/lib", "/existing/lib", "/fallback/lib"]

    steamworks_module._prepend_env_path("LD_LIBRARY_PATH", "/new/lib")
    second_pass = os.environ["LD_LIBRARY_PATH"].split(os.pathsep)
    assert second_pass == ["/new/lib", "/existing/lib", "/fallback/lib"]
