"""Real emotion-editor DOM and API candidates, without GPU or user models."""

import asyncio
import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page

from main_routers import vrm_router


ROOT = Path(__file__).resolve().parents[2]
EMOTIONS = ["neutral", "happy", "relaxed", "sad", "angry", "surprised"]
pytestmark = pytest.mark.frontend


def setup_editor(page: Page, tmp_path, monkeypatch, mapping, real_expressions=None):
    config = tmp_path / "model_emotion.json"
    config.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(vrm_router, "_get_emotion_config_path", lambda _: config)
    fallback = asyncio.run(vrm_router.get_model_expressions("model"))

    html = (ROOT / "templates/vrm_emotion_manager.html").read_text(encoding="utf-8")
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<link\b[^>]*>", "", html)
    page.route("http://neko.test/**", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
    page.goto("http://neko.test/")
    page.set_content(html)
    page.evaluate("""({mapping, fallback, realExpressions}) => {
        window.i18next = {isInitialized: true, t: key => key};
        window.requests = [];
        window.fetch = async url => {
            requests.push(String(url));
            if (url === '/api/model/vrm/models') {
                return {ok: true, json: async () => ({success: true, models: [{name: 'model'}]})};
            }
            if (url === '/api/model/vrm/expressions/model') {
                return {ok: true, json: async () => fallback};
            }
            if (url === '/api/model/vrm/emotion_mapping/model') {
                // Pause binding restoration so the candidate-source status can be inspected.
                return new Promise(resolve => {
                    window.resolveMapping = () => resolve({ok: true,
                        json: async () => ({success: true, config: mapping})});
                });
            }
            throw new Error('Unexpected request: ' + url);
        };
        if (realExpressions) {
            Object.defineProperty(window, 'opener', {configurable: true, value: {
                closed: false, vrmManager: {expression: {
                    getExpressionList: () => [...realExpressions]
                }}
            }});
        }
    }""", {"mapping": mapping, "fallback": fallback, "realExpressions": real_expressions})
    page.add_script_tag(path=str(ROOT / "static/js/vrm_emotion_manager.js"))
    page.locator(".singleselect-item").click()
    page.wait_for_function("typeof window.resolveMapping === 'function'")


def candidates(page):
    return page.locator('.emotion-expression-select[data-emotion="happy"] input').evaluate_all(
        "inputs => inputs.map(input => input.value)"
    )


def test_partial_mapping_keeps_default_reset_choices(page: Page, tmp_path, monkeypatch):
    setup_editor(page, tmp_path, monkeypatch, {"neutral": ["neutral"], "happy": ["happy"]})
    page.evaluate("resolveMapping()")
    page.locator('#reset-btn').click()

    for emotion in EMOTIONS:
        checked = page.locator(f'.emotion-expression-select[data-emotion="{emotion}"] input:checked')
        assert emotion in checked.evaluate_all("inputs => inputs.map(input => input.value)")


def test_custom_saved_name_is_available_and_source_is_labeled(page: Page, tmp_path, monkeypatch):
    setup_editor(page, tmp_path, monkeypatch, {"happy": ["big_smile", "big_smile"]})

    values = candidates(page)
    assert all(name in values for name in EMOTIONS)
    assert values.count("big_smile") == 1
    assert page.locator('#status-message').inner_text() == 'vrmEmotionManager.useSupplementedExpressions'
    page.evaluate("resolveMapping()")
    page.wait_for_function("document.querySelector('.emotion-expression-select[data-emotion=\"happy\"] input[value=\"big_smile\"]').checked")


def test_common_only_source_keeps_generic_status(page: Page, tmp_path, monkeypatch):
    setup_editor(page, tmp_path, monkeypatch, {"happy": ["happy"]})

    assert page.locator('#status-message').inner_text() == 'vrmEmotionManager.useGenericExpressions'
    page.evaluate("resolveMapping()")


def test_loaded_model_candidates_are_not_mixed_with_fallback(page: Page, tmp_path, monkeypatch):
    setup_editor(page, tmp_path, monkeypatch, {"happy": ["saved_smile"]}, ["real_smile"])

    assert candidates(page) == ["neutral", "real_smile"]
    assert page.locator('#status-message').inner_text() == 'vrmEmotionManager.expressionsLoadedFromModel'
    assert '/api/model/vrm/expressions/model' not in page.evaluate('requests')
    page.evaluate("resolveMapping()")


def test_supplemented_status_has_all_eight_translations():
    for locale in ["en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"]:
        data = json.loads((ROOT / f"static/locales/{locale}.json").read_text(encoding="utf-8"))
        assert data["vrmEmotionManager"]["useSupplementedExpressions"].strip()
