from pathlib import Path

import pytest


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-desktop.yml"


@pytest.mark.unit
def test_macos_pyobjc_build_uses_background_app_with_electron_wrapper():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'NUITKA_OPTS="--mode=app --macos-app-mode=background"' in workflow
    assert "dist/build_nuitka_launcher.app" in workflow
    assert "dist/Xiao8/projectneko_server.app" in workflow
    assert 'projectneko_server.app/Contents/MacOS/projectneko_server' in workflow
    assert (
        "chmod +x electron-app/bin/projectneko_server.app/Contents/MacOS/"
        "projectneko_server"
    ) in workflow
    assert "Re-sign macOS backend after post-processing" in workflow
    assert (
        "codesign --force --deep --sign - "
        "dist/Xiao8/projectneko_server.app"
    ) in workflow
    assert (
        "codesign --verify --deep --strict "
        "dist/Xiao8/projectneko_server.app"
    ) in workflow
    assert 'NEKO_NUITKA_RUNTIME_DIR=$RUNTIME_DIR' in workflow


@pytest.mark.unit
def test_macos_build_no_longer_excludes_pyobjc_bridge_modules():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for module in (
        "Foundation",
        "AppKit",
        "objc",
        "PyObjCTools",
        "CoreFoundation",
        "Quartz",
    ):
        assert f"--nofollow-import-to={module}" not in workflow
