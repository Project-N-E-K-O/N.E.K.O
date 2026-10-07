"""Exercise MMD completion with the bundled Three.js mixer and action."""

import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_mmd_animation_completion_respects_action_time_and_direction():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")

    result = run_node_script(
        node,
        r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
    const source = fs.readFileSync('static/libs/three.core.js');
    const THREE = await import('data:text/javascript;base64,' + source.toString('base64'));
    const context = vm.createContext({ window: { THREE }, console });
    const MMDAnimation = vm.runInContext(
        fs.readFileSync('static/mmd/mmd-animation.js', 'utf8') + '\nMMDAnimation;', context
    );
    // [name, scale, initial action time, delta, loop, expected completion]
    const cases = [
        ['slow playback stays active past mixer duration', 0.5, 0, 3, false, false],
        ['slow playback finishes', 0.5, 0, 4, false, true],
        ['fast playback finishes before mixer duration', 2, 0, 1, false, true],
        ['reverse playback in progress', -1, 2, 0.5, false, false],
        ['reverse playback reaches zero', -1, 2, 2, false, true],
        ['reverse playback overshoots zero', -2, 2, 2, false, true],
        ['reverse at end with zero delta', -1, 2, 0, false, false],
        ['frozen at start', 0, 0, 3, false, false],
        ['frozen at end', 0, 2, 3, false, false],
        ['forward loop', 2, 0, 3, true, false],
        ['reverse loop', -2, 2, 3, true, false],
    ];
    for (const path of ['slot', 'legacy', 'crossfade']) {
        for (const [name, scale, start, delta, loop, finished] of cases) {
            const mesh = new THREE.Object3D();
            mesh.skeleton = { bones: [] };
            let resets = 0;
            const animation = new MMDAnimation({
                currentModel: { mesh },
                core: { resetModelPose() { resets++; } },
            });
            const mixer = new THREE.AnimationMixer(mesh);
            const clip = new THREE.AnimationClip('test', 2, []);
            const action = mixer.clipAction(clip);
            action.clampWhenFinished = true;
            action.play();
            action.time = start;
            if (path === 'legacy') {
                Object.assign(animation, { mixer, currentAction: action, currentClip: clip });
            } else {
                Object.assign(animation._slotA, { mixer, action, clip });
                animation._activeSlot = animation._slotA;
                if (path === 'crossfade') {
                    animation._isCrossfading = true;
                    animation._snapshotCrossfade = true;
                    animation._stopSnapshot = [];
                    animation._fadeDuration = 1;
                }
            }
            animation.setLoop(loop);
            animation.setTimeScale(scale);
            animation.isPlaying = true;
            animation.update(delta);
            const label = `${path}: ${name}`;
            assert.equal(animation.isPlaying, !finished, label);
            assert.equal(animation.isPaused, finished, label);
            assert.equal(resets, finished ? 1 : 0, label);
            if (finished) {
                animation.update(1);
                assert.equal(resets, 1, `${label}: cleanup only once`);
            }
        }
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
""",
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
