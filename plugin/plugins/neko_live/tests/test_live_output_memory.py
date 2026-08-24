from plugin.plugins.neko_live.core.live_output_memory import coerce_recent_reply_values


def test_single_recent_reply_string_is_not_split_into_characters():
    assert coerce_recent_reply_values("hello room") == ["hello room"]
