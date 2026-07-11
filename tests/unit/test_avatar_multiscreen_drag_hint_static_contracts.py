from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_multiscreen_drag_hint_ack_snoozes_for_three_days():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "const SNOOZE_MS = 3 * 24 * 60 * 60 * 1000;" in source
    assert "state.snoozeUntil = now() + SNOOZE_MS;" in source
    assert "neko:avatar-multiscreen-drag-hint:v1" in source


def test_multiscreen_drag_hint_counts_display_switch_misses_only_on_multiple_displays():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "const REQUIRED_MISSES = 2;" in source
    assert "const MISS_WINDOW_MS = 30 * 1000;" in source
    assert "function hasDisplaySwitchBridge()" in source
    assert "typeof window.electronScreen.moveWindowToDisplay === 'function'" in source
    assert "typeof window.electronScreen.getCurrentDisplay === 'function'" in source
    assert "async function hasMultipleDisplays()" in source
    assert "window.electronScreen.getAllDisplays" in source
    assert "if (!hasDisplaySwitchBridge()) return false;" in source
    assert "return Array.isArray(displays) && displays.length > 1;" in source
    assert "function recordDisplaySwitchMiss(source)" in source
    assert "if (!(await hasMultipleDisplays())) return false;" in source
    assert "state.recentMissCount >= REQUIRED_MISSES" in source
    assert "async function isModelCenterOnAnotherDisplay(model)" in source
    assert "if (!displaySwitched && await isModelCenterOnAnotherDisplay(model))" in source


def test_multiscreen_drag_hint_serializes_display_switch_miss_updates():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "let missRecordQueue = Promise.resolve();" in source
    assert "function recordDisplaySwitchMiss(source) {" in source
    assert "const nextRecord = missRecordQueue.then(function () {" in source
    assert "return recordDisplaySwitchMissNow(normalizedSource);" in source
    assert "missRecordQueue = nextRecord.catch(function () {});" in source
    assert "function recordDisplaySwitchMissNow(source)" in source


def test_multiscreen_drag_hint_records_pointer_edge_release_intent():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "const EDGE_RELEASE_THRESHOLD_PX = 180;" in source
    assert "const MIN_EDGE_DRAG_DISTANCE_PX = 48;" in source
    assert "function getPointerEdgeIntents(pointer, currentDisplay)" in source
    assert "function hasAdjacentDisplayForEdge(displays, currentDisplay, edge, pointer)" in source
    assert "async function recordPointerEdgeApproach(source, pointer)" in source
    assert "async function recordPointerEdgeRelease(source, pointer)" in source
    assert "edges.some(edge => hasAdjacentDisplayForEdge(displays, currentDisplay, edge, pointer))" in source
    assert "wasDisplaySwitchMissRecordedSince(source, startedAt)" in source
    assert "window.electronScreen.getCurrentDisplay" in source
    assert "recordPointerEdgeApproach," in source
    assert "recordPointerEdgeRelease," in source


def test_multiscreen_drag_hint_can_be_disabled_or_suppressed_after_success():
    source = _source("static/avatar-multiscreen-drag-hint.js")

    assert "state.never = true;" in source
    assert "state.successAt = now();" in source
    assert "window.NekoAvatarMultiScreenDragHint" in source
    assert "state.recentMissCount = 0;" in source
    assert "state.lastMissAt = 0;" in source
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


def test_model_interactions_report_display_switch_misses_and_success():
    helper = _source("static/avatar-multiscreen-drag-hint.js")
    live2d = _source("static/live2d/live2d-interaction.js")
    mmd = _source("static/mmd-interaction.js")
    vrm = _source("static/vrm-interaction.js")

    assert "installLive2DDisplaySwitchMissHook" in helper
    assert "window.Live2DManager" in helper
    assert "_checkAndSwitchDisplay" in helper
    assert "recordDisplaySwitchMiss('live2d')" in helper
    assert "recordDisplaySwitchMiss('live2d')" not in live2d
    assert "recordPointerEdgeApproach('live2d'" in live2d
    assert "recordPointerEdgeRelease('live2d'" in live2d
    assert "recordEdgeBounce('live2d')" not in live2d
    assert "markDisplaySwitchSuccess('live2d')" in live2d
    assert "recordDisplaySwitchMiss('mmd')" in mmd
    assert "void this._recordDragHintPointerEdgeApproach('mmd');" in mmd
    assert "if (!targetDisplay) {\n                return false;\n            }" in mmd
    mmd_release = mmd.split("if (!displaySwitched) {", 1)[1].split("// 鼠标离开", 1)[0]
    assert mmd_release.index("if (wasPanDrag) {") < mmd_release.index(
        "await this._recordDragHintPointerEdgeRelease('mmd');"
    )
    assert "recordEdgeBounce('mmd')" not in mmd
    assert "markDisplaySwitchSuccess('mmd')" in mmd
    assert "recordDisplaySwitchMiss('vrm')" in vrm
    assert "void this._recordDragHintPointerEdgeApproach('vrm');" in vrm
    assert "if (!targetDisplay) {\n                return false;\n            }" in vrm
    vrm_release = vrm.split("if (!displaySwitched) {", 1)[1].split("// 5. 鼠标进入", 1)[0]
    assert vrm_release.index("if (wasPanDrag) {") < vrm_release.index(
        "await this._recordDragHintPointerEdgeRelease('vrm');"
    )
    assert "recordEdgeBounce('vrm')" not in vrm
    assert "markDisplaySwitchSuccess('vrm')" in vrm


def test_model_renderers_refresh_pixel_density_after_display_switch():
    live2d_core = _source("static/live2d/live2d-core.js")
    mmd_core = _source("static/mmd-core.js")
    vrm_core = _source("static/vrm-core.js")

    assert "lastDevicePixelRatio = window.devicePixelRatio || 1;" in live2d_core
    assert "renderer.resolution = nextResolution;" in live2d_core
    assert "electron-display-changed:settled" in live2d_core

    assert "syncRendererPixelRatio(reason = 'resize')" in vrm_core
    assert "window.addEventListener('electron-display-changed', this.manager._displayChangeHandler);" in vrm_core
    assert "this.manager.core.syncRendererPixelRatio('electron-display-changed:settled');" in vrm_core

    assert "syncRendererPixelRatio(reason = 'resize')" in mmd_core
    assert "window.addEventListener('electron-display-changed', this.manager._displayChangeHandler);" in mmd_core
    assert "this.onWindowResize('electron-display-changed:settled')" in mmd_core


def test_multiscreen_drag_hint_script_loads_before_model_interactions():
    source = _source("templates/index.html")

    helper_index = source.index("/static/avatar-multiscreen-drag-hint.js")
    live2d_index = source.index("/static/live2d/live2d-interaction.js")
    vrm_index = source.index("/static/vrm-init.js")
    mmd_index = source.index("/static/mmd-init.js")

    assert helper_index < live2d_index
    assert helper_index < vrm_index
    assert helper_index < mmd_index
