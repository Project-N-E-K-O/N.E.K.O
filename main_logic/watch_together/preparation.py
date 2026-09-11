"""Preparation adapter uses the character's provider-neutral official TTS cache."""
import asyncio
import json
import uuid
import wave

from .engine import Engine
from .library import application_library

jobs = {}
tasks = set()


async def prepare(url, manager, character, *, automatic=False, confirmed_duration=None):
    if tasks:
        raise ValueError("A video is already being prepared")
    library = application_library()
    voice_signature = manager.current_game_speech_audio_runtime_signature()
    from utils.language_utils import get_global_language_full, normalize_language_code
    language = normalize_language_code(getattr(manager, "user_language", None) or get_global_language_full(), format="full")
    job = {"id": uuid.uuid4().hex, "status": "working", "stage": "Preparing", "stage_key": "checking", "events": []}
    jobs[job["id"]] = job

    async def synthesize(text, output):
        from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
        key, signature = manager.game_speech_audio_cache_identity(text)
        if signature != voice_signature:
            raise ValueError("Character voice changed during preparation")
        result = await manager.preload_game_speech_audio([text])
        if not result.get("ok") or manager.game_speech_audio_cache_identity(text) != (key, signature):
            raise ValueError("Character voice changed or synthesis failed")
        chunks = GAME_SPEECH_AUDIO_CACHE.get(key)
        if not chunks:
            raise ValueError("Synthesized audio unavailable")
        # All official workers normalize to signed 16-bit, mono, 48kHz PCM.
        with wave.open(str(output), "wb") as stream:
            stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            stream.writeframes(b"".join(chunks))

    async def run():
        staging = library.root / "preparations"
        try:
            engine = Engine(staging, synthesize, character, language=language)
            async with asyncio.timeout(1800):
                await engine.prepare(job, url, character, automatic=automatic, confirmed_duration=confirmed_duration)
        except asyncio.CancelledError:
            job.update(status="cancelled", stage="Cancelled", stage_key="cancelled")
            raise
        except Exception as exc:
            job.update(status="error", stage="Preparation failed", stage_key="prepareFailed", error=type(exc).__name__)
            print(f"Watch preparation failed: {type(exc).__name__}")
        finally:
            folder = staging / job["id"]
            try:
                if folder.exists():
                    (folder / "timeline.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
                    await asyncio.to_thread(library.import_sources, [staging], only_job=job["id"], write_report=False)
            except Exception as exc:
                # Keep staged artifacts for recovery, but give polling clients a
                # terminal state even when the history transaction never commits.
                job.update(status="error", stage="Saving preparation failed", stage_key="saveFailed", error=type(exc).__name__)
                print(f"Watch preparation persistence failed: {type(exc).__name__}")

    task = asyncio.create_task(run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"id": job["id"]}
