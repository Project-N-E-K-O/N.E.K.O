from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vrm_initial_visibility_fence_uses_runtime_threshold():
    manager_source = (PROJECT_ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8")
    interaction_source = (PROJECT_ROOT / "static/vrm/vrm-interaction.js").read_text(encoding="utf-8")

    assert "clampModelPosition(position, { minVisiblePixels = 200 } = {})" in interaction_source
    assert "clampModelPosition(currentPos, { minVisiblePixels: 300 })" not in manager_source
    assert "const correctedPos = this.interaction.clampModelPosition(currentPos);" in manager_source


def test_vrm_display_switch_miss_records_bridge_errors_after_model_leaves_window():
    source = (PROJECT_ROOT / "static/vrm/vrm-interaction.js").read_text(encoding="utf-8")
    method_section = source.split("async _checkAndSwitchDisplay() {", 1)[1].split("\n\n    /**\n     * 兼容旧接口", 1)[0]

    assert method_section.index("const recordDisplaySwitchMiss = () => {") < method_section.index("try {")
    assert "let displaySwitchAttempted = false;" in method_section
    assert method_section.index("displaySwitchAttempted = true;") < method_section.index("window.electronScreen.getAllDisplays()")
    assert "if (displaySwitchAttempted) recordDisplaySwitchMiss();" in method_section
