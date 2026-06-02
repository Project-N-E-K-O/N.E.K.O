from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.core import python_dependencies
from plugin.core import registry as module


def _fake_distribution(name: str, version: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"Name": name, "Version": version},
        name=name,
        version=version,
    )


def _write_dist_info(vendor_dir: Path, name: str, version: str) -> None:
    dist_dir = vendor_dir / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_dir.mkdir(parents=True)
    (dist_dir / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )


@pytest.mark.plugin_unit
def test_collect_plugin_python_requirements_ignores_plugin_dependencies(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "demo_plugin"
    plugin_dir.mkdir(parents=True)
    toml_path = plugin_dir / "plugin.toml"
    toml_path.write_text("", encoding="utf-8")
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx>=0.27", "N.E.K.O>=0.1"]\n',
        encoding="utf-8",
    )

    requirements = module._collect_plugin_python_requirements(
        {"plugin": {"dependencies": ["shared_plugin"]}},
        toml_path,
        module._DEFAULT_LOGGER,
        "demo_plugin",
    )

    assert requirements == ["httpx>=0.27"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_checks_plugin_vendor(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "demo_plugin" / "vendor"
    _write_dist_info(vendor_dir, "demo-lib", "2.1.0")

    missing = module._find_missing_python_requirements(
        ["demo-lib>=2.0", "other-lib>=1"],
        search_paths=[vendor_dir],
    )

    assert missing == ["other-lib>=1"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_empty_search_paths_do_not_use_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        python_dependencies.importlib_metadata,
        "distributions",
        lambda: [_fake_distribution("demo-lib", "9.0.0")],
    )

    missing = module._find_missing_python_requirements(["demo-lib>=2.0"], search_paths=[])

    assert missing == ["demo-lib>=2.0"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_detects_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        python_dependencies.importlib_metadata,
        "distributions",
        lambda: [_fake_distribution("demo-lib", "1.0.0")],
    )

    missing = module._find_missing_python_requirements(["demo-lib>=2.0"])

    assert missing == ["demo-lib>=2.0"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_skips_non_applicable_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(python_dependencies.importlib_metadata, "distributions", lambda: [])

    missing = module._find_missing_python_requirements(
        ['demo-lib>=2.0; python_version < "0"']
    )

    assert missing == []
