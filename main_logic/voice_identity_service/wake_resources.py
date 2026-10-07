"""Resolve deployment preference into the neutral wake capability snapshot."""

from config.voice_wake_word import wake_word_model_dir, wake_word_preference
from main_logic.voice_input.wake_word.errors import WakeWordFailureReason
from main_logic.voice_input.wake_word.resources import WakeWordResources
from .wake_word_bundle import WakeWordBundleError, resolve_cached_model_dir


def resolve_wake_word_resources() -> WakeWordResources:
    """Call on a worker thread. Discovery never enables the capability."""
    preference = wake_word_preference()
    if preference["reason"]:
        return WakeWordResources(True, reason=WakeWordFailureReason.PREFERENCE_UNAVAILABLE.value)
    if not preference["enabled"]:
        return WakeWordResources(False)
    explicit = wake_word_model_dir()
    if explicit:
        return WakeWordResources(True, explicit)
    try:
        cached = resolve_cached_model_dir()
    except WakeWordBundleError:
        return WakeWordResources(True, reason=WakeWordFailureReason.MODEL_INVALID.value)
    return (WakeWordResources(True, str(cached)) if cached is not None else
            WakeWordResources(True, reason=WakeWordFailureReason.MODEL_MISSING.value))
