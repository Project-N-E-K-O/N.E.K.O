from utils.source_locale import source_region_from_locale


def test_source_locale_prefers_mainland_chinese_sources():
    assert source_region_from_locale("zh-CN") == "china"
    assert source_region_from_locale("zh-Hans") == "china"
    assert source_region_from_locale("cn") == "china"


def test_source_locale_does_not_treat_all_chinese_locales_as_mainland():
    assert source_region_from_locale("zh-TW") == "non-china"
    assert source_region_from_locale("zh-HK") == "non-china"


def test_empty_source_locale_keeps_runtime_region_detection():
    assert source_region_from_locale(None) is None
    assert source_region_from_locale("") is None
