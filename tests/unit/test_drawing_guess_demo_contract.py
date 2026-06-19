import json
from pathlib import Path

import pytest

from main_routers import pages_router


ROOT = Path(__file__).resolve().parents[2]
DRAWING_GUESS_TEMPLATE = ROOT / "templates" / "drawing_guess_demo.html"
DRAWING_GUESS_SCRIPT = ROOT / "static" / "game" / "games" / "drawing_guess" / "drawing-guess-demo.js"
LOCALES_DIR = ROOT / "static" / "locales"


class _FakePageRequest:
    query_params = {}


class _FakeTemplates:
    def TemplateResponse(self, template_name: str, context: dict):
        return {"template_name": template_name, "context": context}


def _html() -> str:
    return DRAWING_GUESS_TEMPLATE.read_text(encoding="utf-8")


def _script() -> str:
    return DRAWING_GUESS_SCRIPT.read_text(encoding="utf-8")


def _get_nested(payload: dict, dotted_key: str):
    node = payload
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.unit
@pytest.mark.asyncio
async def test_drawing_guess_demo_page_renders_shell(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())

    result = await pages_router.drawing_guess_demo(_FakePageRequest())

    assert result["template_name"] == "templates/drawing_guess_demo.html"
    assert "static_asset_version" in result["context"]


@pytest.mark.unit
def test_drawing_guess_demo_static_route_contract():
    html = _html()
    script = _script()

    assert "/static/game/games/drawing_guess/drawing-guess-demo.js" in html
    assert "var GAME_TYPE = 'drawing_guess';" in script
    assert "var ROUND_API = '/api/game/drawing_guess';" in script
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'drawing_guess_demo'" not in html
    assert "fetch('/api/characters/current_catgirl'" in script
    assert "ROUTE_API + '/route/start'" in script
    assert "ROUTE_API + '/route/heartbeat'" in script
    assert "ROUTE_API + '/route/end'" in script
    assert "ROUND_API + '/round/start'" in script
    assert "ROUND_API + '/ai-draw'" in script
    assert "ROUND_API + '/input'" in script
    assert "ROUND_API + '/timeout'" in script
    assert "ROUND_API + '/vision-guess'" in script
    assert "memory_consent: state.memoryConsent" in script
    assert "gameStarted: state.phase !== 'tutorial'" in script
    assert "startCountdown(res.guess_seconds || 60" in script
    assert "startCountdown(seconds || 60" in script
    assert "els.canvas.toDataURL('image/png')" in script
    assert "state.phase !== 'user_drawing'" in script
    assert "submitGameChat(value)" in script
    assert "}), 45000)" in script
    assert "downloadAiSvg" in script
    assert "downloadUserPng" in script
    assert "animateAiDrawing" in script
    assert "prefers-reduced-motion: reduce" in script
    assert "navigator.sendBeacon" in script


@pytest.mark.unit
def test_drawing_guess_i18n_keys_exist_in_all_static_locales():
    required_keys = (
        "drawingGuess.title",
        "drawingGuess.status.loadingCharacter",
        "drawingGuess.status.active",
        "drawingGuess.layout.canvasTitle",
        "drawingGuess.tools.color",
        "drawingGuess.tutorial.memoryNone",
        "drawingGuess.actions.done",
        "drawingGuess.actions.downloadPng",
        "drawingGuess.actions.start",
        "drawingGuess.memory.noneShort",
        "drawingGuess.phases.user_drawing",
        "drawingGuess.timer.seconds",
        "drawingGuess.messages.aiGuessLine",
        "drawingGuess.messages.summaryReady",
        "drawingGuess.messages.routeActive",
        "drawingGuess.input.placeholder",
        "drawingGuess.input.hintPlaceholder",
        "drawingGuess.summary.title",
        "drawingGuess.summary.outcome.userWin",
        "drawingGuess.summary.nekoArt",
    )
    locale_files = sorted(LOCALES_DIR.glob("*.json"))
    assert {path.name for path in locale_files} >= {
        "en.json",
        "ja.json",
        "ko.json",
        "zh-CN.json",
        "zh-TW.json",
        "ru.json",
        "pt.json",
        "es.json",
    }

    for path in locale_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in required_keys:
            value = _get_nested(payload, key)
            assert isinstance(value, str) and value.strip(), f"{path.name} missing {key}"
