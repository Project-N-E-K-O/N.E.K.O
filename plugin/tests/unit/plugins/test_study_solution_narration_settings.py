from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

import pytest

from plugin.plugins.study_companion.entry_status_entries import (
    _StatusEntriesMixin,
    _apply_settings_config,
    _communication_status_payload,
    _settings_config_payload,
)
from plugin.plugins.study_companion.models import (
    CommunicationConfig,
    StudyConfig,
    build_config,
)
from plugin.sdk.plugin import Ok


PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
STATIC_DIR = PLUGIN_DIR / "static"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


class _SettingsOwner(_StatusEntriesMixin):
    def __init__(self, config: StudyConfig) -> None:
        self._cfg = config
        self._ocr_pipeline = None
        self._agent = None
        self._pomodoro_timer = None
        self._supervision = None
        self._checkin_manager = None
        self.refresh_calls = 0
        self.persist_calls = 0
        self.persisted_config: dict | None = None

    async def _refresh_dependency_status(self) -> dict:
        self.refresh_calls += 1
        return {}

    async def _persist_state(self) -> None:
        self.persist_calls += 1
        self.persisted_config = self._cfg.to_dict()


def test_general_narration_setting_defaults_true_and_old_config_remains_compatible() -> None:
    defaults = CommunicationConfig()
    restored = build_config(
        {
            "communication": {
                "enabled": True,
                "solution_narration_enabled": False,
            }
        }
    )

    assert defaults.general_narration_enabled is True
    assert restored.communication.general_narration_enabled is True


@pytest.mark.asyncio
async def test_general_narration_setting_round_trips_and_updates_without_restart() -> None:
    owner = _SettingsOwner(StudyConfig())

    result = await owner.study_update_settings_config(
        config={"communication": {"general_narration_enabled": False}}
    )

    assert isinstance(result, Ok)
    assert owner._cfg.communication.enabled is True
    assert owner._cfg.communication.solution_narration_enabled is True
    assert owner._cfg.communication.general_narration_enabled is False
    assert result.value["config"]["communication"]["general_narration_enabled"] is False
    assert owner.persisted_config is not None
    assert owner.persisted_config["communication"]["general_narration_enabled"] is False
    assert "general_narration_enabled" not in _communication_status_payload(owner)


def test_communication_config_defaults_normalizes_booleans_and_serializes() -> None:
    defaults = CommunicationConfig()

    assert defaults.enabled is True
    assert defaults.solution_narration_enabled is True
    assert defaults.general_narration_enabled is True
    assert defaults.to_dict() == {
        "enabled": True,
        "solution_narration_enabled": True,
        "general_narration_enabled": True,
    }

    disabled = CommunicationConfig(
        enabled=0,
        solution_narration_enabled=0,
        general_narration_enabled=0,
    )
    enabled = CommunicationConfig(
        enabled=1,
        solution_narration_enabled=1,
        general_narration_enabled=1,
    )

    assert disabled.to_dict() == {
        "enabled": False,
        "solution_narration_enabled": False,
        "general_narration_enabled": False,
    }
    assert enabled.to_dict() == {
        "enabled": True,
        "solution_narration_enabled": True,
        "general_narration_enabled": True,
    }


def test_build_config_reads_nested_and_persisted_top_level_communication() -> None:
    nested = build_config(
        {
            "study_companion": {
                "communication": {
                    "enabled": False,
                    "solution_narration_enabled": True,
                    "general_narration_enabled": False,
                }
            }
        }
    )
    restored = build_config(
        {
            "communication": {
                "enabled": True,
                "solution_narration_enabled": False,
            }
        }
    )

    assert nested.communication.to_dict() == {
        "enabled": False,
        "solution_narration_enabled": True,
        "general_narration_enabled": False,
    }
    assert restored.communication.to_dict() == {
        "enabled": True,
        "solution_narration_enabled": False,
        "general_narration_enabled": True,
    }


def test_settings_payload_and_apply_round_trip_communication_booleans() -> None:
    current = StudyConfig(
        communication=CommunicationConfig(
            enabled=True,
            solution_narration_enabled=True,
        )
    )

    payload = _settings_config_payload(current)
    updated = _apply_settings_config(
        current,
        {
            "communication": {
                "enabled": "false",
                "solution_narration_enabled": "off",
                "general_narration_enabled": "no",
            }
        },
    )

    assert payload["communication"] == {
        "enabled": True,
        "solution_narration_enabled": True,
        "general_narration_enabled": True,
    }
    assert updated.communication.to_dict() == {
        "enabled": False,
        "solution_narration_enabled": False,
        "general_narration_enabled": False,
    }


@pytest.mark.asyncio
async def test_settings_update_applies_solution_narration_immediately_and_persists() -> None:
    owner = _SettingsOwner(
        StudyConfig(
            communication=CommunicationConfig(
                enabled=True,
                solution_narration_enabled=True,
            )
        )
    )

    result = await owner.study_update_settings_config(
        config={"communication": {"solution_narration_enabled": False}}
    )

    assert isinstance(result, Ok)
    assert owner._cfg.communication.enabled is True
    assert owner._cfg.communication.solution_narration_enabled is False
    assert result.value["config"]["communication"] == {
        "enabled": True,
        "solution_narration_enabled": False,
        "general_narration_enabled": True,
    }
    assert owner.refresh_calls == 1
    assert owner.persist_calls == 1
    assert owner.persisted_config is not None
    assert owner.persisted_config["communication"] == {
        "enabled": True,
        "solution_narration_enabled": False,
        "general_narration_enabled": True,
    }


def test_plugin_toml_enables_solution_narration_by_default() -> None:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)

    assert manifest["study_companion"]["communication"] == {
        "enabled": True,
        "solution_narration_enabled": True,
        "general_narration_enabled": True,
    }


def test_static_settings_exposes_neko_solution_narration_checkbox() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    checkbox = re.search(
        r'<input(?=[^>]*\bid="settingsSolutionNarrationEnabled")'
        r'(?=[^>]*\btype="checkbox")[^>]*>',
        html,
    )

    assert checkbox is not None
    assert (
        'data-i18n="ui.settings.communication.title">N.E.K.O proactive communication'
        in html
    )
    assert (
        'data-i18n="ui.settings.solution_narration_enabled.label">'
        "Let N.E.K.O narrate completed solutions"
        in html
    )


def test_static_settings_applies_and_collects_solution_narration_config() -> None:
    source = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    assert (
        "const settingsSolutionNarrationEnabled = "
        "$id('settingsSolutionNarrationEnabled');"
        in source
    )
    assert re.search(r"const communication\s*=\s*config\.communication\s*\|\|\s*\{\};", source)
    assert re.search(
        r"settingsSolutionNarrationEnabled\.checked\s*=\s*"
        r"communication\.solution_narration_enabled\s*!==\s*false",
        source,
    )
    assert "ensureConfigSection(next, 'communication')" in source
    assert re.search(
        r"communication\.solution_narration_enabled\s*=\s*"
        r"settingsSolutionNarrationEnabled\s*\?\s*"
        r"settingsSolutionNarrationEnabled\.checked\s*:\s*true",
        source,
    )


def test_solution_narration_copy_is_complete_for_all_locales_and_never_mentions_yui() -> None:
    expected_titles = {
        "en": "N.E.K.O proactive communication",
        "es": "Comunicación proactiva de N.E.K.O",
        "ja": "N.E.K.O 能動通信",
        "ko": "N.E.K.O 능동 통신",
        "pt": "Comunicação proativa da N.E.K.O",
        "ru": "Инициативная связь N.E.K.O",
        "zh-CN": "N.E.K.O 主动通信",
        "zh-TW": "N.E.K.O 主動通訊",
    }
    expected_labels = {
        "en": "Let N.E.K.O narrate completed solutions",
        "es": "Permitir que N.E.K.O narre las soluciones completadas",
        "ja": "解答完了後に N.E.K.O が解説する",
        "ko": "풀이 완료 후 N.E.K.O가 설명",
        "pt": "Permitir que N.E.K.O narre as soluções concluídas",
        "ru": "Озвучивать готовые решения через N.E.K.O",
        "zh-CN": "解题完成后由 N.E.K.O 讲述",
        "zh-TW": "解題完成後由 N.E.K.O 講述",
    }
    bundles = {
        locale: json.loads((PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in LOCALES
    }

    assert {path.stem for path in (PLUGIN_DIR / "i18n").glob("*.json")} == set(LOCALES)
    baseline_keys = set(bundles["en"])
    for locale, bundle in bundles.items():
        assert set(bundle) == baseline_keys, locale
        assert bundle["ui.settings.communication.title"] == expected_titles[locale]
        assert (
            bundle["ui.settings.solution_narration_enabled.label"]
            == expected_labels[locale]
        )
        assert "yui" not in bundle["ui.settings.communication.title"].lower()
        assert "yui" not in bundle["ui.settings.solution_narration_enabled.label"].lower()


def test_web_and_electron_settings_share_the_registered_static_ui_path() -> None:
    plugin_source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    assert 'self.register_static_ui("static")' in plugin_source
    assert 'f"/plugin/{quote(self.plugin_id, safe=\'\')}/ui/"' in plugin_source
    assert "Fallback only for local/Electron loads" in html
    assert 'id="settingsSolutionNarrationEnabled"' in html
    assert "const RUNS_URL = '/runs';" in main_js
