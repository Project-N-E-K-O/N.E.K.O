"""Preparation adapter uses the character's provider-neutral official TTS cache."""
import asyncio
import json
import uuid

from .engine import Engine, SpeechCueTooLarge, media_binary
from .library import application_library

jobs = {}
tasks = set()
pending_confirmations = {}


def is_available(manager) -> bool:
    """Check local preparation prerequisites without synthesis or network probes."""
    from . import engine
    try:
        engine.media_binary('ffmpeg')
        engine.media_binary('ffprobe')
        if manager is None:
            return False
        vision = manager._config_manager.get_model_api_config('vision')
        if (not isinstance(vision.get('model'), str) or not vision['model'].strip()
                or (not vision.get('api_key') and not vision.get('is_custom'))):
            return False
        worker, key, _voice, provider, disabled, config = manager._resolve_tts_worker_spec()
        from main_logic.tts_client._infra import configured_tts_unavailable_worker
        if worker is configured_tts_unavailable_worker:
            return False
        if provider == 'gptsovits':
            # The worker reads tts_custom even when the resolved route config is tts_default.
            from utils.gptsovits_config import is_valid_http_url, resolve_worker_gsv_api_url
            if not is_valid_http_url(resolve_worker_gsv_api_url(manager._config_manager)):
                return False
        credentials_available = bool(key) or provider in ('custom', 'vllm_omni', 'local_cosyvoice', 'gptsovits')
        return bool(not disabled and credentials_available and manager._tts_worker_supports_completion(worker, provider, config))
    except Exception:
        return False


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
    library = await asyncio.to_thread(application_library)
    # Another request may have claimed the single slot during initialization.
    if tasks:
        raise ValueError("A video is already being prepared")
    from utils.language_utils import get_global_language_full, normalize_language_code
    explicit_language = (getattr(manager, "user_language", None)
                         if getattr(manager, "_user_language_explicit", False) else None)
    language = normalize_language_code(
        explicit_language or render_language or getattr(manager, "_conversation_render_language", None)
        or getattr(manager, "_conversation_turn_language", None)
        or getattr(manager, "user_language", None) or get_global_language_full(), format="full")
    voice_signature = manager.game_speech_audio_cache_identity("", render_language=language)[1]
    persona = str(getattr(manager, "lanlan_prompt", "") or "")
    job = {"id": uuid.uuid4().hex, "status": "working", "stage": "Preparing", "stage_key": "checking", "events": [],
           "persistence_complete": False}
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
        if manager.game_speech_audio_cache_identity(text, render_language=language) != (key, signature):
            raise ValueError("Character voice changed during preparation")
        if not result.get("ok") and any(item.get("reason") == "audio_too_large" for item in result.get("results", [])):
            raise SpeechCueTooLarge()
        if not result.get("ok"):
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
            async with asyncio.timeout(1800) as deadline:
                media_binary('ffmpeg')
                media_binary('ffprobe')
                await engine.vision_config()
                from config.prompts.prompts_watch_together import LAUGH_TEXT_BY_LANGUAGE
                probe = LAUGH_TEXT_BY_LANGUAGE.get(language, LAUGH_TEXT_BY_LANGUAGE["en"])
                speech = await manager.preflight_game_speech_audio(probe, render_language=language)
                if not speech.get("ok"):
                    raise ValueError("Character speech unavailable")
                await engine.prepare(job, url, character, automatic=automatic,
                                     confirmed_duration=confirmed_duration, confirm_download=confirm_download,
                                     deadline=deadline)
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
            finally:
                job["persistence_complete"] = True

    task = asyncio.create_task(run())
    tasks.add(task)
    def finished(completed):
        tasks.discard(completed)
        # Keep terminal status for an hour of polling; persisted history remains.
        asyncio.get_running_loop().call_later(3600, jobs.pop, job["id"], None)
    task.add_done_callback(finished)
    return {"id": job["id"]}
