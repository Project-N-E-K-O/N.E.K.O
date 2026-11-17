"""Music receipt identity checks."""

def music_callback_is_current(manager, callback):
    """Only the request that produced a music receipt may consume it."""
    metadata = callback.get("metadata") or {}
    request_id = metadata.get("music_request_id")
    return not request_id or request_id == (getattr(manager, "_music_request", None) or {}).get("id")


def music_callback_is_deliverable(manager, callback):
    """Ordinary player receipts update read-on-demand state, not the next topic.

    Keep explicit first-play announcements and failures deliverable. The queue
    controller already receives terminal receipts in music_playback, before chat
    delivery; dropping passive success chatter does not affect playlist progress.
    """
    if not music_callback_is_current(manager, callback):
        return False
    metadata = callback.get("metadata") or {}
    return not (
        metadata.get("context_type") == "music_playback"
        and callback.get("delivery_mode") == "passive"
        and metadata.get("state") in {"playing", "paused", "ended", "buffering"}
    )
