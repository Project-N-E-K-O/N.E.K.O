"""The InkAI install CLI must use the same guards as the router's package import."""

from __future__ import annotations

from contextlib import contextmanager
import json

import pytest

from scripts import validate_numeric_v2_story as cli
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


@pytest.fixture
def cli_env(tmp_path, monkeypatch, capsys):
    from utils import cloudsave_runtime, config_manager

    class _Config:
        app_docs_dir = str(tmp_path / "docs")

    fence = {"entered": 0, "blocked": False}

    @contextmanager
    def transaction(manager, *, operation="write", target=""):
        if fence["blocked"]:
            raise cloudsave_runtime.MaintenanceModeError("maintenance", operation=operation, target=target)
        fence["entered"] += 1
        yield

    monkeypatch.setattr(config_manager, "ConfigManager", _Config)
    monkeypatch.setattr(cloudsave_runtime, "cloudsave_writable_transaction", transaction)
    source = tmp_path / "story.json"
    source.write_text(json.dumps(numeric_v2_story(), ensure_ascii=False), encoding="utf-8")
    packages = tmp_path / "docs" / "theater" / "numeric_v2" / "packages"

    def run(*args):
        monkeypatch.setattr(cli.sys, "argv", ["validate_numeric_v2_story.py", str(source), *args])
        code = cli.main()
        return code, json.loads(capsys.readouterr().out.splitlines()[-1])

    return run, fence, source, packages


def test_install_writes_inside_the_cloudsave_write_transaction(cli_env):
    run, fence, _source, packages = cli_env
    code, result = run("--install")
    assert code == 0 and result["success"] is True
    assert fence["entered"] == 1
    assert (packages / "numeric_v2_contract.json").is_file()


def test_install_is_refused_during_cloudsave_maintenance(cli_env):
    run, fence, _source, packages = cli_env
    fence["blocked"] = True
    code, result = run("--install")
    assert code == 5
    assert result == {"success": False, "error": {"code": "CLOUDSAVE_WRITE_FENCE_ACTIVE", "retryable": True}}
    assert not packages.exists() or not any(packages.iterdir())


@pytest.mark.parametrize("install", [True, False])
def test_oversized_package_is_rejected_like_the_router(cli_env, monkeypatch, install):
    run, fence, source, packages = cli_env
    monkeypatch.setattr(cli, "MAX_PACKAGE_BYTES", source.stat().st_size - 1)
    code, result = run(*(["--install"] if install else []))
    assert code == 5
    assert result == {"success": False, "error": {"code": "numeric_story_package_too_large"}}
    assert fence["entered"] == 0
    assert not packages.exists()
