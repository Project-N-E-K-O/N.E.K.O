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

    target_block = source.split("    isNekoTutorialClickTarget(target) {", 1)[1].split(
        "    blockNekoTutorialClickEvent(event) {",
        1,
    )[0]
    assert "#live2d-container" in target_block
    assert "#vrm-container" in target_block
    assert "#mmd-container" in target_block
    assert "#live2d-canvas" in target_block
    assert "#vrm-canvas" in target_block
    assert "#mmd-canvas" in target_block
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
