from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"


def test_startup_default_cat_waits_for_avatar_then_uses_the_existing_goodbye_flow():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "window.addEventListener('neko:startup-default-form'" in source
    assert "if (detail.form === 'cat') requestStartupDefaultCat();" in source
    assert "STARTUP_DEFAULT_CAT_MAX_ATTEMPTS = 300" in source
    assert "#live2d-btn-goodbye, #vrm-btn-goodbye, #mmd-btn-goodbye, #pngtuber-btn-goodbye" in source
    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'" in source
    assert "startupDefaultForm: 'cat'" in source


def test_startup_default_cat_retry_is_deferred_and_user_actions_cancel_it():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "if (!state.started)" in source
    assert "cancelStartupDefaultCatRequest(false);" in source
    assert "detail.startupDefaultForm !== 'cat' && state.startupDefaultCatRequested" in source
    assert "const handleReturn = () => {\n            // Returning" in source
    assert "cancelStartupDefaultCatRequest(true);" in source


def test_startup_default_cat_is_cat1_and_has_a_distinct_silence_reason():
    source = APP_AUTO_GOODBYE.read_text(encoding="utf-8")

    assert "const isStartupDefaultCat = detail.startupDefaultForm === 'cat';" in source
    assert "state.lastReason = isStartupDefaultCat ? 'startup-default-cat' : 'manual-goodbye';" in source
    assert "source: isStartupDefaultCat ? 'startup-default-form' : 'manual-goodbye'" in source
    assert "setVisualTier(TIER_CAT1" in source
