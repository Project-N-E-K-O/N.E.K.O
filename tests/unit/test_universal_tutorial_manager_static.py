from pathlib import Path


UNIVERSAL_TUTORIAL_MANAGER_PATH = (
    Path(__file__).resolve().parents[2] / "static" / "universal-tutorial-manager.js"
)


def _read_manager() -> str:
    return UNIVERSAL_TUTORIAL_MANAGER_PATH.read_text(encoding="utf-8")


def test_home_tutorial_blocks_real_neko_click_targets_but_keeps_tutorial_controls():
    source = _read_manager()

    assert "this._nekoTutorialClickBlockHandler = this.blockNekoTutorialClickEvent.bind(this);" in source
    assert "this.blockNekoTutorialClickEvents();" in source
    assert "this.unblockNekoTutorialClickEvents();" in source

    selector_block = source.split("    getTutorialInteractiveSelectors() {", 1)[1].split(
        "    isTutorialControlledElement(element) {",
        1,
    )[0]
    target_block = source.split("    isNekoTutorialClickTarget(target) {", 1)[1].split(
        "    blockNekoTutorialClickEvent(event) {",
        1,
    )[0]
    assert "#live2d-container" in selector_block
    assert "#vrm-container" in selector_block
    assert "#mmd-container" in selector_block
    assert "#live2d-canvas" in selector_block
    assert "#vrm-canvas" in selector_block
    assert "#mmd-canvas" in selector_block
    assert "...this.getTutorialInteractiveSelectors()" in target_block
    assert "[id$=\"-floating-buttons\"]" in target_block
    assert "[id$=\"-lock-icon\"]" in target_block
    assert "[id$=\"-return-button-container\"]" in target_block

    block_event = source.split("    blockNekoTutorialClickEvent(event) {", 1)[1].split(
        "    blockNekoTutorialClickEvents() {",
        1,
    )[0]
    assert "this.isTutorialControlEventTarget(event && event.target)" in block_event
    assert "event.isTrusted === false" in block_event
    assert "this.isNekoTutorialClickTarget(event && event.target)" in block_event
    assert "event.stopImmediatePropagation()" in block_event


def test_home_tutorial_click_blocker_allows_intro_chat_activation_target():
    source = _read_manager()

    allowed_target_block = source.split("    isHomeIntroActivationClickTarget(target) {", 1)[1].split(
        "    isNekoTutorialClickTarget(target) {",
        1,
    )[0]
    block_event = source.split("    blockNekoTutorialClickEvent(event) {", 1)[1].split(
        "    blockNekoTutorialClickEvents() {",
        1,
    )[0]

    assert "this.yuiGuideDirector.awaitingIntroActivation !== true" in allowed_target_block
    assert "#text-input-area" in allowed_target_block
    assert "#chat-container" in allowed_target_block
    assert "data-compact-geometry-item=\"input\"" in allowed_target_block
    assert "data-compact-geometry-item=\"capsule\"" in allowed_target_block
    assert "this.isHomeIntroActivationClickTarget(event && event.target)" in block_event
    assert block_event.index("this.isHomeIntroActivationClickTarget(event && event.target)") < block_event.index(
        "this.isNekoTutorialClickTarget(event && event.target)"
    )


def test_home_tutorial_click_blocker_allows_manual_plugin_dashboard_target():
    source = _read_manager()

    manual_target_block = source.split("    isManualPluginDashboardOpenClickTarget(target) {", 1)[1].split(
        "    isNekoTutorialClickTarget(target) {",
        1,
    )[0]
    block_event = source.split("    blockNekoTutorialClickEvent(event) {", 1)[1].split(
        "    blockNekoTutorialClickEvents() {",
        1,
    )[0]

    assert "manualPluginDashboardOpenAllowed !== true" in manual_target_block
    assert "manualPluginDashboardOpenTarget" in manual_target_block
    assert "target === manualTarget" in manual_target_block
    assert "manualTarget.contains(target)" in manual_target_block
    assert "#neko-sidepanel-action-agent-user-plugin-management-panel" in manual_target_block
    assert "this.isManualPluginDashboardOpenClickTarget(event && event.target)" in block_event
    assert block_event.index("this.isManualPluginDashboardOpenClickTarget(event && event.target)") < block_event.index(
        "this.isNekoTutorialClickTarget(event && event.target)"
    )


def test_neko_tutorial_click_blocker_covers_click_and_pointer_events():
    source = _read_manager()
    install_block = source.split("    blockNekoTutorialClickEvents() {", 1)[1].split(
        "    unblockNekoTutorialClickEvents() {",
        1,
    )[0]
    uninstall_block = source.split("    unblockNekoTutorialClickEvents() {", 1)[1].split(
        "    blockTutorialPointerEvent(event) {",
        1,
    )[0]

    for event_name in (
        "pointerdown",
        "pointerup",
        "mousedown",
        "mouseup",
        "click",
        "dblclick",
        "auxclick",
        "contextmenu",
        "touchstart",
        "touchend",
    ):
        assert f"window.addEventListener('{event_name}'" in install_block
        assert f"window.removeEventListener('{event_name}'" in uninstall_block
