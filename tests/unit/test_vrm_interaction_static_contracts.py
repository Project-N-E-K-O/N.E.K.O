from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vrm_initial_visibility_fence_uses_runtime_threshold():
    manager_source = (PROJECT_ROOT / "static/vrm-manager.js").read_text(encoding="utf-8")
    interaction_source = (PROJECT_ROOT / "static/vrm-interaction.js").read_text(encoding="utf-8")

    assert "clampModelPosition(position, { minVisiblePixels = 200 } = {})" in interaction_source
    assert "clampModelPosition(currentPos, { minVisiblePixels: 300 })" not in manager_source
    assert "const correctedPos = this.interaction.clampModelPosition(currentPos);" in manager_source
