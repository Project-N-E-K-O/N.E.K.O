from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.infrastructure import config_profiles_write as module


@pytest.mark.plugin_unit
def test_delete_profile_config_removes_active_key_in_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='demo'\n", encoding="utf-8")

    profiles_path = plugin_dir / "profiles.toml"
    profiles_path.write_text(
        "[config_profiles]\nactive='dev'\n[config_profiles.files]\ndev='profiles/dev.toml'\n",
        encoding="utf-8",
    )

    captured_payloads: list[dict[str, object]] = []

    def _fake_atomic_dump_toml(*, target_path: Path, payload: dict[str, object], prefix: str) -> None:
        if target_path.name == "profiles.toml":
            captured_payloads.append(payload)

    monkeypatch.setattr(module, "tomli_w", object())
    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "_atomic_dump_toml", _fake_atomic_dump_toml)

    result = module.delete_profile_config(plugin_id="demo", profile_name="dev")

    assert result["removed"] is True
    assert captured_payloads, "profiles payload was not persisted"
    persisted_cfg = captured_payloads[-1]["config_profiles"]
    assert isinstance(persisted_cfg, dict)
    assert "active" not in persisted_cfg
