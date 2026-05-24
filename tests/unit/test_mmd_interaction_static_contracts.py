from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _mmd_source() -> str:
    return (PROJECT_ROOT / "static/mmd-interaction.js").read_text(encoding="utf-8")


def test_mmd_pan_drag_snaps_on_all_platforms_before_saving_position():
    source = _mmd_source()
    pan_drag_section = source.split("if (!displaySwitched) {", 1)[1].split("// 鼠标离开", 1)[0]

    # 桌宠窗口与网页端统一走 clampModelPosition(可见像素阈值)，不再按运行环境分叉。
    assert "if (isDesktopPetWindow) {" not in pan_drag_section
    assert "const snapped = await this._snapModelIntoScreen({ animate: true });" in pan_drag_section
    assert "if (!snapped) {" in pan_drag_section
    assert "this._savePositionAfterInteraction();" in pan_drag_section
    assert "recordEdgeBounce('mmd')" in pan_drag_section


def test_mmd_display_switch_snaps_to_target_screen_before_saving_position():
    source = _mmd_source()

    display_switch_section = source.split("console.log('[MMD] 屏幕切换成功:', result);", 1)[1]
    assert "const snapped = await this._snapModelIntoScreen({ animate: true });" in display_switch_section
    assert "if (!snapped) {\n                await this._savePositionAfterInteraction();" in display_switch_section
