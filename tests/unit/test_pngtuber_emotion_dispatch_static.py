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
