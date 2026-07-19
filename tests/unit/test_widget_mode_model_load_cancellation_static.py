from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_widget_mode_model_load_cancellation_contract_is_removed() -> None:
    paths = [
        ROOT / "static" / "app" / "app-widget-mode.js",
        ROOT / "static" / "live2d" / "live2d-model.js",
        ROOT / "static" / "vrm" / "vrm-manager.js",
        ROOT / "static" / "mmd" / "mmd-manager.js",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "cancelActiveModelLoadForWidgetMode" not in source
    assert "WidgetModeLoadCancelled" not in source
    assert "_nekoWidgetModeReloadRequired" not in source
    assert "_nekoWidgetModeLoadCancelReason" not in source


def test_mmd_superseded_load_blocks_default_model_fallback() -> None:
    source = (ROOT / "static" / "mmd" / "mmd-manager.js").read_text(encoding="utf-8")
    catch_start = source.index("} catch (error) {", source.index("async loadModel"))
    fallback = source.index("MMDManager.DEFAULT_MODEL_PATH", catch_start)
    token_guard = source.index("if (this._activeLoadToken !== loadToken) return null;", catch_start)

    assert token_guard < fallback


def test_vrm_and_mmd_keep_their_full_pending_load_lifecycle() -> None:
    for directory, filename in (("vrm", "vrm-manager.js"), ("mmd", "mmd-manager.js")):
        source = (ROOT / "static" / directory / filename).read_text(encoding="utf-8")
        assert "this._pendingModelLoadCount += 1;" in source
        assert "this._pendingModelLoadCount = Math.max(0, this._pendingModelLoadCount - 1);" in source
        assert "this._isLoadingModel = this._pendingModelLoadCount > 0;" in source
        assert "_activeLoadToken" in source


def test_live2d_keeps_generic_superseded_load_protection() -> None:
    source = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")

    assert ": 'LoadSuperseded';" in source
    assert "error.name === 'LoadSuperseded'" in source
    assert "_isLoadTokenActive" in source
