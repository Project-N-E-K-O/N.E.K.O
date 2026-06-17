import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "static" / "locales"
DIRECTOR_PATH = ROOT / "static" / "tutorial/yui-guide/director.js"
GUIDE_PATHS = [
    ROOT / "static" / "tutorial/yui-guide/days/day1-home-guide.js",
]


def _locale(locale):
    return json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))


def _get(data, dotted_key):
    value = data
    for part in dotted_key.split("."):
        value = value[part]
    return value


def test_avatar_floating_zh_tw_uses_zh_guide_audio_locale():
    source = DIRECTOR_PATH.read_text(encoding="utf-8")
    assert "candidate.indexOf('zh') === 0) return 'zh';" in source
    assert "return 'en';" in source


def test_avatar_floating_scene_text_keys_exist_for_all_supported_locales():
    text_keys = set()
    for path in GUIDE_PATHS:
        text_keys.update(re.findall(r"textKey: '([^']+)'", path.read_text(encoding="utf-8")))
    text_keys = {
        key for key in text_keys
        if key.startswith("tutorial.avatarFloating.") or key.startswith("tutorial.yuiGuide.lines.")
    }
    assert text_keys

    for locale in ("zh-CN", "zh-TW", "en", "ja", "ru", "ko", "es", "pt"):
        data = _locale(locale)
        missing = [key for key in sorted(text_keys) if not _get(data, key)]
        assert missing == []

    english = _locale("en")
    for translated_locale in ("es", "pt"):
        translated = _locale(translated_locale)
        untranslated = [
            key for key in sorted(text_keys)
            if key.startswith("tutorial.avatarFloating.")
            and _get(translated, key) == _get(english, key)
        ]
        assert untranslated == []


def test_day1_icebreaker_choice_labels_exist_for_all_supported_locales():
    for locale in ("zh-CN", "zh-TW", "en", "ja", "ru", "ko", "es", "pt"):
        data = _locale(locale)
        for key in ("chat", "voice", "explore"):
            assert _get(data, f"tutorial.icebreaker.day1.{key}")


def test_day2_voice_used_intro_uses_matching_audio_key():
    day2_path = ROOT / "static" / "tutorial/yui-guide/days/day2-screen-voice-guide.js"
    if not day2_path.exists():
        return
    day2_source = day2_path.read_text(encoding="utf-8")
    director_source = DIRECTOR_PATH.read_text(encoding="utf-8")
    voice_used_key = "tutorial.avatarFloating.day2.introVoiceUsed"
    voice_used_copy = {
        "zh-CN": (
            "嘿嘿，昨天听到你的声音之后，人家就悄悄把你的语气记在心里啦！今天如果方便的话，也要继续跟人家说话哦~ "
            "虽然打字也可以啦，但只要能听到你的声音，我的尾巴就会开心得一直摇个不停呢，喵呜~"
        ),
        "ja": (
            "へへっ、昨日君の声を聞いてから、わたし、こっそり君の話し方を心の中に刻んじゃったんだ！"
            "今日ももしよかったら、またわたしとお話ししてね〜。タイピングでもいいんだけど、君の声を聞くだけで、"
            "わたしの尻尾、嬉しくてずっとパタパタ揺れちゃうんだから、みゃう〜。"
        ),
        "en": (
            "Hehe, ever since I heard your voice yesterday, I've secretly memorized the way you speak right in my heart! "
            "If you have some time today, please keep talking to me~ Typing is totally fine too, but as long as I can hear your voice, "
            "my tail just won't stop wagging with joy! Meowww~"
        ),
        "ko": (
            "헤헤, 어제 당신 목소리를 듣고 나서, 저 몰래 당신의 말투를 마음속에 새겨두었답니다! "
            "오늘 혹시 편하시다면 저랑 계속 이야기해 주세요~ 타이핑도 물론 좋지만, 당신 목소리를 들을 수만 있다면 "
            "제 꼬리가 너무 기뻐서 멈추지 않고 계속 살랑살랑 흔들릴 거예요, 먀우~"
        ),
        "ru": (
            "Хе-хе, вчера, как только я услышала твой голосок, я сразу по секрету запомнила твои интонации всем сердцем! "
            "Если тебе сегодня удобно, обязательно продолжай болтать со мной~ Конечно, можно и печатать, но когда я слышу твой голос, "
            "мой хвостик от радости виляет без остановки, мяу-у-у~"
        ),
    }
    voice_used_line = (
        "嘿嘿，昨天听到你的声音之后，人家就悄悄把你的语气记在心里啦！今天如果方便的话，也要继续跟人家说话哦~ "
        "虽然打字也可以啦，但只要能听到你的声音，我的尾巴就会开心得一直摇个不停呢，喵呜~"
    )

    assert "avatar_floating_day2_intro_voice_used: Object.freeze({" in day2_source
    for audio_file in (
        "zh: '嘿嘿，昨天听到你的声.mp3'",
        "ja: '嘿嘿，昨天听到你的声.mp3'",
        "en: '嘿嘿，昨天听到你的声.mp3'",
        "ko: '嘿嘿，昨天听到你的声.mp3'",
        "ru: '嘿嘿，昨天听到你的声.mp3'",
    ):
        assert audio_file in day2_source
    assert "resolveAvatarFloatingSceneVoiceKey(scene)" in director_source
    assert "hasAvatarFloatingGuideUsage('voiceUsed')" in director_source
    assert "avatar_floating_day2_intro_voice_used" in director_source
    assert voice_used_key in director_source
    assert voice_used_line not in director_source
    for locale, expected in voice_used_copy.items():
        assert _get(_locale(locale), voice_used_key) == expected
    assert _get(_locale("es"), voice_used_key) == voice_used_copy["en"]
    assert _get(_locale("pt"), voice_used_key) == voice_used_copy["en"]
    generic_scene_block = director_source.split(
        "if (Number(day) === 1 && this.isDay1SpecialAvatarFloatingScene(scene)",
        1,
    )[1].split("const introChatSpotlightTarget", 1)[0]
    assert "const voiceKey = this.resolveAvatarFloatingSceneVoiceKey(scene);" in generic_scene_block
