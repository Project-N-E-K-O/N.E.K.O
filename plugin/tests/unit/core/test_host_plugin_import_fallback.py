"""覆盖 host 的用户插件安全导入兜底（_import_current_plugin_from_config / _import_plugin_module）。

重点：缺失/拼错的子模块不能被插件包的 __init__.py 静默顶替成功。
"""

from __future__ import annotations

import sys
import multiprocessing
from pathlib import Path

import pytest

from plugin.core import host as host_module


def _probe_child_import_roots(
    builtin_root: str,
    managed_root: str,
    config_path: str,
    result_queue,
) -> None:
    from plugin import settings
    from plugin.core import host

    class _ChildLogger:
        def debug(self, *_args, **_kwargs) -> None:
            return

        def info(self, *_args, **_kwargs) -> None:
            return

    settings.BUILTIN_PLUGIN_CONFIG_ROOT = Path(builtin_root)
    settings.PLUGIN_CONFIG_ROOTS = (Path(builtin_root), Path(managed_root))
    host._prepare_child_plugin_import_roots(_ChildLogger())
    host._prepare_child_current_plugin_import_root(Path(config_path), _ChildLogger())

    for name in tuple(sys.modules):
        if name == "json" or name.startswith("json."):
            sys.modules.pop(name, None)
    import json

    result_queue.put((bool(getattr(json, "UNSELECTED_PLUGIN_SHADOW", False)), json.__file__))


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


@pytest.mark.plugin_unit
def test_child_import_only_exposes_selected_plugin(
    tmp_path: Path,
) -> None:
    builtin_root = tmp_path / "builtin" / "plugins"
    managed_root = tmp_path / "plugin-installations"
    selected_dir = managed_root / "selected"
    sibling_dir = managed_root / "json"
    builtin_root.mkdir(parents=True)
    selected_dir.mkdir(parents=True)
    sibling_dir.mkdir(parents=True)
    config_path = selected_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='selected'\n", encoding="utf-8")
    (sibling_dir / "__init__.py").write_text(
        "UNSELECTED_PLUGIN_SHADOW = True\n",
        encoding="utf-8",
    )

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_probe_child_import_roots,
        args=(str(builtin_root), str(managed_root), str(config_path), result_queue),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 0
    shadowed, imported_from = result_queue.get(timeout=5)
    assert shadowed is False, imported_from
