from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]


def test_audio_worklet_flush_emits_tail_and_completion_without_changing_full_blocks() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for audio processor behavioural contract")

    script = textwrap.dedent(
        """
        const fs = require('fs');
        class AudioWorkletProcessor {
          constructor() {
            this.port = { messages: [], postMessage: (...args) => {
              this.port.messages.push(args);
            }};
          }
        }
        global.AudioWorkletProcessor = AudioWorkletProcessor;
        global.registerProcessor = (_name, processorClass) => {
          global.AudioProcessor = processorClass;
        };
        eval(fs.readFileSync('static/audio-processor.js', 'utf8'));

        const processor = new global.AudioProcessor({
          processorOptions: { originalSampleRate: 48000, targetSampleRate: 48000 },
        });

        // A partial block is emitted only by flush, and the completion packet
        // carries the final PCM without a transfer list.
        processor.process([[new Float32Array([0, 0.5, -1])]], [], {});
        processor.port.onmessage({ data: { type: 'flush' } });
        if (processor.port.messages.length !== 1) {
          throw new Error(`expected one flush message, got ${processor.port.messages.length}`);
        }
        const first = processor.port.messages[0];
        if (first.length !== 1 || first[0].type !== 'flush_complete') {
          throw new Error(`unexpected flush packet: ${JSON.stringify(first)}`);
        }
        if (!(first[0].pcmData instanceof Int16Array) || first[0].pcmData.length !== 3) {
          throw new Error('flush packet did not contain the partial Int16Array');
        }
        if (Array.from(first[0].pcmData).join(',') !== '0,16383,-32767') {
          throw new Error(`unexpected PCM tail: ${Array.from(first[0].pcmData)}`);
        }

        // Flushing an already aligned stream still acknowledges completion
        // with an empty Int16Array, allowing teardown to proceed deterministically.
        processor.port.onmessage({ data: { type: 'flush' } });
        const second = processor.port.messages[1];
        if (second.length !== 1 || second[0].type !== 'flush_complete' ||
            !(second[0].pcmData instanceof Int16Array) || second[0].pcmData.length !== 0) {
          throw new Error('empty flush acknowledgement was not emitted');
        }

        // Complete blocks retain the pre-existing raw Int16Array message shape.
        const aligned = new global.AudioProcessor({
          processorOptions: { originalSampleRate: 48000, targetSampleRate: 48000 },
        });
        aligned.process([[new Float32Array(480).fill(0.25)]], [], {});
        if (aligned.port.messages.length !== 1 ||
            !(aligned.port.messages[0][0] instanceof Int16Array) ||
            aligned.port.messages[0][0].length !== 480) {
          throw new Error('full audio block changed its message shape');
        }
        aligned.port.onmessage({ data: { type: 'flush' } });
        if (aligned.port.messages.length !== 2 ||
            aligned.port.messages[1][0].type !== 'flush_complete' ||
            aligned.port.messages[1][0].pcmData.length !== 0) {
          throw new Error('aligned stream did not acknowledge flush');
        }
        """
    )
    result = run_node_script(
        node,
        script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
