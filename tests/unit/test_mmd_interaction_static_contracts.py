from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _mmd_source() -> str:
    return (PROJECT_ROOT / "static/mmd/mmd-interaction.js").read_text(encoding="utf-8")


def test_mmd_pan_drag_snaps_on_all_platforms_before_saving_position():
    source = _mmd_source()
    pan_drag_section = source.split("if (!displaySwitched) {", 1)[1].split("// 鼠标离开", 1)[0]

    # 桌宠窗口与网页端统一走 clampModelPosition(可见像素阈值)，不再按运行环境分叉。
    assert "if (isDesktopPetWindow) {" not in pan_drag_section
    assert "const snapped = await this._snapModelIntoScreen({ animate: true });" in pan_drag_section
    assert "if (!snapped) {" in pan_drag_section
    assert "this._savePositionAfterInteraction();" in pan_drag_section
    assert "recordEdgeBounce('mmd')" not in pan_drag_section


def test_mmd_display_switch_snaps_to_target_screen_before_saving_position():
    source = _mmd_source()

    display_switch_section = source.split("console.log('[MMD] 屏幕切换成功:', result);", 1)[1]
    assert "this._moveModelCenterToWindowPoint(desiredModelCenterX, desiredModelCenterY);" in display_switch_section
    assert "const snapped = useDragPointerForSwitch" in display_switch_section
    assert "? false" in display_switch_section
    assert ": await this._snapModelIntoScreen({ animate: true });" in display_switch_section
    assert "if (useDragPointerForSwitch || !snapped) {\n                await this._savePositionAfterInteraction();" in display_switch_section


def test_mmd_display_switch_miss_records_bridge_errors_after_model_leaves_window():
    source = _mmd_source()
    method_section = source.split("async _checkAndSwitchDisplay() {", 1)[1].split("\n    /**\n     * 基于可见像素限制", 1)[0]

    assert method_section.index("const recordDisplaySwitchMiss = () => {") < method_section.index("try {")
    assert "recordDisplaySwitchMiss('mmd')" in method_section
    assert "let displaySwitchAttempted = false;" in method_section
    assert method_section.index("displaySwitchAttempted = true;") < method_section.index("window.electronScreen.getAllDisplays()")
    assert "if (displaySwitchAttempted) recordDisplaySwitchMiss();" in method_section
