import pytest
from main_logic.watch_together.engine import Engine
from main_logic.watch_together.engine import normalize_events, parse_video_url, danmaku_hotspots, hotspot_frame_times


@pytest.mark.parametrize("language,laugh", [("en", "Hehe, haha!"), ("ja", "ふふ、あはは！"), ("zh-TW", "捏嘿嘿，哈哈！")])
def test_character_language_selects_laughter_without_changing_legacy_baseline(tmp_path, language, laugh):
    engine = Engine(tmp_path, None, "cat", language=language)
    assert engine.language == language
    assert engine.laugh_text == laugh


def test_links_reject_unrelated_hosts_and_keep_page():
    assert parse_video_url("https://www.bilibili.com/video/BV1GJ411x7h7?p=2") == ("BV1GJ411x7h7", 1)
    for url in ("https://evil.test/video/BV1GJ411x7h7", "http://127.0.0.1/video/BV1GJ411x7h7", "https://www.bilibili.com/video/BV1GJ411x7h7?p=0"):
        with pytest.raises(ValueError):
            parse_video_url(url)


def test_rejects_spoilers_nan_weak_evidence_and_dense_cues():
    def cue(at, **kwargs):
        return {"at": at, "evidence_at": at-1, "kind": "laugh", "reason": "画面反转", "confidence": .8, **kwargs}
    actual = normalize_events([cue(10), cue(11), cue(22, evidence_at=25), cue(float('nan')), cue(30, confidence=.2), cue(40)], 60)
    assert [e["at"] for e in actual] == [10, 40]


def test_hotspots_cover_middle_and_deduplicate_spam():
    dm = [{"at": 3, "text": "哈哈"}] * 100 + [{"at": 48, "text": "绷不住了"}, {"at": 49, "text": "还有高手"}, {"at": 81, "text": "坠机了"}]
    spots = danmaku_hotspots(dm, 100)
    assert len(spots) == 3
    assert spots[0]["score"] == 3
    assert spots[1]["at"] == 48.5
    assert 45.5 in hotspot_frame_times(spots, 100)
    assert 49.5 in hotspot_frame_times(spots, 100)


def test_hotspot_frames_stay_inside_video():
    frames = hotspot_frame_times([{"at": .2}, {"at": 9.5}], 10)
    assert all(0 <= t < 9.9 for t in frames)
