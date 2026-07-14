from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_soccer_vrm0_fixed_camera_facing_fix_is_scoped_to_vrm0():
    source = (PROJECT_ROOT / "templates/soccer_demo.html").read_text(encoding="utf-8")

    assert "function isSoccerVrm0(gltf, vrm)" in source
    assert "if (exts.includes('VRMC_vrm')) return false;" in source
    assert "if (exts.includes('VRM')) return true;" in source
    assert "function shouldNormalizeSoccerVrm0FixedCameraYaw(gltf, vrm)" in source


def test_soccer_vrm0_fixed_camera_facing_fix_uses_bone_and_head_evidence():
    source = (PROJECT_ROOT / "templates/soccer_demo.html").read_text(encoding="utf-8")

    assert "function countSoccerVrmReversedBonePairs(vrm)" in source
    assert "['leftEye', 'rightEye']" in source
    assert "['leftUpperArm', 'rightUpperArm']" in source
    assert "function sampleSoccerVrmHeadFaceZ(vrm)" in source
    assert "headFaceZ.negative > headFaceZ.positive * 1.25" in source


def test_soccer_vrm0_fixed_camera_facing_fix_runs_on_both_soccer_load_paths():
    source = (PROJECT_ROOT / "templates/soccer_demo.html").read_text(encoding="utf-8")

    assert "function syncSoccerVrmCameraTarget(manager, lookY, dist)" in source
    assert "manager.controls.target.copy(cameraTarget);" in source

    fit_section = source.split("function fitVrmManagerCamera", 1)[1].split(
        "function isSoccerVrm0",
        1,
    )[0]
    assert "syncSoccerVrmCameraTarget(manager, lookY, dist);" in fit_section

    helper_section = source.split(
        "window.__SoccerLoadVrmIntoManager = async function loadVrmIntoManager",
        1,
    )[1].split("return vrm;", 1)[0]
    assert "applySoccerVrm0FixedCameraFacingFix(gltf, vrm, manager);" in helper_section
    assert "fitVrmManagerCamera(manager, containerId, label);" in helper_section

    player_section = source.split(
        "async function setPlayerAvatar({ type, path } = {})",
        1,
    )[1].split("emitEvent('player-avatar-changed'", 1)[0]
    assert "applySoccerVrm0FixedCameraFacingFix(gltf, vrm, window.vrmManager);" in player_section
    assert "syncSoccerVrmCameraTarget(window.vrmManager, lookY, dist);" in player_section


def test_soccer_vrm0_fixed_camera_facing_fix_keeps_yaw_offset_alive():
    soccer_source = (PROJECT_ROOT / "templates/soccer_demo.html").read_text(encoding="utf-8")
    interaction_source = (PROJECT_ROOT / "static/vrm/vrm-interaction.js").read_text(encoding="utf-8")

    assert "manager.__soccerFixedCameraNormalizeYaw = shouldNormalizeYaw;" in soccer_source
    assert "vrm.scene.rotation.y = Math.PI;" in soccer_source
    assert "if (this.manager.__soccerFixedCameraNormalizeYaw)" in interaction_source
    assert "targetAngle += Math.PI;" in interaction_source
