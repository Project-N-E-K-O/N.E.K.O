from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_mmd_token_invalidation_blocks_default_model_fallback():
    source = (ROOT / "static" / "mmd" / "mmd-manager.js").read_text(encoding="utf-8")
    catch_start = source.index("} catch (error) {", source.index("async loadModel"))
    fallback = source.index("MMDManager.DEFAULT_MODEL_PATH", catch_start)
    cancellation_guard = source.index("if (this._activeLoadToken !== loadToken) return null;", catch_start)

    assert cancellation_guard < fallback


def test_vrm_and_mmd_expose_their_full_pending_load_lifecycle():
    for directory, filename in (("vrm", "vrm-manager.js"), ("mmd", "mmd-manager.js")):
        source = (ROOT / "static" / directory / filename).read_text(encoding="utf-8")
        assert "this._pendingModelLoadCount += 1;" in source
        assert "this._pendingModelLoadCount = Math.max(0, this._pendingModelLoadCount - 1);" in source
        assert "this._isLoadingModel = this._pendingModelLoadCount > 0;" in source


def test_vrm_and_mmd_restore_respects_mouse_tracking_changes_during_protection():
    for directory, filename in (("vrm", "vrm-manager.js"), ("mmd", "mmd-manager.js")):
        source = (ROOT / "static" / directory / filename).read_text(encoding="utf-8")
        assert "this._gameModeResourceMouseTrackingEnabled = window.mouseTrackingEnabled !== false;" in source
        assert "const currentMouseTrackingEnabled = window.mouseTrackingEnabled !== false;" in source
        assert "const mouseTrackingChanged = this._gameModeResourceMouseTrackingEnabled !== null" in source
        assert "currentMouseTrackingEnabled && window.nekoYuiGuideFaceForwardLock !== true" in source
        assert "this.cursorFollow.setEnabled(restoreCursorFollow);" in source or "this._cursorFollow.setEnabled(restoreCursorFollow);" in source
