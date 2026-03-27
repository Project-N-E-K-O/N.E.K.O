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
        module.importlib_metadata,
        "distributions",
        lambda: [_fake_distribution("demo-lib", "1.0.0")],
    )

    missing = module._find_missing_python_requirements(["demo-lib>=2.0"])

    assert missing == ["demo-lib>=2.0"]


@pytest.mark.plugin_unit
def test_find_missing_python_requirements_skips_non_applicable_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.importlib_metadata, "distributions", lambda: [])

    missing = module._find_missing_python_requirements(
        ['demo-lib>=2.0; python_version < "0"']
    )

    assert missing == []
