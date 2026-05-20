from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _mmd_source() -> str:
    return (PROJECT_ROOT / "static/mmd-interaction.js").read_text(encoding="utf-8")


def test_mmd_pan_drag_snaps_to_screen_before_saving_position():
    source = _mmd_source()

    assert "const snapped = await this._snapModelIntoScreen({ animate: true });" in source
    assert "if (!snapped) {\n                        this._savePositionAfterInteraction();" in source


def test_mmd_display_switch_snaps_to_target_screen_before_saving_position():
    source = _mmd_source()

    display_switch_section = source.split("console.log('[MMD] 屏幕切换成功:', result);", 1)[1]
    assert "const snapped = await this._snapModelIntoScreen({ animate: true });" in display_switch_section
    assert "if (!snapped) {\n                await this._savePositionAfterInteraction();" in display_switch_section
