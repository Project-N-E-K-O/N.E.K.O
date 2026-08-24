from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"
DIST_CHECK = ROOT / "scripts" / "check_nuitka_dist.py"

EXPECTED_RUNTIME_PACKAGES = (
    "bilibili_api",
    "Cryptodome",
    "chompjs",
    "frozendict",
    "qrcode",
    "qrcode_terminal",
)


def test_desktop_build_rejects_legacy_bilibili_sdk_install() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Verify Bilibili SDK dependency convergence" in workflow
    uninstall = "uv pip uninstall --python .venv bilibili-api-python"
    reinstall = "uv sync --group galgame --reinstall-package bilibili-api-dev"
    assert uninstall in workflow
    assert reinstall in workflow
    assert workflow.index(uninstall) < workflow.index(reinstall)
    assert 'expected = {"bilibili-api-dev": "18.0.0a1"}' in workflow
    assert "uv sync --reinstall-package bilibili-api-dev" in workflow
    assert "legacy bilibili-api-python files" in workflow
    assert "from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents" in workflow


def test_bilibili_login_runtime_packages_are_explicit_on_all_desktop_builds() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    unix_block, windows_block = workflow.split("- name: Build with Nuitka (Windows)", maxsplit=1)
    for package in EXPECTED_RUNTIME_PACKAGES:
        assert f'--include-package={package}"' in unix_block
        assert f"--include-package={package}" in windows_block


def test_nuitka_dist_requires_bilibili_native_crypto_runtime() -> None:
    dist_check = DIST_CHECK.read_text(encoding="utf-8")

    assert '("Cryptodome/Cipher", "_raw_aes.*")' in dist_check
