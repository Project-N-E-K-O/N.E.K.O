"""Unit tests for config.network port_config.json validation and overrides."""

import json
import sys

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_json",
    [
        "null",
        "[]",
        "[48911]",
        "123",
        '"48911"',
        "true",
        "{not valid json",
    ],
)
def test_read_port_overrides_rejects_non_dict_or_malformed_json(
    tmp_path, monkeypatch, raw_json
):
    from config import network

    app_dir = tmp_path / "N.E.K.O"
    app_dir.mkdir(parents=True)
    (app_dir / "port_config.json").write_text(raw_json, encoding="utf-8")

    monkeypatch.setattr(network.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert network._read_port_overrides() == {}


@pytest.mark.unit
def test_read_port_overrides_and_env_priority(tmp_path, monkeypatch):
    from config import network

    app_dir = tmp_path / "N.E.K.O"
    app_dir.mkdir(parents=True)
    (app_dir / "port_config.json").write_text(
        json.dumps({"MAIN_SERVER_PORT": 50001}), encoding="utf-8"
    )

    monkeypatch.setattr(network.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    overrides = network._read_port_overrides()
    assert overrides == {"MAIN_SERVER_PORT": 50001}

    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    monkeypatch.setattr(network, "_PORT_FILE_OVERRIDES", overrides)
    assert network._read_port_env("MAIN_SERVER_PORT", 48911) == 50001

    monkeypatch.setenv("NEKO_MAIN_SERVER_PORT", "50002")
    assert network._read_port_env("MAIN_SERVER_PORT", 48911) == 50002


@pytest.mark.unit
@pytest.mark.parametrize("bad_overrides", [[], None, "48911", {"MAIN_SERVER_PORT": True}])
def test_read_port_env_handles_non_dict_file_overrides_and_bool(monkeypatch, bad_overrides):
    from config import network

    monkeypatch.delenv("NEKO_MAIN_SERVER_PORT", raising=False)
    monkeypatch.delenv("MAIN_SERVER_PORT", raising=False)
    monkeypatch.setattr(network, "_PORT_FILE_OVERRIDES", bad_overrides)

    assert network._read_port_env("MAIN_SERVER_PORT", 48911) == 48911
    assert sys is not None
