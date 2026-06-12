"""覆盖 host 的用户插件安全导入兜底（_import_current_plugin_from_config / _import_plugin_module）。

重点：缺失/拼错的子模块不能被插件包的 __init__.py 静默顶替成功。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from plugin.core import host as host_module


class _StubLogger:
    def debug(self, *_args, **_kwargs) -> None:
        return

    def info(self, *_args, **_kwargs) -> None:
        return

    def warning(self, *_args, **_kwargs) -> None:
        return


@pytest.fixture
def _isolate_plugins_namespace():
    """隔离全局 sys.path / sys.modules['plugins*']，兜底会改这些全局状态。"""
    saved_path = sys.path[:]
    saved_modules = {
        key: value
        for key, value in sys.modules.items()
        if key == "plugins" or key.startswith("plugins.")
    }
    for key in list(saved_modules):
        sys.modules.pop(key, None)
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for key in [k for k in sys.modules if k == "plugins" or k.startswith("plugins.")]:
            sys.modules.pop(key, None)
        sys.modules.update(saved_modules)


def _make_user_plugin(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "user_root" / "plugins" / "myplug"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("class MyPlugin:\n    pass\n", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='myplug'\n", encoding="utf-8")
    return config_path


@pytest.mark.plugin_unit
def test_import_current_plugin_loads_package_from_config(_isolate_plugins_namespace, tmp_path: Path) -> None:
    config_path = _make_user_plugin(tmp_path)
    mod = host_module._import_current_plugin_from_config("plugins.myplug", config_path, _StubLogger())
    assert mod is not None
    assert getattr(mod, "MyPlugin", None) is not None


@pytest.mark.plugin_unit
def test_import_current_plugin_does_not_mask_missing_submodule(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    config_path = _make_user_plugin(tmp_path)
    # plugins.myplug.missing 不存在：兜底必须返回 None，而不是拿 __init__.py 顶替成功。
    mod = host_module._import_current_plugin_from_config("plugins.myplug.missing", config_path, _StubLogger())
    assert mod is None
    assert "plugins.myplug.missing" not in sys.modules


@pytest.mark.plugin_unit
def test_import_plugin_module_raises_for_missing_submodule(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    config_path = _make_user_plugin(tmp_path)
    with pytest.raises(ModuleNotFoundError):
        host_module._import_plugin_module("plugins.myplug.missing", config_path, _StubLogger())
