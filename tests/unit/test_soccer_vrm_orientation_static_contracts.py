from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOCCER_AVATAR_HOST_PATH = (
    PROJECT_ROOT / "static/game/games/soccer/soccer-avatar-host.js"
)


def test_soccer_vrm0_fixed_camera_facing_fix_is_scoped_to_vrm0():
    source = SOCCER_AVATAR_HOST_PATH.read_text(encoding="utf-8")

    assert "function isVrm0(gltf, vrm)" in source
    assert "if (extensions.includes('VRMC_vrm')) return false;" in source
    assert "if (extensions.includes('VRM')) return true;" in source
    assert "function applyVrm0FixedCameraFacingFix(gltf, vrm, manager)" in source


def test_soccer_vrm0_fixed_camera_facing_fix_uses_bone_and_head_evidence():
    source = SOCCER_AVATAR_HOST_PATH.read_text(encoding="utf-8")

    assert "function countReversedVrmBonePairs(vrm)" in source
    assert "['leftEye', 'rightEye']" in source
    assert "['leftUpperArm', 'rightUpperArm']" in source
    assert "function sampleVrmHeadFaceZ(vrm)" in source
    assert "headFaceZ.negative > headFaceZ.positive * 1.25" in source


def test_soccer_vrm0_fixed_camera_facing_fix_runs_on_both_soccer_load_paths():
    source = SOCCER_AVATAR_HOST_PATH.read_text(encoding="utf-8")

    assert "function syncVrmCameraTarget(manager, lookY, distance)" in source
    assert "manager.controls.target.copy(target);" in source

    fit_section = source.split("function fitVrmManagerCamera", 1)[1].split(
        "function isVrm0",
        1,
    )[0]
    assert "syncVrmCameraTarget(manager, visibleHeight / 2, distance);" in fit_section

    helper_section = source.split(
        "async function loadVrmIntoManager",
        1,
    )[1].split("return manager.currentModel;", 1)[0]
    assert "applyVrm0FixedCameraFacingFix(gltf, vrm, manager);" in helper_section
    assert "fitVrmManagerCamera(manager, containerId, label, viewport);" in helper_section

    controller_section = source.split("function createController", 1)[1]
    assert "canvasId: 'player-vrm-canvas'" in controller_section
    assert "canvasId: 'ai-l2d-canvas'" in controller_section
    assert controller_section.count("await loadVrmIntoManager(") >= 2


def test_soccer_vrm0_fixed_camera_facing_fix_keeps_yaw_offset_alive():
    soccer_source = SOCCER_AVATAR_HOST_PATH.read_text(encoding="utf-8")
    interaction_source = (PROJECT_ROOT / "static/vrm/vrm-interaction.js").read_text(encoding="utf-8")

    assert "manager.__soccerFixedCameraNormalizeYaw = shouldNormalize;" in soccer_source
    assert "vrm.scene.rotation.y = Math.PI;" in soccer_source
    assert "if (this.manager.__soccerFixedCameraNormalizeYaw)" in interaction_source
    assert "targetAngle += Math.PI;" in interaction_source
