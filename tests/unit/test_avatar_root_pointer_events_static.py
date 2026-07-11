from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app-interpage.js"
UNIVERSAL_MANAGER_PATH = PROJECT_ROOT / "static" / "tutorial" / "core" / "universal-manager.js"


def test_live2d_restore_keeps_root_container_passthrough():
    source = APP_UI_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("function keepAvatarRootContainerPassthrough(container)"):
        source.index("    function restoreLive2DDisplaySurface(reason)")
    ]
    restore_block = source[
        source.index("function restoreLive2DDisplaySurface(reason)"):
        source.index("    function activateLive2DRenderForDisplay(reason)")
    ]
    return_prepare_block = source[
        source.index("function prepareModelReturnContainer(container, rect, options = {})"):
        source.index("    function applyModelGoodbyeVisualFade(container")
    ]

    assert "container.id !== 'live2d-container' && container.id !== 'pngtuber-container'" in helper_block
    assert "container.style.setProperty('pointer-events', 'none', 'important');" in helper_block
    assert "keepAvatarRootContainerPassthrough(live2dContainer);" in restore_block
    assert "live2dContainer.style.removeProperty('pointer-events');" not in restore_block
    assert "if (!keepAvatarRootContainerPassthrough(container)) {" in return_prepare_block
    assert "container.style.removeProperty('pointer-events');" in return_prepare_block


def test_model_reload_live2d_restore_keeps_root_container_passthrough():
    source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")
    reload_block = source[
        source.index("var live2dContainer2 = document.getElementById('live2d-container');"):
        source.index("if (window.lanlan_config) {", source.index("var live2dContainer2 = document.getElementById('live2d-container');"))
    ]

    assert "live2dContainer2.style.setProperty('pointer-events', 'none', 'important');" in reload_block
    assert "live2dContainer2.style.removeProperty('pointer-events');" not in reload_block
    assert "live2dCanvas2.style.pointerEvents = 'auto';" in reload_block


def test_tutorial_live2d_restore_keeps_root_container_passthrough():
    source = UNIVERSAL_MANAGER_PATH.read_text(encoding="utf-8")
    temporary_load_block = source[
        source.index("async loadTemporaryTutorialLive2dModel("):
        source.index("    isTutorialYuiLive2dActive()")
    ]
    restore_block = source[
        source.index("restoreTutorialLive2dDisplayState(reason"):
        source.index("    revealTutorialLive2dPrepared()")
    ]

    assert "live2dContainer.style.setProperty('pointer-events', 'none', 'important');" in temporary_load_block
    assert "live2dContainer.style.removeProperty('pointer-events');" not in temporary_load_block
    assert "live2dContainer.style.setProperty('pointer-events', 'none', 'important');" in restore_block
    assert "live2dContainer.style.removeProperty('pointer-events');" not in restore_block
    assert "live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');" in restore_block
