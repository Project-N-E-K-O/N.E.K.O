from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _live2d_source() -> str:
    return (PROJECT_ROOT / "static/live2d-interaction.js").read_text(encoding="utf-8")


def _js_block(source: str, marker: str) -> str:
    start = source.index(marker)
    params_end = source.index(")", start)
    brace_start = source.index("{", params_end)
    depth = 0
    for pos in range(brace_start, len(source)):
        char = source[pos]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : pos + 1]
    raise AssertionError(f"Could not find closing brace for {marker!r}")


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


def test_live2d_initial_snap_uses_runtime_threshold():
    source = _live2d_source()
    model_source = (PROJECT_ROOT / "static/live2d-model.js").read_text(encoding="utf-8")

    assert "threshold: customThreshold" in source
    assert "const margin = SNAP_CONFIG.margin;" in source
    assert "_checkSnapRequired(model, { threshold: 300 })" not in model_source
    assert "const snapInfo = await this._checkSnapRequired(model);" in model_source


def test_live2d_display_switch_still_snaps_after_window_move():
    source = _live2d_source()

    display_switch_section = source.split("console.log('[Live2D] 屏幕切换成功:', result);", 1)[1]
    assert "const snapped = await this._checkAndPerformSnap(model, { afterDisplaySwitch: true });" in display_switch_section


def test_live2d_does_not_switch_display_during_drag_before_mouseup():
    source = _live2d_source()

    assert "maybeSwitchDisplayDuringDrag" not in source
    assert "liveDisplaySwitchPromise" not in source


def test_live2d_niri_physical_crop_mouse_tracking_splits_virtual_and_local_coords():
    source = _live2d_source()

    assert "function getLive2DNiriPetPointerCoordinates(event)" in source
    assert "typeof api.isActive === 'function' && !api.isActive()" in source
    assert "typeof api.getEventCoordinates === 'function'" in source
    assert "const pointerCoords = getLive2DNiriPetPointerCoordinates(event);" in source
    assert "const pointer = pointerCoords.virtual;" in source
    assert "const localPointer = pointerCoords.local;" in source
    assert "this._lastMouseLocalX = localPointer.x;" in source
    assert "this._lastMouseLocalY = localPointer.y;" in source
    assert "isLive2DPointInRect(localPointer, lr, 0)" in source
    assert "isLive2DPointInRect(localPointer, br, 0)" in source
    assert "isPointerNearFloatingButtons()" in source


def test_live2d_click_touch_set_logs_trigger_summary():
    source = _live2d_source()
    summary_logger = _js_block(source, "function logLive2DClickTriggerSummary")
    touch_set_fallback = _js_block(source, "Live2DManager.prototype._playTouchSetWithFallback")
    touch_set_animation = _js_block(source, "Live2DManager.prototype._playTouchSetAnimation")

    assert "function logLive2DClickTriggerSummary(label, details = {})" in summary_logger
    assert "triggered=${triggerCount}, motions=${motionCount}, expressions=${expressionCount}" in summary_logger
    assert "requestedHitArea" in summary_logger
    assert "resolvedHitArea" in summary_logger
    assert "summaryType" in summary_logger
    assert "motionCandidates" in summary_logger
    assert "expressionCandidates" in summary_logger
    assert "failedMotions" in summary_logger
    assert "failedExpressions" in summary_logger
    assert "await this._playTouchSetAnimation(useBlock, { requestedHitArea });" in touch_set_fallback
    assert "fallback: 'default'" in touch_set_fallback
    assert "summaryType: 'routing_decision'" in touch_set_fallback
    assert "triggerLog.motions.push({" in touch_set_animation
    assert "triggerLog.expressions.push({" in touch_set_animation


def test_live2d_random_click_prefers_motion_and_uses_expression_as_fallback():
    source = _live2d_source()
    click_effect = _js_block(source, "Live2DManager.prototype._playTemporaryClickEffect")

    motion_branch = click_effect.index("if (motions && motions.length > 0)")
    expression_fallback = click_effect.index("if (!didPlayEffect && expressionFiles.length > 0)")
    assert motion_branch < expression_fallback
    assert "const motion = await this.currentModel.motion(motionGroup, undefined, priority);" in click_effect
    assert "triggerLog.motions.push({" in click_effect
    assert "triggerLog.expressions.push({ emotion, file: choiceFile, fallbackFor: 'motion' });" in click_effect


def test_live2d_touch_set_prefers_motion_and_uses_expression_as_fallback():
    source = _live2d_source()
    touch_set_animation = _js_block(source, "Live2DManager.prototype._playTouchSetAnimation")

    motion_branch = touch_set_animation.index("if (motions.length > 0)")
    expression_fallback = touch_set_animation.index("if (triggerLog.motions.length === 0 && expressions.length > 0)")
    assert motion_branch < expression_fallback
    assert "triggerLog.motions.push({" in touch_set_animation
    assert "fallbackFor: 'motion'" in touch_set_animation
