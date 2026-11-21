"""Preparation adapter uses the character's provider-neutral official TTS cache."""
import asyncio
import json
import os
import re
import uuid
from urllib.parse import quote

from .engine import Engine, SpeechCueTooLarge, endpoint_host
from . import media
from .library import application_library
from .telemetry import count

jobs = {}
tasks = set()
pending_confirmations = {}

MAX_CAUSE_DEPTH = 4
MAX_CAUSE_MESSAGE_CHARS = 200
# Provider messages can be large; bound the regex work before redacting.
_REDACTION_INPUT_CHARS = 2000
_DATA_URL = re.compile(r"\bdata:[^\s,]{0,100},\S*", re.IGNORECASE)
_SECRET_PATTERNS = (
    # h11 / httpx reject a malformed header with only its raw value in the
    # message (e.g. a key with stray whitespace), so no header name labels it.
    (re.compile(r"\b(Illegal header (?:value|name))\b[^\r\n]*", re.IGNORECASE), r"\1 ***"),
    (re.compile(r"\b(Authorization['\"]?\s*[:=]\s*['\"]?)(?:[A-Za-z]+\s+)?[^\s'\",;}]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"\bBearer\s+[^\s'\",;}]+", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]+"), "sk-***"),
    # Everything after a Cookie / Set-Cookie header name is credentials (quoted
    # or not, any number of pairs): mask to the end of the line. Over-masking
    # a one-line repr or JSON that embeds the header is the safe side.
    (re.compile(r"\b(Cookie['\"]?\s*[:=]\s*)[^\r\n]+", re.IGNORECASE), r"\1***"),
    # Quoted values run to the closing quote, or to the end of the bounded
    # input when truncation cut the quote off.
    (re.compile(r"\b([A-Za-z_\-]{0,32}(?:key|token|secret|password|cookie)['\"]?\s*[:=]\s*)"
                r"(?:'[^']*'?|\"[^\"]*\"?|[^\s&'\",;}]+)", re.IGNORECASE), r"\1***"),
)
_URL = re.compile(r"\b([A-Za-z][A-Za-z0-9+.\-]{0,15})://[^\s'\"<>]+")


def _url_origin(match):
    return f"{match.group(1)}://{endpoint_host(match.group(0)) or '?'}"


def _secret_variants(secrets):
    """Configured credentials as they may appear in messages, longest first."""
    variants = set()
    for secret in secrets or ():
        if isinstance(secret, str):
            # Short values (``none``, ``local``) would mask ordinary words.
            for value in (secret, secret.strip()):
                # Also as a bytes/str repr escapes it and as a URL encodes it.
                forms = (value, repr(value)[1:-1], repr(value.encode("utf-8", "replace"))[2:-1], quote(value, safe=""))
                variants.update(form for form in forms if len(form.strip()) >= 8)
    return sorted(variants, key=len, reverse=True)


def redact_error_message(message, secrets=()) -> str:
    """Keep an exception message useful for diagnosis without credentials, URL paths or home directory.

    ``secrets`` are configured credentials, masked wherever they appear:
    pattern rules cannot recognise an unlabelled raw key.
    """
    text = str(message)
    # Before truncating, so a length cut can never leave half a key behind.
    for secret in _secret_variants(secrets):
        text = text.replace(secret, "***")
    # Lone surrogates (non-UTF-8 file names) would break the UTF-8 timeline write.
    text = text[:_REDACTION_INPUT_CHARS].encode("utf-8", "backslashreplace").decode("utf-8")
    text = _DATA_URL.sub("data:***", text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    # URLs keep only scheme and host[:port]: paths and queries may carry keys or IDs.
    text = _URL.sub(_url_origin, text)
    # OSError messages carry local paths (Windows ones repr-escaped, or with
    # forward slashes as ffmpeg prints them); users paste these logs into
    # public issues.
    home = os.path.expanduser("~")
    if len(home) > 1:
        for form in (home.replace("\\", "\\\\"), home, home.replace("\\", "/")):
            text = text.replace(form, "~")
    if len(text) > MAX_CAUSE_MESSAGE_CHARS:
        text = text[:MAX_CAUSE_MESSAGE_CHARS] + "…"
    return text


def _error_layer(exc, secrets=()) -> dict:
    errno = getattr(exc, "errno", None)
    layer = {"type": type(exc).__name__,
             "errno": errno if isinstance(errno, int) and not isinstance(errno, bool) else None}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        # Provider status errors embed the response body, which may echo
        # request content; keep only the status and the provider error code.
        code = getattr(exc, "code", None)
        # Some OpenAI-compatible services use numeric codes (e.g. 1301).
        if isinstance(code, int) and not isinstance(code, bool):
            code = str(code)
        layer.update(status_code=status, code=redact_error_message(code, secrets)[:64] if isinstance(code, str) else None,
                     message=None)
        return layer
    try:
        layer["message"] = redact_error_message(exc, secrets)
    except Exception:
        layer["message"] = None
    return layer


def error_cause_chain(exc, limit=MAX_CAUSE_DEPTH, secrets=()):
    """Describe the exception and up to ``limit - 1`` underlying causes.

    Provider SDKs wrap transport failures (for example an httpx ReadError with
    an errno) in a generic connection error, so the class name alone does not
    tell a dropped oversized request from DNS, TLS or proxy failures.
    """
    chain, seen = [], set()
    while isinstance(exc, BaseException) and len(chain) < limit and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(_error_layer(exc, secrets))
        # Explicit None checks: exceptions may define __len__/__bool__. Honour
        # ``raise ... from None``: a context hidden on purpose stays hidden
        # (the openai SDK raises status errors that way over httpx's error).
        if exc.__cause__ is not None:
            exc = exc.__cause__
        elif exc.__suppress_context__:
            exc = None
        else:
            exc = exc.__context__
    return chain


def preparation_error_detail(exc, engine=None) -> dict:
    """Structural failure detail: no prompt, danmaku, model output or frame data."""
    secrets = getattr(engine, "diagnostic_secrets", None)
    secrets = tuple(secrets) if isinstance(secrets, (tuple, list)) else ()
    detail = {"chain": error_cause_chain(exc, secrets=secrets)}
    endpoint = getattr(engine, "vision_endpoint", None)
    if isinstance(endpoint, dict):
        host, model = endpoint.get("host"), endpoint.get("model")
        detail.update(vision_host=host if isinstance(host, str) else None,
                      vision_model=redact_error_message(model, secrets)[:100] if isinstance(model, str) else None)
    request = getattr(engine, "last_vision_request", None)
    if isinstance(request, dict):
        detail["last_vision_request"] = dict(request)
    return detail


def is_available(manager) -> bool:
    """Check local preparation prerequisites without synthesis or network probes."""
    try:
        media.check_available()
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


def session_language(manager, render_language=None):
    """Resolve the speech language: explicit preference, request, then session."""
    from utils.language_utils import get_global_language_full, normalize_language_code
    explicit_language = (getattr(manager, "user_language", None)
                         if getattr(manager, "_user_language_explicit", False) else None)
    return normalize_language_code(
        explicit_language or render_language or getattr(manager, "_conversation_render_language", None)
        or getattr(manager, "_conversation_turn_language", None)
        or getattr(manager, "user_language", None) or get_global_language_full(), format="full")


async def prepare(url, manager, character, *, automatic=False, confirmed_duration=None, render_language=None):
    if tasks:
        raise ValueError("A video is already being prepared")
    library = await asyncio.to_thread(application_library)
    # Another request may have claimed the single slot during initialization.
    if tasks:
        raise ValueError("A video is already being prepared")
    language = session_language(manager, render_language)
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
        engine = None
        try:
            engine = Engine(staging, synthesize, character, language=language, persona=persona)
            async with asyncio.timeout(1800) as deadline:
                media.check_available()
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
            try:
                detail = preparation_error_detail(exc, engine)
            except Exception as detail_error:
                # Diagnostics must never keep the job from its terminal state.
                detail = {"chain": [{"type": type(exc).__name__}], "detail_error": type(detail_error).__name__}
            job["error_detail"] = detail
            # print, not logger (privacy rule): messages come from providers.
            print(f"Watch preparation failed: {type(exc).__name__} "
                  f"{json.dumps(detail, default=str)}")
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
                count("watch_together_prepared", source="discovery" if automatic else "manual",
                      outcome=str(job.get("status") or "unknown")[:24])

    task = asyncio.create_task(run())
    tasks.add(task)
    def finished(completed):
        tasks.discard(completed)
        # Keep terminal status for an hour of polling; persisted history remains.
        asyncio.get_running_loop().call_later(3600, jobs.pop, job["id"], None)
    task.add_done_callback(finished)
    return {"id": job["id"]}
