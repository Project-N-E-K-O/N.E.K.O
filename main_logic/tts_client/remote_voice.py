"""Map an imported library reference at the TTS worker boundary."""

from functools import partial
from contextvars import ContextVar

from utils.config_manager import get_config_manager

from ._infra import _enqueue_error

active_imported_voice = ContextVar("active_imported_voice", default=None)


def unavailable_imported_voice_worker(request_queue, response_queue, api_key, voice_id):
    """Fail startup through the existing terminal configuration-error channel."""
    _enqueue_error(response_queue, {
        "code": "TTS_CONFIG_INVALID",
        "reason": "IMPORTED_VOICE_UNAVAILABLE",
    })
    response_queue.put(("__ready__", False))


def run_imported_voice_worker(
    request_queue, response_queue, api_key, voice_id, *, worker, local_ref,
    remote_voice_id, scope_id,
):
    """Recheck ownership at startup, then pass only the remote ID upstream."""
    try:
        current = get_config_manager().get_imported_voice(local_ref)
    except Exception:
        current = None
    if not current or (
        current.get("scope_id") != scope_id
        or current.get("remote_voice_id") != remote_voice_id
    ):
        return unavailable_imported_voice_worker(
            request_queue, response_queue, api_key, voice_id,
        )
    token = active_imported_voice.set(current)
    try:
        return worker(request_queue, response_queue, api_key, remote_voice_id)
    finally:
        active_imported_voice.reset(token)


def bind_imported_voice_worker(worker, local_ref, metadata):
    return partial(
        run_imported_voice_worker,
        worker=worker,
        local_ref=local_ref,
        remote_voice_id=metadata["remote_voice_id"],
        scope_id=metadata["scope_id"],
    )
