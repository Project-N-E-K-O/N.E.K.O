from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_game_mode_uses_explicit_cancellation_contract_for_all_model_managers():
    app = (ROOT / "static" / "app-game-mode-beta.js").read_text(encoding="utf-8")
    live2d = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")
    vrm = (ROOT / "static" / "vrm" / "vrm-manager.js").read_text(encoding="utf-8")
    mmd = (ROOT / "static" / "mmd" / "mmd-manager.js").read_text(encoding="utf-8")

    assert "cancelActiveModelLoadForGameMode" in live2d
    assert "cancelActiveModelLoadForGameMode" in vrm
    assert "cancelActiveModelLoadForGameMode" in mmd
    assert "manager.cancelActiveModelLoadForGameMode('game-mode-protection')" in app


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


def test_live2d_cancellation_releases_only_the_cancelled_load_lock():
    source = (ROOT / "static" / "live2d" / "live2d-model.js").read_text(encoding="utf-8")
    cancel_start = source.index("Live2DManager.prototype.cancelActiveModelLoadForGameMode")
    load_start = source.index("Live2DManager.prototype.loadModel", cancel_start)
    cancel_body = source[cancel_start:load_start]
    finally_start = source.index("} finally {", load_start)
    finally_body = source[finally_start:source.index("\n    }\n};", finally_start)]

    assert "this._isLoadingModel = false;" in cancel_body
    assert "if (this._activeLoadToken === loadToken)" in finally_body


def test_vrm_and_mmd_restore_respects_mouse_tracking_changes_during_protection():
    for directory, filename in (("vrm", "vrm-manager.js"), ("mmd", "mmd-manager.js")):
        source = (ROOT / "static" / directory / filename).read_text(encoding="utf-8")
        assert "this._gameModeResourceMouseTrackingEnabled = window.mouseTrackingEnabled !== false;" in source
        assert "const currentMouseTrackingEnabled = window.mouseTrackingEnabled !== false;" in source
        assert "const mouseTrackingChanged = this._gameModeResourceMouseTrackingEnabled !== null" in source
        assert "this.cursorFollow.setEnabled(restoreCursorFollow);" in source or "this._cursorFollow.setEnabled(restoreCursorFollow);" in source
