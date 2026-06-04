from pathlib import Path


def test_avatar_model_manager_popup_opens_fullscreen():
    source = Path("static/avatar-ui-popup.js").read_text(encoding="utf-8")

    assert "function buildAvatarFullscreenWindowFeatures()" in source
    assert "screenRef.availWidth || screenRef.width" in source
    assert "screenRef.availHeight || screenRef.height" in source
    assert "features = buildAvatarFullscreenWindowFeatures();" in source
    assert "openAndPauseMainUI(finalUrl, windowName, features);" in source


def test_yui_model_manager_handoff_opens_fullscreen():
    source = Path("static/yui-guide-page-handoff.js").read_text(encoding="utf-8")

    assert "function buildFullscreenWindowFeatures()" in source
    assert "function isModelManagerPageUrl(openUrl)" in source
    assert "if (isModelManagerPageUrl(openUrl))" in source
    assert "return buildFullscreenWindowFeatures();" in source
    assert "buildFullscreenWindowFeatures()" in source[source.index("function openModelManagerPage("):]
