from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INIT_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-init.js"


def test_live2d_init_respects_model_manager_pngtuber_mode():
    source = LIVE2D_INIT_PATH.read_text(encoding="utf-8")
    guard_block = source[
        source.index("const modelManagerAvatarType = window.location.pathname.includes('model_manager')"):
        source.index("if (!targetModelPath && !isModelManagerPage)", source.index("const modelManagerAvatarType = window.location.pathname.includes('model_manager')"))
    ]

    assert "String(window._modelManagerCurrentAvatarType || '').toLowerCase()" in guard_block
    assert "modelManagerAvatarType === 'pngtuber'" in guard_block
    assert guard_block.index("modelManagerAvatarType === 'pngtuber'") < guard_block.index("(window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'")


def test_resident_expression_callback_reapplies_normalized_effective_parameters():
    source = LIVE2D_INIT_PATH.read_text(encoding="utf-8")
    callback_start = source.index("onResidentExpressionApplied: (model) =>")
    callback_end = source.index("onModelReady: (model) =>", callback_start)
    callback_source = source[callback_start:callback_end]

    assert "window.live2dManager.effectiveModelParameters" in callback_source
    assert "applyModelParameters(model, effectiveParameters)" in callback_source
    assert "applyModelParameters(model, modelPreferences.parameters)" not in callback_source
