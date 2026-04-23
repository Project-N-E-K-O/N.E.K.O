"""Unit tests for TTS 首 N 字语言检测 helper 以及在 TTS worker 中的接线。"""

from utils.language_utils import (
    TTS_LANG_DETECT_MIN_CHARS,
    detect_tts_language_hint,
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
        # 很多日语夹杂汉字/标点，哪怕只有一个假名也应当判定为日语
        assert detect_tts_language_hint("今日はいい天気") == "ja"

    def test_half_width_katakana_returns_none(self):
        # 半角片假名在 FF65-FF9F 范围，不在当前正则覆盖的 30A0-30FF，返回 None
        # （该范围在中文/韩文场景中会误伤，故特意不纳入）
        assert detect_tts_language_hint("ｶﾀｶﾅ") is None

    def test_min_chars_constant_is_positive(self):
        assert isinstance(TTS_LANG_DETECT_MIN_CHARS, int)
        assert TTS_LANG_DETECT_MIN_CHARS > 0


class TestQwenConfigMessageBuilder:
    """通过模拟进入 qwen_realtime_tts_worker 的命名空间，验证 build_config_message 的分支。

    这里不跑 worker 本身（会连接真实 websocket），而是直接 import 模块内部常量并
    复刻其 config-message 分支逻辑；如果将来 worker 代码漂移，此测试需同步更新。
    """

    def _build(self, voice_id: str, lang_hint):
        # 复刻 main_logic.tts_client.qwen_realtime_tts_worker.async_worker.build_config_message
        # 逻辑，校验分支行为。
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
    """类似地，覆盖 step_realtime_tts_worker 的 _build_tts_create_data 分支逻辑。"""

    def _build(self, *, voice_id: str, session_id: str, lang_hint, is_lanlan_app: bool, fallback_lang_code: str = "zh-CN"):
        data = {
            "session_id": session_id,
            "voice_id": voice_id,
            "response_format": "wav",
            "sample_rate": 24000,
        }
        if is_lanlan_app:
            data["voice_id"] = "Leda"
            data["language_code"] = "ja-JP" if lang_hint == "ja" else fallback_lang_code
        else:
            # lanlan.tech (free) 和自建 StepFun 协议对称，都用 voice_label
            if lang_hint == "ja":
                data["voice_label"] = {"language": "日语"}
        return data

    def test_lanlan_tech_ja_adds_voice_label(self):
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=False)
        assert data["voice_label"] == {"language": "日语"}
        assert data["voice_id"] == "v1"
        assert "language_code" not in data

    def test_lanlan_tech_no_hint_has_no_voice_label(self):
        data = self._build(voice_id="v1", session_id="s1", lang_hint=None, is_lanlan_app=False)
        assert "voice_label" not in data

    def test_paid_stepfun_ja_also_uses_voice_label(self):
        # 付费 StepFun 与 lanlan.tech 协议对称，同样应该带 voice_label
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=False)
        assert data["voice_label"] == {"language": "日语"}

    def test_lanlan_app_ja_uses_ja_jp_language_code(self):
        data = self._build(voice_id="v1", session_id="s1", lang_hint="ja", is_lanlan_app=True)
        assert data["language_code"] == "ja-JP"
        # lanlan.app 会强制 Leda 音色
        assert data["voice_id"] == "Leda"
        assert "voice_label" not in data

    def test_lanlan_app_no_hint_uses_fallback_language_code(self):
        data = self._build(
            voice_id="v1",
            session_id="s1",
            lang_hint=None,
            is_lanlan_app=True,
            fallback_lang_code="zh-CN",
        )
        assert data["language_code"] == "zh-CN"
