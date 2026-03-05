from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.infrastructure.config_profiles import resolve_profile_path


@pytest.mark.plugin_unit
def test_resolve_profile_path_allows_path_under_base(tmp_path: Path) -> None:
    base_dir = tmp_path / "profiles"
    base_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_profile_path("dev.toml", base_dir)
    assert resolved is not None
    assert resolved == (base_dir / "dev.toml").resolve()


@pytest.mark.plugin_unit
def test_resolve_profile_path_rejects_path_escape(tmp_path: Path) -> None:
    base_dir = tmp_path / "profiles"
    base_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_profile_path("../outside.toml", base_dir)
    assert resolved is None

