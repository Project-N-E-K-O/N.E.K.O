from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d-core.js"
LIVE2D_MODEL_PATH = PROJECT_ROOT / "static" / "live2d-model.js"
LIVE2D_EMOTION_PATH = PROJECT_ROOT / "static" / "live2d-emotion.js"


def test_saved_live2d_parameters_feed_appearance_baseline():
    core_source = LIVE2D_CORE_PATH.read_text(encoding="utf-8")
    model_source = LIVE2D_MODEL_PATH.read_text(encoding="utf-8")

    assert "this.appearanceBaselineParameters = {};" in core_source
    assert "Live2DManager.prototype.mergeAppearanceBaselineParameters" in model_source
    assert "this.mergeAppearanceBaselineParameters(model, parameters);" in model_source
    assert "this._isRuntimeManagedAppearanceParam" in model_source


def test_full_live2d_reset_prefers_saved_appearance_baseline():
    source = LIVE2D_EMOTION_PATH.read_text(encoding="utf-8")

    assert "this.appearanceBaselineParameters = { ...this.initialParameters };" in source
    assert "? [this.appearanceBaselineParameters, this.savedModelParameters, this.motionBaselineParameters, this.initialParameters]" in source
    assert "const resetValue = baseline.found ? baseline.value : initialValue;" in source
    assert "coreModel.setParameterValueByIndex(paramIndex, resetValue);" in source
    assert "coreModel.setParameterValueById(paramId, resetValue);" in source
