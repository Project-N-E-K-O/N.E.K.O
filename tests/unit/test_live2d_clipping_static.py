from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_MODEL_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-model.js"


def test_clipping_buffer_count_does_not_reinitialize_existing_manager():
    source = LIVE2D_MODEL_PATH.read_text(encoding="utf-8")
    configure_block = source.split(
        "Live2DManager.prototype._configureLoadedModel =", 1
    )[1].split("Live2DManager.prototype.", 1)[0]

    assert "._renderTextureCount = 3;" in configure_block
    assert "._clippingManager.initialize(" not in configure_block
