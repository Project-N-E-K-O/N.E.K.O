from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SURVEY_UI_PATH = PROJECT_ROOT / "static/app/app-ui/bootstrap-goodbye-and-toasts.js"


def _survey_modal_source() -> str:
    source = SURVEY_UI_PATH.read_text(encoding="utf-8")
    return source.split("function showSurveyModal(survey) {", 1)[1].split(
        "I.mod.showSurveyModal = showSurveyModal;", 1
    )[0]


def test_survey_overlay_uses_shared_full_window_modal_contract():
    source = _survey_modal_source()

    overlay_id = "overlay.id = 'survey-modal-overlay';"
    modal_contract = "overlay.className = 'modal-overlay';"
    overlay_style = "overlay.style.cssText = `"
    append_overlay = "document.body.appendChild(overlay);"

    assert overlay_id in source
    assert modal_contract in source
    assert overlay_style in source
    assert append_overlay in source
    assert (
        source.index(overlay_id)
        < source.index(modal_contract)
        < source.index(overlay_style)
        < source.index(append_overlay)
    )

    # The shared class is an input-routing contract, not a replacement for the
    # survey's existing presentation or teardown behavior.
    for preserved_behavior in (
        "position: fixed; inset: 0;",
        "pointer-events: auto;",
        "overlay.remove();",
        "if (needRestoreBodyPE) document.body.style.pointerEvents = bodyPE;",
    ):
        assert preserved_behavior in source

    # Keep platform policy in the desktop host. The web UI should expose the
    # same modal semantics to browsers and every packaged desktop platform.
    for platform_probe in ("process.platform", "navigator.platform", "navigator.userAgent"):
        assert platform_probe not in source
