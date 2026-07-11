from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_game_mode_uses_explicit_cancellation_contract_for_all_model_managers():
    app = (ROOT / "static" / "app-game-mode-beta.js").read_text(encoding="utf-8")
    live2d = (ROOT / "static" / "live2d-model.js").read_text(encoding="utf-8")
    vrm = (ROOT / "static" / "vrm-manager.js").read_text(encoding="utf-8")
    mmd = (ROOT / "static" / "mmd-manager.js").read_text(encoding="utf-8")

    assert "cancelActiveModelLoadForGameMode" in live2d
    assert "cancelActiveModelLoadForGameMode" in vrm
    assert "cancelActiveModelLoadForGameMode" in mmd
    assert "manager.cancelActiveModelLoadForGameMode('game-mode-protection')" in app


def test_mmd_cancellation_blocks_default_model_fallback_after_token_invalidation():
    source = (ROOT / "static" / "mmd-manager.js").read_text(encoding="utf-8")
    catch_start = source.index("} catch (error) {", source.index("async loadModel"))
    fallback = source.index("MMDManager.DEFAULT_MODEL_PATH", catch_start)
    cancellation_guard = source.index("if (this._activeLoadToken !== loadToken) return null;", catch_start)

    assert cancellation_guard < fallback
    assert "this._modelLoadState = 'cancelled';" in source
