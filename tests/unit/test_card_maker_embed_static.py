from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_card_maker_exposes_transparent_model_embed_mode() -> None:
    template = (ROOT / "templates" / "card_maker.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "card_maker.css").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "card_maker.js").read_text(encoding="utf-8")

    assert "document.documentElement.classList.add('card-maker-embed')" in template
    assert "background: transparent !important;" in css
    assert "html.card-maker-embed #model-viewport" in css
    assert "html.card-maker-embed #card-edit-area" in css
    assert "const isEmbedMode = _urlParams.get('mode') === 'embed';" in script
    assert "type: 'neko-card-maker-embed'" in script
    assert "character: currentCharaName" not in script
    assert "modelType: currentModelType" not in script
    assert "if (isEmbedMode) {" in script
    assert "requestedEmbedScale" not in script
    assert "requestedEmbedOffsetY" not in script
    assert "if (!isEmbedMode) {\n                startPreviewLoop();\n                refreshPreview();\n            }\n            notifyEmbedHost('ready');" in script
    assert "notifyEmbedHost('ready')" in script
    assert "notifyEmbedHost('error'" in script
    assert "if (!isEmbedMode) {\n                startPreviewLoop();\n                refreshPreview();" in script
    assert "const EMBED_MODEL_HEIGHT_RATIO = 1.34;" in script
    assert "const EMBED_MODEL_CENTER_X_RATIO = 0.22;" in script
    assert "const EMBED_MODEL_CENTER_Y_RATIO = 0.67;" in script
    assert "frameLive2DModelForEmbed(window.live2dManager);" in script
    assert "frameThreeModelForEmbed(window.vrmManager);" in script
    assert "frameThreeModelForEmbed(mmdProxy);" in script
    assert "framePNGTuberForEmbed(mgr);" in script
    assert "isEmbedMode ? Math.max(1, window.innerWidth) : CARD_BASE_WIDTH" in script
