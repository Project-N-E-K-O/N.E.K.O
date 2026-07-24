from pathlib import Path


SOCIAL_EMBED_PATH = Path(__file__).resolve().parents[2] / "static" / "social-embed.js"
SOCIAL_EMBED_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "social-embed.css"


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


def test_social_embed_has_four_edge_resize_handles():
    source = _source()
    styles = SOCIAL_EMBED_CSS_PATH.read_text(encoding="utf-8")

    assert "var RESIZE_HANDLES = ['n', 'e', 's', 'w', 'ne', 'nw', 'se', 'sw'];" in source
    assert "function startResize(e, win, handle, edge)" in source
    assert "document.addEventListener('pointermove', onResizeMove, true)" in source
    assert "window.addEventListener('blur', onResizeEnd, true)" in source
    assert "frame.style.pointerEvents = 'none'" in source
    assert "appendResizeHandles(win);" in source
    assert ".neko-social-embed-resize-n" in styles
    assert ".neko-social-embed-resize-e" in styles
    assert ".neko-social-embed-resize-s" in styles
    assert ".neko-social-embed-resize-w" in styles
