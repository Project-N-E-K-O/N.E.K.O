from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.core import registry as module


def _fake_distribution(name: str, version: str) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"Name": name, "Version": version},
        name=name,
        version=version,
    )


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_detects_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.python_dependencies.importlib_metadata,
        "distributions",
        lambda: [_fake_distribution("demo-lib", "1.0.0")],
    )

    missing = module._find_missing_python_requirements(["demo-lib>=2.0"])

    assert missing == ["demo-lib>=2.0"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_skips_non_applicable_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.python_dependencies.importlib_metadata, "distributions", lambda: [])

    missing = module._find_missing_python_requirements(
        ['demo-lib>=2.0; python_version < "0"']
    )

    assert missing == []


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_uses_explicit_vendor_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_distributions(**kwargs: object) -> list[SimpleNamespace]:
        calls.append(kwargs.get("path"))
        return [_fake_distribution("demo-lib", "2.1.0")]

    monkeypatch.setattr(module.python_dependencies.importlib_metadata, "distributions", fake_distributions)

    missing = module._find_missing_python_requirements(
        ["demo-lib>=2.0"],
        search_paths=["/tmp/plugin/vendor"],
    )

    assert missing == []
    assert calls == [["/tmp/plugin/vendor"]]


@pytest.mark.plugin_unit
def test_plugin_dependencies_shortcut_is_plugin_dependency_not_python_requirement() -> None:
    conf = {"plugin": {"dependencies": ["provider_plugin"]}}

    dependencies = module._parse_plugin_dependencies(conf, module._DEFAULT_LOGGER, "consumer_plugin")

    assert len(dependencies) == 1
    assert dependencies[0].id == "provider_plugin"
    assert dependencies[0].untested is None
