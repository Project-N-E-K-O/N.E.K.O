from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[2]


def render_card_maker(mode: str) -> str:
    environment = Environment(loader=FileSystemLoader(ROOT))
    template = environment.get_template("templates/card_maker.html")
    return template.render(
        request=SimpleNamespace(query_params={"mode": mode}),
        vrm_defaults={},
        static_asset_version="test-version",
    )


def test_card_maker_exposes_transparent_model_embed_mode() -> None:
    template = (ROOT / "templates" / "card_maker.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "card_maker.css").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "card_maker.js").read_text(encoding="utf-8")
    bootstrap = (ROOT / "static" / "js" / "card_maker_embed_bootstrap.js").read_text(encoding="utf-8")
    model_runtime = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")

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
    config_error_handler = script.split(
        "console.error('[CardExport] 加载角色模型失败:', e);", 1
    )[1].split("}", 1)[0]
    assert "notifyEmbedHost('error');" in config_error_handler
    assert "if (!isEmbedMode) {\n                startPreviewLoop();\n                refreshPreview();" in script
    assert "const EMBED_MODEL_HEIGHT_RATIO = 1.34;" in script
    assert "const EMBED_MODEL_CENTER_X_RATIO = 0.22;" in script
    assert "const EMBED_MODEL_CENTER_Y_RATIO = 0.67;" in script
    assert "frameLive2DModelForEmbed(window.live2dManager);" in script
    assert "frameThreeModelForEmbed(window.vrmManager);" in script
    assert "frameThreeModelForEmbed(mmdProxy);" in script
    assert "framePNGTuberForEmbed(mgr);" in script
    assert "isEmbedMode ? Math.max(1, window.innerWidth) : CARD_BASE_WIDTH" in script
    assert "window.addEventListener('resize'" in script
    assert "function syncEmbedModelViewport()" in script
    assert "resizeModelRendererForCard(currentModelType, activeModelSourceScale);" in script
    assert "window.__NEKO_CARD_MAKER_EMBED__ = {{ card_maker_embed | tojson }};" in template
    assert "card_maker_embed_bootstrap.js?v={{ static_asset_version" in template
    assert "window.__NEKO_CARD_MAKER_CONFIG_PROMISE__" in bootstrap
    assert "await loaders[effectiveModelType(config)]();" in bootstrap
    assert "minimalEmbed: true" in script
    assert "preserveDrawingBuffer: !isEmbedMode" in script
    assert "loadEmotionMapping: false" in script
    assert "const minimalEmbed = options.minimalEmbed === true;" in model_runtime
    assert "minimalEmbed ? 480 : 2000" in model_runtime


def test_card_maker_embed_runtime_loaders_are_provider_symmetric() -> None:
    bootstrap = (ROOT / "static" / "js" / "card_maker_embed_bootstrap.js").read_text(encoding="utf-8")

    for provider in ("live2d", "vrm", "mmd", "pngtuber"):
        assert f"{provider}: load" in bootstrap
    assert "loadLive2DRuntime" in bootstrap
    assert "loadVRMRuntime" in bootstrap
    assert "loadMMDRuntime" in bootstrap
    assert "loadPNGTuberRuntime" in bootstrap


def test_embed_template_omits_full_editor_runtimes() -> None:
    embedded = render_card_maker("embed")
    full_editor = render_card_maker("maker")

    assert "/static/js/card_maker_embed_bootstrap.js?v=test-version" in embedded
    assert "/static/libs/live2dcubismcore.min.js" not in embedded
    assert "/static/vrm/vrm-init.js" not in embedded
    assert "/static/mmd/mmd-init.js" not in embedded
    assert "/static/i18n-i18next.js" not in embedded
    assert "/static/js/card_maker_embed_bootstrap.js" not in full_editor
    assert "/static/libs/live2dcubismcore.min.js" in full_editor
    assert "/static/vrm/vrm-init.js" in full_editor
    assert "/static/mmd/mmd-init.js" in full_editor


def test_versioned_embed_assets_use_immutable_cache_headers() -> None:
    web_app = (ROOT / "app" / "main_server" / "web_app.py").read_text(encoding="utf-8")
    pages_router = (ROOT / "main_routers" / "pages_router.py").read_text(encoding="utf-8")

    assert 'if b"v=" in scope.get("query_string", b""):' in web_app
    assert '"public, max-age=31536000, immutable"' in web_app
    assert 'static/js/card_maker_embed_bootstrap.js' in pages_router
