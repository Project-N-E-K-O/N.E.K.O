from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from plugin.server import install_registry


def test_builtin_install_registration_bootstraps_from_empty_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", {})

    galgame = install_registry.get_install_plugin_registration("galgame_plugin")
    study = install_registry.get_install_plugin_registration("study_companion")

    assert galgame is not None
    assert set(galgame.install_kinds) == {"rapidocr_models", "textractor"}
    assert galgame.tutorial_enabled is True
    assert galgame.ui_i18n_dir == (
        Path(install_registry.__file__).resolve().parents[1]
        / "plugins"
        / "galgame_plugin"
        / "i18n"
        / "ui"
    )

    assert study is not None
    assert set(study.install_kinds) == {"rapidocr_models", "tesseract"}
    assert study.tutorial_enabled is True


def test_builtin_install_registration_does_not_overwrite_existing_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", {})
    install_registry.register_install_plugin(
        "study_companion",
        install_kinds={},
        ui_i18n_dir=tmp_path,
    )

    study = install_registry.get_install_plugin_registration("study_companion")

    assert study is not None
    assert study.install_kinds == {}
    assert study.ui_i18n_dir == tmp_path.resolve()


def test_builtin_install_registration_skips_unavailable_optional_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(install_registry, "_install_plugin_registry", {})
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", {})
    monkeypatch.setattr(install_registry, "_plugin_module_available", lambda _plugin_id: False)

    assert install_registry.get_install_plugin_registration("galgame_plugin") is None
    assert install_registry.get_install_plugin_registration("study_companion") is None


def test_plugin_module_available_treats_import_errors_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_find_spec(_module_name: str):
        raise ModuleNotFoundError("No module named 'plugin.plugins'", name="plugin.plugins")

    monkeypatch.setattr(install_registry.importlib.util, "find_spec", fail_find_spec)

    assert install_registry._plugin_module_available("galgame_plugin") is False


def test_tutorial_migration_hooks_for_normalizes_plugin_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(install_registry, "_tutorial_migration_hooks", {})

    def migrate(_store_path: Path) -> None:
        pass

    install_registry.register_tutorial_migration_hook(
        migrate,
        plugin_id="study_companion",
    )

    assert install_registry.tutorial_migration_hooks_for(" study_companion ") == [
        migrate
    ]


@pytest.mark.parametrize(
    "missing_module",
    [
        "plugin.plugins",
        "plugin.plugins.galgame_plugin._tutorial_migration",
    ],
)
def test_builtin_galgame_migration_hook_ignores_missing_optional_plugin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_module: str,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "plugin.plugins.galgame_plugin._tutorial_migration":
            raise ModuleNotFoundError(
                f"No module named {missing_module!r}",
                name=missing_module,
            )
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    install_registry._copy_legacy_galgame_tutorial_progress_if_missing(
        tmp_path / "tutorial_progress.json",
    )


def test_builtin_galgame_migration_hook_reraises_unexpected_missing_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "plugin.plugins.galgame_plugin._tutorial_migration":
            raise ModuleNotFoundError(
                "No module named 'plugin.plugins.galgame_plugin.store'",
                name="plugin.plugins.galgame_plugin.store",
            )
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(ModuleNotFoundError):
        install_registry._copy_legacy_galgame_tutorial_progress_if_missing(
            tmp_path / "tutorial_progress.json",
        )
