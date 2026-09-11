"""Preparation adapter uses the character's provider-neutral official TTS cache."""
import asyncio
import json
import uuid

from .engine import Engine
from .library import application_library

jobs = {}
tasks = set()
pending_confirmations = {}


def confirm_preparation(identifier, manager, accepted, duration):
    pending = pending_confirmations.get(identifier)
    if (not pending or pending[0] is not manager or pending[1].done()
            or not isinstance(accepted, bool) or duration != pending[2]):
        raise ValueError("Preparation confirmation is no longer valid")
    pending[1].set_result(accepted)
    return {"ok": True}


async def prepare(url, manager, character, *, automatic=False, confirmed_duration=None, render_language=None):
    if tasks:
        raise ValueError("A video is already being prepared")
    library = application_library()
    from utils.language_utils import get_global_language_full, normalize_language_code
    explicit_language = (getattr(manager, "user_language", None)
                         if getattr(manager, "_user_language_explicit", False) else None)
    language = normalize_language_code(
        explicit_language or render_language or getattr(manager, "_conversation_render_language", None)
        or getattr(manager, "_conversation_turn_language", None)
        or getattr(manager, "user_language", None) or get_global_language_full(), format="full")
    voice_signature = manager.game_speech_audio_cache_identity("", render_language=language)[1]
    persona = str(getattr(manager, "lanlan_prompt", "") or "")
    job = {"id": uuid.uuid4().hex, "status": "working", "stage": "Preparing", "stage_key": "checking", "events": []}
    jobs[job["id"]] = job

    async def confirm_download(title, duration):
        future = asyncio.get_running_loop().create_future()
        pending_confirmations[job["id"]] = (manager, future, duration)
        job.update(status="awaiting_confirmation", confirmation_required=True,
                   confirmation_video={"title": title, "duration": duration}, stage_key="longWarning")
        try:
            accepted = await asyncio.wait_for(future, 300)
            job.update(status="working", stage_key="checking")
            return accepted
        finally:
            pending_confirmations.pop(job["id"], None)
            job.pop("confirmation_required", None)
            job.pop("confirmation_video", None)

    async def synthesize(text, output):
        from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
        key, signature = manager.game_speech_audio_cache_identity(text, render_language=language)
        if signature != voice_signature:
            raise ValueError("Character voice changed during preparation")
        result = await manager.preload_game_speech_audio([text], render_language=language)
        if not result.get("ok") or manager.game_speech_audio_cache_identity(text, render_language=language) != (key, signature):
            raise ValueError("Character voice changed or synthesis failed")
        chunks = GAME_SPEECH_AUDIO_CACHE.get(key)
        if not chunks:
            raise ValueError("Synthesized audio unavailable")
        from .audio import write_speech_wav_async
        await write_speech_wav_async(chunks, output)

    async def run():
        staging = library.root / "preparations"
        try:
            engine = Engine(staging, synthesize, character, language=language, persona=persona)
            async with asyncio.timeout(1800):
                await engine.prepare(job, url, character, automatic=automatic,
                                     confirmed_duration=confirmed_duration, confirm_download=confirm_download)
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
