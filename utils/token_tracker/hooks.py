# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""OpenAI SDK hook installation and stream usage interception."""

import functools
import logging
import threading
from urllib.parse import urlsplit

from utils.token_tracker import TokenTracker
from ._shared import logger
from .call_context import _current_call_type

_stream_options_blocklist: set = set()

_blocklist_lock = threading.Lock()

_hooks_install_lock = threading.Lock()

def _get_base_url(self_obj) -> str:
    """Extract base_url from an OpenAI client instance."""
    try:
        # self_obj is Completions / AsyncCompletions; its _client is OpenAI / AsyncOpenAI.
        client = getattr(self_obj, '_client', None)
        if client is None:
            return ""
        base_url = getattr(client, 'base_url', None)
        if base_url is None:
            return ""
        return str(base_url).rstrip('/')
    except Exception:
        return ""


def _is_plugin_model_gateway(base_url: str) -> bool:
    """The local gateway records upstream attempts itself, including failures.

    Forked plugins can inherit this SDK hook from Agent. Bypass only the exact
    local gateway endpoint so its SDK request is not counted a second time.
    """
    try:
        from config.network import resolve_user_plugin_base

        url = urlsplit(base_url)
        gateway = urlsplit(resolve_user_plugin_base())
        default_ports = {"http": 80, "https": 443}
        return (
            url.scheme in {"http", "https"}
            and url.scheme == gateway.scheme
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
            and url.hostname == gateway.hostname
            and (url.port if url.port is not None else default_ports[url.scheme])
            == (gateway.port if gateway.port is not None else default_ports[gateway.scheme])
            and url.path.rstrip("/") == gateway.path.rstrip("/") + "/api/models/v1"
            and url.username is None and url.password is None
            and not url.query and not url.fragment
        )
    except ValueError:
        return False

def _usage_to_dict(usage) -> dict:
    """Normalize the usage object into a dict so all fields (including provider-custom ones) can be retrieved.

    The OpenAI SDK parses usage with a Pydantic model; non-standard fields (like
    StepFun's cached_tokens) hide in model_extra in v2, and in v1 may be dropped but
    remain in __dict__.
    """
    if isinstance(usage, dict):
        return usage

    d = {}

    # Pydantic v2: model_dump() omits extra fields, so merge model_extra.
    if hasattr(usage, 'model_dump'):
        try:
            d = usage.model_dump()
        except Exception:
            d = {}
        # model_extra contains fields unknown to the Pydantic model (e.g. Step cached_tokens).
        extra = getattr(usage, 'model_extra', None)
        if extra and isinstance(extra, dict):
            d.update(extra)
    # Pydantic v1: .dict()
    elif hasattr(usage, 'dict'):
        try:
            d = usage.dict()
        except Exception:
            d = {}

    # Fallback: __dict__ may contain more fields.
    if hasattr(usage, '__dict__'):
        for k, v in usage.__dict__.items():
            if not k.startswith('_') and k not in d:
                d[k] = v

    return d

_CACHED_TOKEN_FIELDS = (
    'cached_tokens',                # Step (StepFun): usage.cached_tokens.
    'cache_read_input_tokens',      # Anthropic Claude
    'prompt_cache_hit_tokens',      # Some Chinese providers.
    'cached_content_token_count',   # Google PaLM / older Gemini.
    'cache_tokens',                 # Other variants.
)

_NESTED_DETAIL_FIELDS = (
    'prompt_tokens_details',        # Official OpenAI.
    'details',                      # Generic.
    'token_details',                # Generic.
    'prompt_details',               # Generic.
)

def _extract_cached_tokens(usage_dict: dict) -> int:
    """Extract cached_tokens from the usage dict, compatible with multiple provider formats.

    Known formats:
    1. official OpenAI: usage.prompt_tokens_details.cached_tokens
    2. StepFun: usage.cached_tokens (top level)
    3. Gemini/others: possibly in nested structures
    """
    # 1) Check nested structures (e.g. OpenAI prompt_tokens_details.cached_tokens).
    for nested_key in _NESTED_DETAIL_FIELDS:
        nested = usage_dict.get(nested_key)
        if not nested:
            continue
        # May be a Pydantic object or dict.
        if not isinstance(nested, dict):
            nested = _usage_to_dict(nested)
        for field in _CACHED_TOKEN_FIELDS:
            val = nested.get(field)
            if val:
                return int(val)

    # 2) Top-level cached_tokens (e.g. StepFun).
    for field in _CACHED_TOKEN_FIELDS:
        val = usage_dict.get(field)
        if val:
            return int(val)

    return 0

def calculate_cache_hit_rate(prompt_tokens: int, cached_tokens: int) -> float:
    """Compute the cache hit rate.

    Args:
        prompt_tokens: total prompt tokens (cache hits and misses included)
        cached_tokens: cache-hit tokens

    Returns:
        Cache hit rate in the range 0.0 ~ 1.0
        Returns 0.0 when prompt_tokens is 0

    Example:
        >>> calculate_cache_hit_rate(2911, 2888)
        0.9920989350738585
    """
    if prompt_tokens <= 0:
        return 0.0
    cached_tokens = max(0, min(cached_tokens, prompt_tokens))
    return cached_tokens / prompt_tokens

def _record_usage_from_response(response, call_type: str):
    """Extract usage from an OpenAI SDK response and record it.

    Extracted fields:
    - usage.prompt_tokens: total prompt tokens (including cached)
    - usage.completion_tokens: generated tokens
    - usage.total_tokens: total
    - usage.prompt_tokens_details.cached_tokens: the cache-hit part of the prompt
    """
    try:
        if not hasattr(response, 'usage') or response.usage is None:
            return
        usage = response.usage
        model = getattr(response, 'model', None) or "unknown"

        # Convert usage to dict for consistent lookup (supports Pydantic v1/v2 and native dict).
        usage_dict = _usage_to_dict(usage)

        # Debug: record the full usage structure.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Token tracker: usage for model={model}: {usage_dict}")

        cached_tokens = _extract_cached_tokens(usage_dict)

        TokenTracker.get_instance().record(
            model=model,
            prompt_tokens=usage_dict.get('prompt_tokens', 0) or 0,
            completion_tokens=usage_dict.get('completion_tokens', 0) or 0,
            total_tokens=usage_dict.get('total_tokens', 0) or 0,
            cached_tokens=cached_tokens,
            call_type=call_type,
        )
    except Exception:
        # Token accounting is best-effort and must not affect SDK responses.
        pass

def record_anthropic_usage(model: str, usage, call_type: str | None = None):
    """Record usage returned by Anthropic Messages API calls.

    Anthropic reports ``input_tokens`` / ``output_tokens`` instead of the
    OpenAI SDK's ``prompt_tokens`` / ``completion_tokens`` names, so it cannot
    be observed by the OpenAI monkey-patch above.
    """
    try:
        usage_dict = _usage_to_dict(usage)
        if not usage_dict:
            return
        if 'input_tokens' in usage_dict:
            prompt_tokens = sum(
                int(usage_dict.get(field) or 0)
                for field in (
                    'input_tokens',
                    'cache_creation_input_tokens',
                    'cache_read_input_tokens',
                )
            )
        else:
            prompt_tokens = int(usage_dict.get('prompt_tokens') or 0)
        completion_tokens = int(usage_dict.get('output_tokens') or usage_dict.get('completion_tokens') or 0)
        total_tokens = int(usage_dict.get('total_tokens') or (prompt_tokens + completion_tokens))
        cached_tokens = _extract_cached_tokens(usage_dict)
        TokenTracker.get_instance().record(
            model=model or "unknown",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            call_type=call_type or _current_call_type.get('unknown'),
        )
    except Exception:
        # Anthropic usage accounting is intentionally non-fatal.
        pass

def _should_inject_stream_options(base_url: str) -> bool:
    """Check whether this base_url is in the blocklist."""
    if not base_url:
        return True
    with _blocklist_lock:
        return base_url not in _stream_options_blocklist

def _add_to_blocklist(base_url: str):
    """Add a base_url that doesn't support stream_options to the blocklist."""
    if base_url:
        with _blocklist_lock:
            _stream_options_blocklist.add(base_url)
        logger.info(f"Token tracker: added base_url to stream_options blocklist: {base_url[:60]}...")

def _install_crash_excepthook():
    """Install a global sys.excepthook that turns unhandled exceptions into crash events.

    Chain pattern: keeps the original hook (the system default prints the traceback to
    stderr), only prepending a telemetry layer. Existing logging / error display logic
    is untouched; we just take a note in passing.

    Idempotent: multiple installs take effect once (avoiding nested chains when both
    main_server and memory_server import this).
    """
    import sys
    if getattr(sys, "_neko_crash_hook_installed", False):
        return
    _orig_excepthook = sys.excepthook

    def _crash_excepthook(exc_type, exc_value, exc_tb):
        try:
            # KeyboardInterrupt is intentional user Ctrl-C, not a crash.
            if not issubclass(exc_type, KeyboardInterrupt):
                import traceback as _tb
                import hashlib as _hl
                from utils.instrument import event as _e, counter as _c
                tb_text = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
                # traceback_hash is a 12-character digest: sufficient to deduplicate crashes with the same origin,
                # without reconstructing the stack (privacy). Dashboard only needs the most frequent hashes.
                tb_hash = _hl.sha256(tb_text.encode("utf-8", errors="replace")).hexdigest()[:12]
                _e("crash", error_class=exc_type.__name__, traceback_hash=tb_hash)
                _c("crash", error_class=exc_type.__name__)
                # Force event_logger flush: the process may die immediately afterward, losing unflushed events.
                # Flush itself catches exceptions and does not raise.
                from utils.event_logger import EventLogger
                EventLogger.get_instance().flush()
        except Exception:
            # The crash hook must never raise, or its exception replaces the original traceback,
            # hiding the actual crash. Telemetry failure is insignificant by comparison.
            pass
        # Let the default hook print the stack, preserving existing behavior.
        try:
            _orig_excepthook(exc_type, exc_value, exc_tb)
        except Exception:
            # If the original hook itself crashes (rare, e.g. sys.stderr already closed),
            # little can be done beyond letting the process exit; the original traceback is already lost.
            pass

    sys.excepthook = _crash_excepthook
    sys._neko_crash_hook_installed = True
    logger.info("Token tracker: crash excepthook installed")

def install_hooks():
    """
    Install the OpenAI SDK monkey-patch, automatically tracking token usage of all chat.completions.create calls.
    Also covers LangChain's underlying calls (LangChain ChatOpenAI calls the OpenAI SDK underneath).

    Along the way: installs sys.excepthook to catch unhandled exceptions as crash events.

    Idempotent: merged single-process mode (packaged / Steam edition, see the launcher's
    _run_merged) runs the main / memory / agent uvicorn apps in one process; all three
    apps' startup calls this function, patching the same process-level
    ``Completions.create``. Without the guard the wrappers would stack — every
    chat.completions call gets recorded multiple times, and hook-based call_types like
    conversation / emotion / proactive / galgame_options inflate exactly N-fold in
    telemetry (×3 measured live on the three-app Steam edition). tts /
    conversation_realtime / agent_cua, which book directly via ``TokenTracker.record()``,
    bypass the hook and are unaffected; app_start is held by the
    ``_has_recorded_app_start`` singleton lock — this guard is its dual on the hook side.
    """
    # Install the crash hook independently of openai, idempotently.
    _install_crash_excepthook()

    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
    except ImportError:
        logger.warning("Token tracker: openai package not found, hooks not installed")
        return

    # Return immediately if installed (cheap path), avoiding stacked wrappers. Actual installation uses double-checked locking below.
    if getattr(Completions.create, "_neko_token_tracker_hooked", False):
        return

    _original_create = Completions.create
    _original_async_create = AsyncCompletions.create

    @functools.wraps(_original_create)
    def patched_create(self, *args, **kwargs):
        if _is_plugin_model_gateway(_get_base_url(self)):
            return _original_create(self, *args, **kwargs)
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return _handle_sync_stream(self, _original_create, args, kwargs, call_type)

        try:
            result = _original_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    @functools.wraps(_original_async_create)
    async def patched_async_create(self, *args, **kwargs):
        if _is_plugin_model_gateway(_get_base_url(self)):
            return await _original_async_create(self, *args, **kwargs)
        call_type = _current_call_type.get('unknown')
        is_stream = kwargs.get('stream', False)

        if is_stream:
            return await _handle_async_stream(self, _original_async_create, args, kwargs, call_type)

        try:
            result = await _original_async_create(self, *args, **kwargs)
            _record_usage_from_response(result, call_type)
            return result
        except Exception as e:
            TokenTracker.get_instance().record(
                model=kwargs.get('model', 'unknown'),
                prompt_tokens=0, completion_tokens=0, total_tokens=0,
                call_type=call_type, success=False,
            )
            raise

    # Mark wrappers so the idempotence guard recognizes installation. functools.wraps does not copy this custom attribute,
    # so only our wrapped methods carry it, never the original SDK methods.
    patched_create._neko_token_tracker_hooked = True
    patched_async_create._neko_token_tracker_hooked = True

    # Double-checked locking: merged mode's three startup coroutines run serially on one event loop, handled by
    # the cheap path. The lock protects multithreaded initialization (agent / memory watchdog threads), ensuring
    # check-installed → assign cannot interleave into stacked installation.
    with _hooks_install_lock:
        if getattr(Completions.create, "_neko_token_tracker_hooked", False):
            return
        Completions.create = patched_create
        AsyncCompletions.create = patched_async_create
    logger.info("Token tracker: OpenAI SDK hooks installed")

def _handle_sync_stream(self_obj, original_fn, args, kwargs, call_type):
    """Handle sync streaming calls: inject stream_options + wrap Stream."""
    base_url = _get_base_url(self_obj)
    injected = False

    # Try injecting stream_options.
    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = original_fn(self_obj, *args, **kwargs)
        return _SyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            # If stream_options causes an error, remove it and retry.
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = original_fn(self_obj, *args, **kwargs)
                return _SyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise

async def _handle_async_stream(self_obj, original_fn, args, kwargs, call_type):
    """Handle async streaming calls: inject stream_options + wrap AsyncStream."""
    base_url = _get_base_url(self_obj)
    injected = False

    if _should_inject_stream_options(base_url) and 'stream_options' not in kwargs:
        kwargs['stream_options'] = {"include_usage": True}
        injected = True

    try:
        result = await original_fn(self_obj, *args, **kwargs)
        return _AsyncStreamWrapper(result, call_type)
    except Exception as e:
        if injected:
            _add_to_blocklist(base_url)
            kwargs.pop('stream_options', None)
            try:
                result = await original_fn(self_obj, *args, **kwargs)
                return _AsyncStreamWrapper(result, call_type)
            except Exception:
                TokenTracker.get_instance().record(
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                    call_type=call_type, success=False,
                )
                raise
        TokenTracker.get_instance().record(
            model=kwargs.get('model', 'unknown'),
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            call_type=call_type, success=False,
        )
        raise

class _SyncStreamWrapper:
    """Wrap a sync Stream, extracting usage after iteration completes.

    Key point: record only once after the stream ends (taking the last chunk carrying
    usage). Some OpenAI-compatible APIs (StepFun, Qwen, etc.) return cumulative usage
    in every chunk; recording every chunk would cause severe double counting.
    """

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __iter__(self):
        last_usage_chunk = None
        for chunk in self._stream:
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                last_usage_chunk = chunk
            yield chunk
        # After the stream ends, record only the final chunk containing usage.
        if last_usage_chunk is not None:
            _record_usage_from_response(last_usage_chunk, self._call_type)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __enter__(self):
        if hasattr(self._stream, '__enter__'):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        if hasattr(self._stream, '__exit__'):
            return self._stream.__exit__(*args)
        return None

class _AsyncStreamWrapper:
    """Wrap an async AsyncStream, extracting usage after iteration completes.

    Same as _SyncStreamWrapper: record only once after the stream ends.
    """

    def __init__(self, stream, call_type: str):
        self._stream = stream
        self._call_type = call_type

    def __aiter__(self):
        return self._aiter_and_track()

    async def _aiter_and_track(self):
        last_usage_chunk = None
        async for chunk in self._stream:
            if hasattr(chunk, 'usage') and chunk.usage is not None:
                last_usage_chunk = chunk
            yield chunk
        # After the stream ends, record only the final chunk containing usage.
        if last_usage_chunk is not None:
            _record_usage_from_response(last_usage_chunk, self._call_type)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        if hasattr(self._stream, '__aenter__'):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        if hasattr(self._stream, '__aexit__'):
            return await self._stream.__aexit__(*args)
        return None
