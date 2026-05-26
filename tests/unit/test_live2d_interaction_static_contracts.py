from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _live2d_source() -> str:
    return (PROJECT_ROOT / "static/live2d-interaction.js").read_text(encoding="utf-8")


def test_live2d_drag_snap_keeps_visible_area_threshold_on_all_platforms():
    source = _live2d_source()

    # 桌宠窗口不再单独走 margin 回弹，与网页端统一用可见面积阈值。
    assert "const isDesktopPetWindow = Boolean(" not in source
    assert "const visibleWidth = Math.max(0, visibleRight - visibleLeft);" in source
    assert "const visibleHeight = Math.max(0, visibleBottom - visibleTop);" in source
    assert "const needsSnapHorizontal = visibleWidth < threshold && (overflowLeft > 0 || overflowRight > 0);" in source
    assert "const needsSnapVertical = visibleHeight < threshold && (overflowTop > 0 || overflowBottom > 0);" in source
    assert "needsSnapBottom = overflowBottom > 0 && needsSnapVertical;" in source


def test_live2d_only_display_switch_uses_edge_margin():
    source = _live2d_source()

    assert "if (afterDisplaySwitch) {" in source
    assert "if (afterDisplaySwitch || isDesktopPetWindow) {" not in source
    assert "needsSnapLeft = overflowLeft > margin;" in source
    assert "needsSnapRight = overflowRight > margin;" in source
    assert "needsSnapTop = overflowTop > margin;" in source
    assert "needsSnapBottom = overflowBottom > margin;" in source


def test_live2d_snap_keeps_explicit_threshold_override_for_initial_placement():
    source = _live2d_source()

    assert "threshold: customThreshold" in source
    assert "const margin = SNAP_CONFIG.margin;" in source
    assert "_checkSnapRequired(model, { threshold: 300 })" in (
        PROJECT_ROOT / "static/live2d-model.js"
    ).read_text(encoding="utf-8")


def test_live2d_display_switch_still_snaps_after_window_move():
    source = _live2d_source()

    display_switch_section = source.split("console.log('[Live2D] 屏幕切换成功:', result);", 1)[1]
    assert "const snapped = await this._checkAndPerformSnap(model, { afterDisplaySwitch: true });" in display_switch_section


def test_live2d_does_not_switch_display_during_drag_before_mouseup():
    source = _live2d_source()

    assert "maybeSwitchDisplayDuringDrag" not in source
    assert "liveDisplaySwitchPromise" not in source
