"""Convert official Ogg Opus or PCM16 speech to WAV using bundled PyAV."""
from . import media


async def write_speech_wav_async(chunks, output):
    await media.run_async("speech", b"".join(chunks), output)


def write_speech_wav(chunks, output):
    media.run("speech", b"".join(chunks), output)
