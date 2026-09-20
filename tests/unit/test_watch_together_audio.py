import math
import shutil
import struct
import wave

import pytest

from main_logic.watch_together.audio import write_speech_wav
from main_logic.watch_together.engine import run_media


def test_pcm_audio_retains_samples(tmp_path):
    payload = struct.pack('<4h', 0, 1000, -1000, 0)
    output = tmp_path / 'pcm.wav'
    write_speech_wav([payload[:3], payload[3:]], output)
    with wave.open(str(output)) as stream:
        assert stream.getparams()[:3] == (1, 2, 48000)
        assert stream.readframes(4) == payload


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg integration prerequisite')
def test_ogg_transport_decodes_to_samples_and_real_duration(tmp_path):
    source = tmp_path / 'tone.ogg'
    run_media('ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.25', '-c:a', 'libopus', source)
    encoded = source.read_bytes()
    output = tmp_path / 'decoded.wav'
    write_speech_wav([encoded[:2], encoded[2:99], encoded[99:]], output)
    with wave.open(str(output)) as stream:
        assert stream.getparams()[:3] == (1, 2, 48000)
        assert .24 < stream.getnframes() / stream.getframerate() < .26
        samples = struct.unpack('<' + 'h' * stream.getnframes(), stream.readframes(stream.getnframes()))
    rms = math.sqrt(sum(x*x for x in samples)/len(samples)) / 32768
    assert .07 < rms < .11
    crossings = sum(a < 0 <= b for a, b in zip(samples, samples[1:]))
    assert 105 < crossings < 115  # 440 Hz for a quarter second, not compressed-byte noise.
