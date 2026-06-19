from __future__ import annotations

import pytest

from plugin.core.dependency import _parse_plugin_dependencies


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        return

    def exception(self, *_args, **_kwargs) -> None:
        return


@pytest.mark.plugin_unit
def test_parse_plugin_dependencies_translates_simple_plugin_id_list() -> None:
    dependencies = _parse_plugin_dependencies(
        {
            "plugin": {
                "dependencies": ["provider", "provider", " other_provider "],
            }
        },
        _Logger(),
        "consumer",
    )

    assert [dep.id for dep in dependencies] == ["provider", "other_provider"]
    assert [dep.untested for dep in dependencies] == [">=0", ">=0"]
