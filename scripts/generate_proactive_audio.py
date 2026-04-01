"""
Generate pre-recorded proactive audio prompts for voice-mode proactive chat.

Produces PCM16, 16kHz, mono WAV files in static/proactive_audio/.
Each file is a short (~1-3s) conversational prompt used to trigger AI proactive speech.

Usage:
    uv run python scripts/generate_proactive_audio.py
"""

import asyncio
import io
import struct
import wave
from pathlib import Path

import edge_tts

# Output directory
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "proactive_audio"

# Target format
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # 16-bit

# Voice selections per language (natural, casual-sounding voices)
VOICES = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "en": "en-US-AriaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "ru": "ru-RU-SvetlanaNeural",
}

# Prompt texts: (vision_prompt, general_prompt)
# Keep it minimal and natural — just a conversational nudge, not a full question.
PROMPTS = {
    "zh": (
        "嗯……瞧！",
        "嗯……嗯……",
    ),
    "en": (
        "Hmm... look!",
        "Hmm... hmm... hmm...",
    ),
    "ja": (
        "うーん……ほら！",
        "うーんうーん……",
    ),
    "ko": (
        "음……봐!",
        "음……음……",
    ),
    "ru": (
        "Хмм... смотри!",
        "Хмм...",
    ),
}


def mp3_to_wav16k(mp3_bytes: bytes) -> bytes:
    """Convert MP3 bytes to PCM16 16kHz mono WAV bytes using pydub."""
    from pydub import AudioSegment

    seg = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
    seg = seg.set_frame_rate(TARGET_SAMPLE_RATE).set_channels(TARGET_CHANNELS).set_sample_width(TARGET_SAMPLE_WIDTH)

    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


def append_silence(wav_bytes: bytes, duration_ms: int = 500) -> bytes:
    """Append silence to WAV to help VAD detect end of speech."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        params = wf.getparams()
        pcm = wf.readframes(wf.getnframes())

    n_silence_samples = int(params.framerate * duration_ms / 1000)
    silence = b"\x00\x00" * n_silence_samples * params.nchannels

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setparams(params)
        wf.writeframes(pcm + silence)
    return buf.getvalue()


async def generate_one(text: str, voice: str, output_path: Path) -> None:
    """Generate a single audio prompt."""
    comm = edge_tts.Communicate(text, voice, rate="-5%")
    mp3_buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            mp3_buf.write(chunk["data"])

    mp3_bytes = mp3_buf.getvalue()
    if not mp3_bytes:
        print(f"  WARNING: empty audio for {output_path.name}")
        return

    wav_bytes = mp3_to_wav16k(mp3_bytes)
    wav_bytes = append_silence(wav_bytes, duration_ms=500)

    output_path.write_bytes(wav_bytes)

    # Print stats
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        duration = wf.getnframes() / wf.getframerate()
    size_kb = len(wav_bytes) / 1024
    print(f"  {output_path.name}: {duration:.1f}s, {size_kb:.0f}KB")


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Output: {OUTPUT_DIR}\n")

    tasks = []
    for lang, (vision_text, general_text) in PROMPTS.items():
        voice = VOICES[lang]
        print(f"[{lang}] voice={voice}")
        print(f"  vision: {vision_text}")
        print(f"  general: {general_text}")

        tasks.append(generate_one(vision_text, voice, OUTPUT_DIR / f"prompt_vision_{lang}.wav"))
        tasks.append(generate_one(general_text, voice, OUTPUT_DIR / f"prompt_general_{lang}.wav"))

    await asyncio.gather(*tasks)
    print(f"\nDone. {len(tasks)} files generated in {OUTPUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
