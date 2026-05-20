from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _live2d_source() -> str:
    return (PROJECT_ROOT / "static/live2d-interaction.js").read_text(encoding="utf-8")


def test_live2d_drag_snap_uses_window_style_edge_margin():
    source = _live2d_source()

    assert "needsSnapLeft = overflowLeft > margin;" in source
    assert "needsSnapRight = overflowRight > margin;" in source
    assert "needsSnapTop = overflowTop > margin;" in source
    assert "needsSnapBottom = overflowBottom > margin;" in source
    assert "visibleWidth < threshold" not in source
    assert "visibleHeight < threshold" not in source


def test_live2d_display_switch_still_snaps_after_window_move():
    source = _live2d_source()

    display_switch_section = source.split("console.log('[Live2D] 屏幕切换成功:', result);", 1)[1]
    assert "const snapped = await this._checkAndPerformSnap(model, { afterDisplaySwitch: true });" in display_switch_section


def test_live2d_does_not_switch_display_during_drag_before_mouseup():
    source = _live2d_source()

    assert "maybeSwitchDisplayDuringDrag" not in source
    assert "liveDisplaySwitchPromise" not in source
