from main_logic.topic.common import ZH_TOPIC_STOP_CHARS, topic_units


def test_topic_units_empty_stop_chars_disables_default_filtering():
    stopped_char = next(iter(ZH_TOPIC_STOP_CHARS))

    assert stopped_char not in topic_units(stopped_char)
    assert stopped_char in topic_units(stopped_char, stop_chars=set())


def test_topic_units_tokenizes_supported_non_latin_scripts():
    units = topic_units("한국어 검색어 日本語カナ テーマ русский запрос", include_cjk_bigrams=False)

    assert "한국어" in units
    assert "검색어" in units
    assert "カナ" in units
    assert "テーマ" in units
    assert "русский" in units
    assert "запрос" in units
