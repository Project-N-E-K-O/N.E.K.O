from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from main_logic.core.notify import NotifyMixin
from main_logic.proactive_chat import service as proactive_service
from main_routers import websocket_router
from main_routers.game_router import char_info as game_char_info


class _NotifyHarness(NotifyMixin):
    def __init__(self) -> None:
        self.user_language = "ja"
        self._user_language_explicit = True
        self._conversation_render_language = "ja"
        self._conversation_turn_language = "ja"
        self.turn_updates: list[str] = []
        self.tool_registrations = 0
        self.tool_syncs = 0
        self.fired: list[object] = []

    def _set_conversation_turn_language(self, language: str) -> None:
        self.turn_updates.append(language)

    def _register_builtin_tools(self) -> None:
        self.tool_registrations += 1

    def _sync_tools_to_active_session(self) -> object:
        self.tool_syncs += 1
        return object()

    def _fire_task(self, value: object) -> None:
        self.fired.append(value)


@pytest.mark.unit
def test_render_language_never_clears_explicit_preference() -> None:
    manager = _NotifyHarness()

    manager.set_render_language("pt")

    assert manager._user_language_explicit is True
    assert manager._conversation_render_language == "pt"
    assert manager.user_language == "ja"
    assert manager._conversation_turn_language == "ja"
    assert manager.tool_registrations == 0
    assert manager.tool_syncs == 0


@pytest.mark.unit
@pytest.mark.parametrize("argument", ("pt", None))
def test_explicit_clear_moves_runtime_and_tools_to_render_fallback(argument) -> None:
    manager = _NotifyHarness()
    manager._conversation_render_language = "pt"

    if argument is None:
        manager.clear_user_language_preference()
    else:
        manager.clear_user_language_preference(argument)

    assert manager._user_language_explicit is False
    assert manager._conversation_render_language == "pt"
    assert manager.user_language == manager._conversation_turn_language == "pt"
    assert manager.turn_updates == ["pt"]
    assert manager.tool_registrations == manager.tool_syncs == 1
    assert len(manager.fired) == 1


@pytest.mark.unit
def test_explicit_clear_without_any_render_evidence_drops_stale_language() -> None:
    manager = _NotifyHarness()
    manager.user_language = None
    manager._user_language_explicit = False
    manager._conversation_render_language = None

    manager.set_user_language("ja")
    assert manager._conversation_render_language is None
    manager.clear_user_language_preference()

    assert manager._user_language_explicit is False
    assert manager.user_language is None
    assert manager._conversation_turn_language is None
    assert manager.turn_updates == ["ja", None]
    assert manager.tool_registrations == 2
    assert manager.tool_syncs == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("message", "expected", "setter", "argument"),
    (
        ({"render_language": "pt"}, "pt", "render", "pt"),
        ({"clear_language_preference": 1, "render_language": "ja"}, "ja", "render", "ja"),
        ({"clear_language_preference": True, "render_language": "zh-TW"}, "zh-TW", "clear", "zh-TW"),
        (
            {"language": "ja", "clear_language_preference": True, "render_language": "pt"},
            "pt",
            "explicit",
            "ja",
        ),
    ),
)
def test_websocket_language_message_precedence(message, expected, setter, argument) -> None:
    manager = SimpleNamespace(
        set_user_language=MagicMock(),
        set_render_language=MagicMock(),
        clear_user_language_preference=MagicMock(),
    )

    assert websocket_router._apply_session_language_message(manager, message) == expected
    calls = {
        "explicit": manager.set_user_language,
        "render": manager.set_render_language,
        "clear": manager.clear_user_language_preference,
    }
    calls[setter].assert_called_once_with(argument)
    if setter != "clear":
        manager.clear_user_language_preference.assert_not_called()
    if setter == "explicit":
        manager.set_render_language.assert_called_once_with("pt")


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolver",
    ("game", "proactive"),
)
@pytest.mark.parametrize(
    ("data", "manager_language", "manager_explicit", "expected"),
    (
        ({"language": "ja", "render_language": "pt"}, "zh-TW", True, "ja"),
        ({"render_language": "pt"}, "zh-TW", True, "zh-TW"),
        ({"render_language": "pt"}, "ja", False, "pt"),
        ({}, "ja", False, "ja"),
        ({}, None, False, "ru"),
    ),
)
def test_template_locale_uses_explicit_render_and_fallback_precedence(
    monkeypatch,
    resolver,
    data,
    manager_language,
    manager_explicit,
    expected,
) -> None:
    manager = SimpleNamespace(
        user_language=manager_language,
        _user_language_explicit=manager_explicit,
        set_user_language=MagicMock(),
    )
    if resolver == "game":
        monkeypatch.setattr(game_char_info, "get_session_manager", lambda: {"Lan": manager})
        monkeypatch.setattr(game_char_info, "get_global_language_full", lambda: "ru")
        actual = game_char_info._resolve_game_prompt_locale("Lan", data)
    else:
        monkeypatch.setattr(proactive_service, "get_global_language_full", lambda: "ru")
        actual = proactive_service._resolve_proactive_locale(data, manager, fmt="full")
    assert actual == expected
