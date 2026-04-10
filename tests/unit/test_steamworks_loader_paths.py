from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_main_server_source_mode_app_root_no_longer_uses_cwd():
    source = (REPO_ROOT / "main_server.py").read_text(encoding="utf-8")

    assert "return os.getcwd()" not in source
    assert "return os.path.dirname(os.path.abspath(__file__))" in source


@pytest.mark.unit
def test_steamworks_source_mode_app_root_no_longer_uses_cwd():
    source = (REPO_ROOT / "steamworks" / "__init__.py").read_text(encoding="utf-8")

    assert "return os.getcwd()" not in source
    assert "return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))" in source


@pytest.mark.unit
def test_steamworks_macos_load_error_includes_gatekeeper_guidance():
    source = (REPO_ROOT / "steamworks" / "__init__.py").read_text(encoding="utf-8")

    assert "macOS may be blocking" in source
    assert "xattr -dr com.apple.quarantine" in source
    assert "codesign --force --sign -" in source
