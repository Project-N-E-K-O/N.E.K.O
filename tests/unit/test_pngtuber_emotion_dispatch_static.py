from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INIT_JS = PROJECT_ROOT / "static" / "live2d-init.js"
PNGTUBER_CORE_JS = PROJECT_ROOT / "static" / "pngtuber-core.js"


def test_lanlan_set_emotion_dispatches_to_pngtuber_runtime():
    script = LIVE2D_INIT_JS.read_text(encoding="utf-8")
    start = script.index("window.LanLan1.setEmotion = function(emotion)")
    end = script.index("// 兼容旧接口 playExpression", start)
    block = script[start:end]

    assert "if (activeType === 'pngtuber')" in block
    assert "window.pngtuberManager.setEmotion(emotion, { source: 'LanLan1.setEmotion' });" in block
    assert "if (activeType === 'pngtuber') return;" not in block


def test_lanlan_clear_expression_clears_pngtuber_emotion():
    script = LIVE2D_INIT_JS.read_text(encoding="utf-8")
    start = script.index("window.LanLan1.clearExpression = function()")
    end = script.index("// 4. 嘴型控制", start)
    block = script[start:end]

    assert "if (activeType === 'pngtuber')" in block
    assert "window.pngtuberManager.clearEmotion({ source: 'LanLan1.clearExpression' });" in block


def test_pngtuber_runtime_exposes_emotion_dispatch_api():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")

    assert "setEmotion(emotion, options = {})" in script
    assert "resolveLayeredEmotionTarget(emotion)" in script
    assert "window.performPNGTuberEmotion = performPNGTuberEmotion;" in script
    assert "const emotions = this.layeredMetadata.emotions;" in script
    assert "this.layeredMetadata.emotion_mapping" not in script


def test_pngtuber_emotion_uses_zero_based_layered_state_index():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")
    start = script.index("        setEmotion(emotion, options = {})")
    end = script.index("        hotkeyMatchesEvent(hotkey, event)", start)
    block = script[start:end]

    assert "applied = this.setLayeredStateIndex(layeredTarget, {" in block
    assert "applied = this.playLayeredAnimation(layeredTarget, {" not in block


def test_pngtuber_emotion_falls_back_to_image_state():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")
    start = script.index("        setEmotion(emotion, options = {})")
    end = script.index("        hotkeyMatchesEvent(hotkey, event)", start)
    block = script[start:end]

    assert "if (!applied && !this.isLayeredActive() && this.config[`${normalized}_image`])" in block
    assert "this.setState(normalized);" in block
    assert "applied = true;" in block


def test_pngtuber_unknown_emotion_keeps_existing_return_timer():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")
    start = script.index("        setEmotion(emotion, options = {})")
    end = script.index("        hotkeyMatchesEvent(hotkey, event)", start)
    block = script[start:end]

    assert block.index("if (!applied) return false;") < block.index("if (this.emotionTimer)")


def test_pngtuber_lifecycle_clears_emotion_and_play_event_listeners():
    script = PNGTUBER_CORE_JS.read_text(encoding="utf-8")

    load_start = script.index("        async load(config) {")
    load_end = script.index("        stateToSrc(state)", load_start)
    load_block = script[load_start:load_end]
    assert "if (this.emotionTimer)" in load_block
    assert "clearTimeout(this.emotionTimer);" in load_block

    show_start = script.index("        show() {")
    show_end = script.index("        hide() {", show_start)
    show_block = script[show_start:show_end]
    assert "this.attachLayeredPlayEvent();" in show_block

    hide_start = script.index("        hide() {")
    hide_end = script.index("        dispose() {", hide_start)
    hide_block = script[hide_start:hide_end]
    assert "this.detachLayeredPlayEvent();" in hide_block

    dispose_start = script.index("        dispose() {")
    dispose_end = script.index("    function applyPNGTuberAvatarUiMixins()", dispose_start)
    dispose_block = script[dispose_start:dispose_end]
    assert "this.detachLayeredPlayEvent();" in dispose_block
