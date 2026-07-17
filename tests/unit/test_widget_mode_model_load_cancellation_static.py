from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_widget_mode_uses_explicit_cancellation_contract_for_all_model_managers():
    app = (ROOT / "static" / "app" / "app-widget-mode.js").read_text(encoding="utf-8")
    live2d = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")
    vrm = (ROOT / "static" / "vrm" / "vrm-manager.js").read_text(encoding="utf-8")
    mmd = (ROOT / "static" / "mmd" / "mmd-manager.js").read_text(encoding="utf-8")

    assert "cancelActiveModelLoadForWidgetMode" in live2d
    assert "cancelActiveModelLoadForWidgetMode" in vrm
    assert "cancelActiveModelLoadForWidgetMode" in mmd
    assert "manager.cancelActiveModelLoadForWidgetMode('widget-mode-compaction')" in app
    assert "await ensureInvalidatedModelReloaded()" in app
    assert "[WidgetMode] model reload after compaction failed:" in app


def test_mmd_cancellation_blocks_default_model_fallback_after_token_invalidation():
    source = (ROOT / "static" / "mmd" / "mmd-manager.js").read_text(encoding="utf-8")
    catch_start = source.index("} catch (error) {", source.index("async loadModel"))
    fallback = source.index("MMDManager.DEFAULT_MODEL_PATH", catch_start)
    cancellation_guard = source.index("if (this._activeLoadToken !== loadToken) return null;", catch_start)

    assert cancellation_guard < fallback
    assert "this._modelLoadState = 'cancelled';" in source


def test_vrm_and_mmd_expose_their_full_pending_load_lifecycle():
    for directory, filename in (("vrm", "vrm-manager.js"), ("mmd", "mmd-manager.js")):
        source = (ROOT / "static" / directory / filename).read_text(encoding="utf-8")
        assert "this._pendingModelLoadCount += 1;" in source
        assert "this._pendingModelLoadCount = Math.max(0, this._pendingModelLoadCount - 1);" in source
        assert "this._isLoadingModel = this._pendingModelLoadCount > 0;" in source


def test_live2d_distinguishes_widget_mode_cancellation_from_superseded_loads():
    source = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")

    assert "cancelError.name = isWidgetMode ? 'WidgetModeLoadCancelled' : 'LoadSuperseded';" in source
    assert "error.name === 'WidgetModeLoadCancelled' || error.name === 'LoadSuperseded'" in source
