from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, topic_units


def test_topic_units_empty_stop_chars_disables_default_filtering():
    stopped_char = next(iter(ZH_TOPIC_STOP_CHARS))

    assert stopped_char not in topic_units(stopped_char)
    assert stopped_char in topic_units(stopped_char, stop_chars=set())
