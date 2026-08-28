"""覆盖 host 的用户插件安全导入兜底（_import_current_plugin_from_config / _import_plugin_module）。

重点：缺失/拼错的子模块不能被插件包的 __init__.py 静默顶替成功。
"""

from __future__ import annotations

import importlib
import importlib.machinery
import multiprocessing
import sys
from pathlib import Path
from types import ModuleType

import pytest

from plugin.core import host as host_module


def _probe_child_import_roots(
    builtin_root: str,
    user_root: str,
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
    settings.PLUGIN_CONFIG_ROOTS = (Path(builtin_root), Path(user_root))
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
    """隔离兜底导入会修改的两个插件命名空间。"""
    saved_path = sys.path[:]
    saved_modules = {
        key: value
        for key, value in sys.modules.items()
        if key == "plugins"
        or key.startswith("plugins.")
        or key.startswith("plugin.plugins.")
    }
    legacy_parent = sys.modules.get("plugin.plugins")
    saved_legacy_children = {
        key.split(".", 2)[2]: getattr(legacy_parent, key.split(".", 2)[2], None)
        for key in saved_modules
        if legacy_parent is not None
        and key.startswith("plugin.plugins.")
        and key.count(".") == 2
    }
    for key in list(saved_modules):
        sys.modules.pop(key, None)
    if legacy_parent is not None:
        for child_name, child_module in saved_legacy_children.items():
            if getattr(legacy_parent, child_name, None) is child_module:
                delattr(legacy_parent, child_name)
    try:
        yield
    finally:
        sys.path[:] = saved_path
        current_legacy_children = {
            key.split(".", 2)[2]
            for key in sys.modules
            if key.startswith("plugin.plugins.") and key.count(".") == 2
        }
        for key in [
            k
            for k in sys.modules
            if k == "plugins"
            or k.startswith("plugins.")
            or k.startswith("plugin.plugins.")
        ]:
            sys.modules.pop(key, None)
        legacy_parent = sys.modules.get("plugin.plugins")
        if legacy_parent is not None:
            for child_name in current_legacy_children:
                if hasattr(legacy_parent, child_name):
                    delattr(legacy_parent, child_name)
        sys.modules.update(saved_modules)
        if legacy_parent is not None:
            for child_name, child_module in saved_legacy_children.items():
                setattr(legacy_parent, child_name, child_module)


def _make_user_plugin(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "user_root" / "plugins" / "myplug"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text("class MyPlugin:\n    pass\n", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='myplug'\n", encoding="utf-8")
    return config_path


def _make_importable_plugin(root: Path, plugin_id: str, source: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(f"SOURCE = {source!r}\n", encoding="utf-8")
    config_path = plugin_dir / "plugin.toml"
    config_path.write_text(f"[plugin]\nid={plugin_id!r}\n", encoding="utf-8")
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
def test_failed_plugin_import_removes_loaded_children_only(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    config_path = _make_user_plugin(tmp_path)
    plugin_dir = config_path.parent
    (plugin_dir / "helper.py").write_text("VALUE = 'stale'\n", encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(
        "from . import helper\nraise RuntimeError('injected import failure')\n",
        encoding="utf-8",
    )
    unrelated = ModuleType("plugins.unrelated")
    sys.modules["plugins.unrelated"] = unrelated

    with pytest.raises(RuntimeError, match="injected import failure"):
        host_module._import_current_plugin_from_config(
            "plugins.myplug",
            config_path,
            _StubLogger(),
        )

    assert "plugins.myplug" not in sys.modules
    assert "plugins.myplug.helper" not in sys.modules
    assert not hasattr(sys.modules["plugins"], "myplug")
    assert sys.modules["plugins.unrelated"] is unrelated


@pytest.mark.plugin_unit
def test_manual_plugin_load_binds_child_on_plugins_namespace(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    config_path = _make_user_plugin(tmp_path)
    plugin_dir = config_path.parent
    (plugin_dir / "helper.py").write_text("VALUE = 'bound'\n", encoding="utf-8")
    (plugin_dir / "__init__.py").write_text(
        "import plugins.myplug.helper\nVALUE = plugins.myplug.helper.VALUE\n",
        encoding="utf-8",
    )

    module = host_module._import_current_plugin_from_config(
        "plugins.myplug",
        config_path,
        _StubLogger(),
    )

    assert module is not None
    assert module.VALUE == "bound"
    assert getattr(sys.modules["plugins"], "myplug") is module


@pytest.mark.plugin_unit
def test_namespace_plugin_loads_without_package_init(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    config_path = _make_user_plugin(tmp_path)
    plugin_dir = config_path.parent
    (plugin_dir / "__init__.py").unlink()
    (plugin_dir / "main.py").write_text("VALUE = 'namespace-entry'\n", encoding="utf-8")

    module = host_module._import_current_plugin_from_config(
        "plugins.myplug.main",
        config_path,
        _StubLogger(),
    )

    assert module is not None
    assert module.VALUE == "namespace-entry"
    plugin_package = getattr(sys.modules["plugins"], "myplug")
    assert str(plugin_dir) in plugin_package.__path__


@pytest.mark.plugin_unit
def test_existing_plugins_namespace_cannot_import_unselected_sibling(
    _isolate_plugins_namespace, tmp_path: Path
) -> None:
    stale_root = tmp_path / "stale-root"
    sibling_dir = stale_root / "unselected"
    sibling_dir.mkdir(parents=True)
    (sibling_dir / "__init__.py").write_text("LOADED = True\n", encoding="utf-8")
    namespace = ModuleType("plugins")
    namespace.__path__ = [str(stale_root)]
    namespace.__spec__ = importlib.machinery.ModuleSpec(
        "plugins",
        loader=None,
        is_package=True,
    )
    namespace.__spec__.submodule_search_locations = namespace.__path__
    sys.modules["plugins"] = namespace

    host_module._ensure_plugins_namespace(tmp_path / "selected-root", _StubLogger())

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("plugins.unselected")
    assert namespace.__path__ == []
    assert namespace.__spec__.submodule_search_locations == []


@pytest.mark.plugin_unit
def test_child_import_only_exposes_selected_plugin(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin" / "plugins"
    user_root = tmp_path / "user" / "plugins"
    selected_dir = user_root / "selected"
    sibling_dir = user_root / "json"
    builtin_root.mkdir(parents=True)
    selected_dir.mkdir(parents=True)
    sibling_dir.mkdir(parents=True)
    config_path = selected_dir / "plugin.toml"
    config_path.write_text("[plugin]\nid='selected'\n", encoding="utf-8")
    (selected_dir / "__init__.py").write_text("VALUE = 'selected'\n", encoding="utf-8")
    (sibling_dir / "__init__.py").write_text(
        "UNSELECTED_PLUGIN_SHADOW = True\n",
        encoding="utf-8",
    )

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_probe_child_import_roots,
        args=(str(builtin_root), str(user_root), str(config_path), result_queue),
    )
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 0
    shadowed, imported_from = result_queue.get(timeout=5)
    assert shadowed is False, imported_from


def test_child_import_roots_remove_shared_plugin_candidates(
    _isolate_plugins_namespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin import settings

    user_root = tmp_path / "user" / "plugins"
    builtin_root = tmp_path / "repo" / "plugin" / "plugins"
    user_root.mkdir(parents=True)
    builtin_root.mkdir(parents=True)
    monkeypatch.setattr(settings, "PLUGIN_CONFIG_ROOTS", (user_root, builtin_root))
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)

    # Simulate inherited candidate roots. Child setup must remove both instead
    # of exposing sibling plugins through top-level import resolution.
    sys.path[:0] = [str(builtin_root.parent), str(user_root.parent)]
    host_module._prepare_child_plugin_import_roots(_StubLogger())

    assert str(user_root.parent.resolve()) not in sys.path
    assert str(builtin_root.parent.resolve()) not in sys.path


@pytest.mark.plugin_unit
@pytest.mark.parametrize("has_builtin_collision", [True, False])
def test_child_import_prefers_current_user_plugin(
    _isolate_plugins_namespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_builtin_collision: bool,
) -> None:
    from plugin import settings

    plugin_id = "shared" if has_builtin_collision else "user_only"
    user_root = tmp_path / "user" / "plugins"
    builtin_root = tmp_path / "repo" / "plugin" / "plugins"
    config_path = _make_importable_plugin(user_root, plugin_id, "user")
    builtin_root.mkdir(parents=True, exist_ok=True)
    (builtin_root / "__init__.py").write_text("", encoding="utf-8")
    _make_importable_plugin(builtin_root, "_shared", "builtin_shared")
    if has_builtin_collision:
        _make_importable_plugin(builtin_root, plugin_id, "builtin")

    monkeypatch.setattr(settings, "PLUGIN_CONFIG_ROOTS", (user_root, builtin_root))
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)
    host_module._prepare_child_plugin_import_roots(_StubLogger())

    module = host_module._import_plugin_module(
        f"plugins.{plugin_id}",
        config_path,
        _StubLogger(),
    )

    assert module.SOURCE == "user"
    assert Path(module.__file__).resolve().is_relative_to(user_root.resolve())
    namespace_paths = [Path(path).resolve() for path in sys.modules["plugins"].__path__]
    assert namespace_paths == []
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("plugins._shared")


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("initial_source", "effective_source"),
    [("builtin", "user"), ("user", "builtin")],
)
def test_child_import_evicts_cached_same_id_from_previous_source(
    _isolate_plugins_namespace,
    tmp_path: Path,
    initial_source: str,
    effective_source: str,
) -> None:
    plugin_id = "shared"
    user_root = tmp_path / "user" / "plugins"
    builtin_root = tmp_path / "repo" / "plugin" / "plugins"
    configs = {
        "user": _make_importable_plugin(user_root, plugin_id, "user"),
        "builtin": _make_importable_plugin(builtin_root, plugin_id, "builtin"),
    }
    for root in (user_root, builtin_root):
        (root / "__init__.py").write_text("", encoding="utf-8")
        (root / plugin_id / "marker.py").write_text(
            f"SOURCE = {root.parent.name!r}\n",
            encoding="utf-8",
        )

    initial_root = user_root if initial_source == "user" else builtin_root
    sys.path.insert(0, str(initial_root.parent))
    importlib.invalidate_caches()
    initial_module = importlib.import_module(f"plugins.{plugin_id}")
    importlib.import_module(f"plugins.{plugin_id}.marker")
    assert initial_module.SOURCE == initial_source

    effective_module = host_module._import_plugin_module(
        f"plugins.{plugin_id}",
        configs[effective_source],
        _StubLogger(),
    )

    effective_root = user_root if effective_source == "user" else builtin_root
    assert effective_module.SOURCE == effective_source
    assert Path(effective_module.__file__).resolve().is_relative_to(effective_root.resolve())
    assert f"plugins.{plugin_id}.marker" not in sys.modules


@pytest.mark.plugin_unit
def test_import_plugin_module_reuses_cached_builtin_from_same_source(
    _isolate_plugins_namespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "same_source"
    builtin_root = tmp_path / "builtin" / "plugins"
    builtin_config = _make_importable_plugin(builtin_root, plugin_id, "builtin")
    legacy_parent = importlib.import_module("plugin.plugins")
    monkeypatch.setattr(legacy_parent, "__path__", [str(builtin_root)])
    if legacy_parent.__spec__ is not None:
        monkeypatch.setattr(
            legacy_parent.__spec__,
            "submodule_search_locations",
            legacy_parent.__path__,
        )
    cached = importlib.import_module(f"plugin.plugins.{plugin_id}")

    imported = host_module._import_plugin_module(
        f"plugin.plugins.{plugin_id}",
        builtin_config,
        _StubLogger(),
    )

    assert imported is cached


@pytest.mark.plugin_unit
def test_explicit_cache_eviction_reloads_replaced_same_path_package(
    _isolate_plugins_namespace,
    tmp_path: Path,
) -> None:
    plugin_id = "same_path_replacement"
    plugin_root = tmp_path / "installations" / "plugins"
    config_path = _make_importable_plugin(plugin_root, plugin_id, "old")
    plugin_dir = config_path.parent
    (plugin_dir / "marker.py").write_text("SOURCE = 'old'\n", encoding="utf-8")

    old_module = host_module._import_plugin_module(
        f"plugins.{plugin_id}",
        config_path,
        _StubLogger(),
    )
    old_marker = importlib.import_module(f"plugins.{plugin_id}.marker")
    assert old_module.SOURCE == "old"
    assert old_marker.SOURCE == "old"

    backup_dir = plugin_root / f".{plugin_id}.backup"
    plugin_dir.rename(backup_dir)
    replacement_config = _make_importable_plugin(plugin_root, plugin_id, "replacement")
    (replacement_config.parent / "marker.py").write_text(
        "SOURCE = 'replacement'\n",
        encoding="utf-8",
    )

    host_module.evict_cached_plugin_modules(plugin_id)
    replacement_module = host_module._import_plugin_module(
        f"plugins.{plugin_id}",
        replacement_config,
        _StubLogger(),
    )
    replacement_marker = importlib.import_module(f"plugins.{plugin_id}.marker")

    assert replacement_module is not old_module
    assert replacement_module.SOURCE == "replacement"
    assert replacement_marker.SOURCE == "replacement"
    assert sys.modules[f"plugin.plugins.{plugin_id}"] is replacement_module


@pytest.mark.plugin_unit
def test_user_plugin_absolute_self_import_uses_selected_source_and_can_restore_builtin(
    _isolate_plugins_namespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_id = "absolute_self_import"
    user_root = tmp_path / "user" / "plugins"
    builtin_root = tmp_path / "builtin" / "plugins"
    user_config = _make_importable_plugin(user_root, plugin_id, "user")
    builtin_config = _make_importable_plugin(builtin_root, plugin_id, "builtin")
    for root, source in ((user_root, "user"), (builtin_root, "builtin")):
        (root / plugin_id / "helper.py").write_text(
            f"SOURCE = {source!r}\n",
            encoding="utf-8",
        )
    (user_root / plugin_id / "__init__.py").write_text(
        f"from plugin.plugins.{plugin_id}.helper import SOURCE\n",
        encoding="utf-8",
    )

    legacy_parent = importlib.import_module("plugin.plugins")
    monkeypatch.setattr(legacy_parent, "__path__", [str(builtin_root)])
    if legacy_parent.__spec__ is not None:
        monkeypatch.setattr(
            legacy_parent.__spec__,
            "submodule_search_locations",
            legacy_parent.__path__,
        )

    user_module = host_module._import_plugin_module(
        f"plugins.{plugin_id}",
        user_config,
        _StubLogger(),
    )

    assert user_module.SOURCE == "user"
    assert sys.modules[f"plugin.plugins.{plugin_id}"] is user_module
    legacy_helper = sys.modules[f"plugin.plugins.{plugin_id}.helper"]
    assert Path(legacy_helper.__file__).resolve().is_relative_to(user_root.resolve())

    builtin_module = host_module._import_plugin_module(
        f"plugin.plugins.{plugin_id}",
        builtin_config,
        _StubLogger(),
    )

    assert builtin_module.SOURCE == "builtin"
    assert Path(builtin_module.__file__).resolve().is_relative_to(builtin_root.resolve())
    assert getattr(legacy_parent, plugin_id) is builtin_module
