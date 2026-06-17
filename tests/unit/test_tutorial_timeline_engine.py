import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")
    return subprocess.run(
        [node_path, "-"],
        input=script,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_non_blocking_timeline_event_rejections_are_warned_not_unhandled():
    script = textwrap.dedent(
        """
        const { createTutorialTimelineEngine } = require('./static/tutorial/core/timeline-engine.js');

        (async () => {
            const warnings = [];
            const unhandled = [];
            let now = 0;
            const originalWarn = console.warn;
            console.warn = (...args) => warnings.push(args.map(String).join(' '));
            process.on('unhandledRejection', (error) => {
                unhandled.push(error && error.message ? error.message : String(error));
            });

            const engine = createTutorialTimelineEngine({
                now: () => now,
                wait: (delayMs) => {
                    now += Math.max(0, delayMs);
                    return Promise.resolve();
                },
                audioRuntime: {
                    play: () => Promise.resolve(),
                    waitForEnd: () => Promise.resolve(),
                },
                commandRegistry: {
                    dispatch: (event) => Promise.reject(new Error(event.id + ' failed')),
                },
            });

            const result = await engine.playScene({
                id: 'scene',
                audio: { voiceKey: 'voice' },
                timeline: [
                    { id: 'during-audio', command: 'visual.fx', atMs: 1 },
                    { id: 'after-audio', command: 'state.fx', afterAudioEnd: true },
                ],
            });

            await Promise.resolve();
            await new Promise((resolve) => setTimeout(resolve, 0));
            console.warn = originalWarn;
            console.log(JSON.stringify({ result, warnings, unhandled }));
        })().catch((error) => {
            console.error(error && error.stack ? error.stack : error);
            process.exit(1);
        });
        """
    )

    result = _run_node_harness(script)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"]["completed"] is True
    assert payload["unhandled"] == []
    assert len(payload["warnings"]) == 2
    assert "during-audio failed" in payload["warnings"][0]
    assert "after-audio failed" in payload["warnings"][1]
