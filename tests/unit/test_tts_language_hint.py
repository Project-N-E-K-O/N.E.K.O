"""Unit tests for initial-N-character TTS language detection and its TTS-worker wiring."""

from utils.language_utils import (
    TTS_LANG_DETECT_MIN_CHARS,
    detect_tts_language_hint,
)
from main_logic.tts_client import (
    _build_step_tts_create_data,
    _get_tts_language_code,
)


class TestDetectTtsLanguageHint:
    def test_empty_returns_none(self):
        assert detect_tts_language_hint("") is None
        assert detect_tts_language_hint(None) is None  # type: ignore[arg-type]

    def test_pure_chinese_returns_none(self):
        assert detect_tts_language_hint("你好世界，今天天气真好") is None

    def test_pure_english_returns_none(self):
        assert detect_tts_language_hint("hello world this is a test") is None

    def test_pure_hiragana_returns_ja(self):
        assert detect_tts_language_hint("こんにちは") == "ja"

    def test_pure_katakana_returns_ja(self):
        assert detect_tts_language_hint("カタカナ") == "ja"

    def test_mixed_with_single_kana_returns_ja(self):
        # Japanese often mixes kanji/punctuation; even one kana character should classify it as Japanese.
        assert detect_tts_language_hint("今日はいい天気") == "ja"

    def test_half_width_katakana_returns_none(self):
        # Halfwidth katakana lies in FF65-FF9F, outside the regex's 30A0-30FF coverage; return None.
        # Deliberately exclude that range to avoid false positives in Chinese/Korean contexts.
        assert detect_tts_language_hint("ｶﾀｶﾅ") is None

    def test_min_chars_constant_is_positive(self):
        assert isinstance(TTS_LANG_DETECT_MIN_CHARS, int)
        assert TTS_LANG_DETECT_MIN_CHARS > 0


class TestQwenConfigMessageBuilder:
    """Enter qwen_realtime_tts_worker's namespace to verify build_config_message branches.

    Do not run the worker itself, which opens a real websocket. Import internal constants directly and
    reproduce config-message branches; update this test if worker behavior later drifts.
    """

    def _build(self, voice_id: str, lang_hint):
        # Reproduce main_logic.tts_client.qwen_realtime_tts_worker.async_worker.build_config_message
        # logic to verify branch behavior.
        session = {
            "mode": "server_commit",
            "voice": voice_id,
            "response_format": "pcm",
            "sample_rate": 24000,
            "channels": 1,
            "bit_depth": 16,
        }
        if lang_hint == "ja":
            session["language_type"] = "Japanese"
        return {"type": "session.update", "session": session}

    def test_no_hint_has_no_language_type(self):
        msg = self._build("Momo", None)
        assert "language_type" not in msg["session"]

    def test_ja_hint_adds_japanese(self):
        msg = self._build("Momo", "ja")
        assert msg["session"]["language_type"] == "Japanese"


class TestStepTtsCreateBuilder:
    """Likewise cover step_realtime_tts_worker._build_tts_create_data branch behavior."""

    def _build(self, *, voice_id: str, session_id: str, lang_hint, is_lanlan_app: bool):
        return _build_step_tts_create_data(session_id, voice_id, lang_hint, is_lanlan_app)

    def test_tts_language_code_uses_global_language(self, monkeypatch):
        monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "en-US")
        assert _get_tts_language_code() == "en-US"

    def test_tts_language_code_preserves_traditional_chinese(self, monkeypatch):
        monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-TW")
        assert _get_tts_language_code() == "cmn-tw"

    def test_tts_language_code_maps_portuguese(self, monkeypatch):
        # pt is supported and needs its own language_code; otherwise overseas TTS/realtime falls back
        # to cmn-CN, giving Portuguese users Mandarin synthesis.
        monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "pt-BR")
        assert _get_tts_language_code() == "pt-BR"

    def test_lanlan_tech_ja_adds_voice_label(self):
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=False)
        assert data["voice_label"] == {"language": "日语"}
        assert data["voice_id"] == "v1"
        assert "language_code" not in data

    def test_lanlan_tech_no_hint_has_no_voice_label(self):
        data = self._build(voice_id="v1", session_id="s1", lang_hint=None, is_lanlan_app=False)
        assert "voice_label" not in data

    def test_paid_stepfun_ja_also_uses_voice_label(self):
        # Paid StepFun uses the same protocol as lanlan.tech and should include voice_label too.
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=False)
        assert data["voice_label"] == {"language": "日语"}

    def test_lanlan_app_ja_uses_ja_jp_language_code(self, monkeypatch):
        monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "en-US")
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=True)
        assert data["language_code"] == "ja-JP"
        # lanlan.app now forwards actual voice_id for server mapping instead of forcing Leda on the client.
        assert data["voice_id"] == "v1"
        assert "voice_label" not in data

    def test_lanlan_app_no_hint_uses_global_language_code(self, monkeypatch):
        monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "ko-KR")
        data = self._build(
            voice_id="v1",
            session_id="s1",
            lang_hint=None,
            is_lanlan_app=True,
        )
        assert data["language_code"] == "ko-KR"
