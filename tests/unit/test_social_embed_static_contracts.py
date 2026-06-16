from pathlib import Path


SOCIAL_EMBED_PATH = Path(__file__).resolve().parents[2] / "static" / "social-embed.js"


def _source() -> str:
    return SOCIAL_EMBED_PATH.read_text(encoding="utf-8")


def test_social_embed_drag_releases_when_pointerup_is_lost():
    source = _source()

    assert "frame.style.pointerEvents = 'none'" in source
    assert "restoreDragHitTesting()" in source
    assert "bar.setPointerCapture(e.pointerId)" in source
    assert "bar.releasePointerCapture(dragState.pointerId)" in source
    assert "document.addEventListener('pointercancel', onDragEnd, true)" in source
    assert "window.addEventListener('blur', onDragEnd, true)" in source
    assert "document.addEventListener('mouseup', onDragEnd, true)" in source
    assert "window.addEventListener('mouseup', onDragEnd, true)" in source
    assert "e.buttons === 0" in source
    assert "function withCacheBust(url)" in source
    assert "_neko_embed_v" in source
    assert "frame.src = withCacheBust(parsed);" in source
