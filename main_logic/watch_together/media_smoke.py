"""Offline codec/worker smoke test, also executed by the frozen release binary."""
from fractions import Fraction
import os
from pathlib import Path
import tempfile
import wave

import av
import numpy as np

from . import media


def make_sources(root):
    video, audio = root / "video.mkv", root / "speech.ogg"
    with av.open(str(video), "w") as out:
        stream = out.add_stream("ffv1", rate=2)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for index in range(7):
            pixels = np.full((48, 64, 3), index * 30, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 2)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    with av.open(str(audio), "w", format="ogg") as out:
        stream = out.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        for offset in range(0, 168000, 4800):
            t = np.arange(offset, offset + 4800) / 48000
            samples = (np.sin(2 * np.pi * 440 * t) * 4000).astype(np.int16)[None, :]
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = 48000, offset, Fraction(1, 48000)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return video, audio


def exercise(root):
    media.check_available()
    video, audio = make_sources(root)
    target = root / "combined.mp4"
    media.run("mux", video, audio, target)
    with av.open(str(target)) as container:
        assert [s.codec_context.name for s in container.streams] == ["h264", "aac"]
    assert 3.4 < media.run("duration", target) < 3.7
    assert media.run("probe", target, "video")
    assert media.run("probe", target, "audio")
    # Exercise packet-copy muxing from a combined source too.
    copied = root / "copied.mp4"
    media.run("mux", target, None, copied)
    directory = root / "frames"
    directory.mkdir()
    frames = media.run("frames", copied, directory, 1.0)
    assert [round(at, 2) for at, _ in frames] == [0, 1, 2, 3]
    assert media.run("frame", copied, 1.5, root / "hotspot.jpg")
    wav = root / "speech.wav"
    media.run("speech", audio.read_bytes(), wav)
    with wave.open(str(wav)) as stream:
        assert stream.getparams()[:3] == (1, 2, 48000)
        assert 3.49 < stream.getnframes() / 48000 < 3.51


def main():
    # Release binaries must work even on a host with no external media tools.
    os.environ["PATH"] = ""
    with tempfile.TemporaryDirectory(prefix="neko-media-smoke-") as root:
        exercise(Path(root))
    print("NEKO_MEDIA_RELEASE_SMOKE_OK", flush=True)
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
