"""Music-only guidance for the plugin assessment channel."""

MUSIC_QUERY_GUIDANCE = (
    "For music playback/search entries, construct query fields from the requested "
    "song title and artist in the dialogue, preserving version qualifiers. "
    "This music guidance applies only to music entry arguments; other plugins "
    "follow their own schemas, including searches for lyrics or their meaning. "
    "Respect an explicit song count; use 6-8 only when no count is specified. "
    "Never select local-save actions without an explicit local-storage request."
)
