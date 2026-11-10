"""Music-only guidance for the plugin assessment channel."""

MUSIC_QUERY_GUIDANCE = (
    "Music query fields are an exception to copying the user's message verbatim: "
    "extract only the song title and requested artist, preserving version qualifiers. "
    "Respect an explicit song count; use 6-8 only when no count is specified. "
    "Never select local-save actions without an explicit local-storage request."
)
