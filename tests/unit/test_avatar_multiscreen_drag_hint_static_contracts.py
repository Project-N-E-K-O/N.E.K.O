from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_multiscreen_drag_hint_ack_snoozes_for_three_days():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "const SNOOZE_MS = 3 * 24 * 60 * 60 * 1000;" in source
    assert "state.snoozeUntil = now() + SNOOZE_MS;" in source
    assert "neko:avatar-multiscreen-drag-hint:v1" in source


def test_multiscreen_drag_hint_only_observes_multi_display_edge_bounces():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "const REQUIRED_BOUNCES = 2;" in source
    assert "const BOUNCE_WINDOW_MS = 30 * 1000;" in source
    assert "window.electronScreen.getAllDisplays" in source
    assert "displays.length <= 1" in source
    assert "state.recentBounceCount >= REQUIRED_BOUNCES" in source
    assert "moveWindowToDisplay" not in source


def test_multiscreen_drag_hint_serializes_edge_bounce_counter_updates():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "let bounceRecordQueue = Promise.resolve();" in source
    assert "function recordEdgeBounce(source) {" in source
    assert "const nextRecord = bounceRecordQueue.then(function () {" in source
    assert "return recordEdgeBounceNow(source);" in source
    assert "bounceRecordQueue = nextRecord.catch(function () {});" in source
    assert "async function recordEdgeBounceNow(source)" in source


def test_multiscreen_drag_hint_can_be_disabled_or_suppressed_after_success():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "state.never = true;" in source
    assert "state.successAt = now();" in source
    assert "window.NekoAvatarMultiScreenDragHint" in source
    assert "if (Number(state.successAt) > 0) return true;" not in source


def test_multiscreen_drag_hint_uses_top_center_project_popup_style():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "left: 50%;" in source
    assert "top: calc" in source
    assert "translate(-50%, -16px)" in source
    assert "translate(-50%, 0)" in source
    assert "bottom: 88px" not in source
    assert "right: 24px" not in source
    assert "radial-gradient(circle at 10% 8%, rgba(111, 194, 255, 0.16), transparent 118px)" in source
    assert "linear-gradient(180deg, rgba(251, 254, 255, 0.98), rgba(239, 248, 255, 0.98))" in source
    assert 'url("/static/icons/paw_ui.png")' in source
    assert "border-radius: 20px;" in source
    assert "0 24px 80px rgba(37, 91, 143, 0.2)" in source
    assert "linear-gradient(135deg, rgba(76, 169, 255, 0.94), rgba(47, 150, 242, 0.92))" in source
    assert ".avatar-multiscreen-drag-hint-visible" in source


def test_model_interactions_report_supported_bounces_and_display_switch_success():
    live2d = _source("static/live2d-interaction.js")
    mmd = _source("static/mmd-interaction.js")
    vrm = _source("static/vrm-interaction.js")

    assert "recordEdgeBounce('live2d')" in live2d
    assert "markDisplaySwitchSuccess('live2d')" in live2d
    assert "recordEdgeBounce('mmd')" in mmd
    assert "markDisplaySwitchSuccess('mmd')" in mmd
    assert "recordEdgeBounce('vrm')" in vrm
    assert "markDisplaySwitchSuccess('vrm')" in vrm


def test_multiscreen_drag_hint_script_loads_before_model_interactions():
    source = _source("templates/index.html")

    helper_index = source.index("/static/avatar-multiscreen-drag-hint.js")
    live2d_index = source.index("/static/live2d-interaction.js")
    vrm_index = source.index("/static/vrm-init.js")
    mmd_index = source.index("/static/mmd-init.js")

    assert helper_index < live2d_index
    assert helper_index < vrm_index
    assert helper_index < mmd_index
