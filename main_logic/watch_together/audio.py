"""Convert the official speech transport (Ogg Opus or PCM16) to real WAV."""
from pathlib import Path
import tempfile
import wave

from .engine import run_media


def write_speech_wav(chunks, output):
    payload = b"".join(chunks)
    if not payload:
        raise ValueError("Empty speech audio")
    if payload.startswith(b"OggS"):
        # Preserve the Ogg page stream, including chunk boundaries inside pages.
        with tempfile.TemporaryDirectory(prefix="neko-watch-audio-") as directory:
            source = Path(directory) / "speech.ogg"
            source.write_bytes(payload)
            run_media("ffmpeg", "-y", "-i", source, "-ac", "1", "-ar", "48000",
                      "-c:a", "pcm_s16le", output)
    else:
        if len(payload) % 2:
            raise ValueError("Invalid PCM16 speech payload")
        with wave.open(str(output), "wb") as stream:
            stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(payload)
