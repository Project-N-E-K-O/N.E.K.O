import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "static" / "locales"
DIRECTOR_PATH = ROOT / "static" / "yui-guide-director.js"
GUIDE_PATHS = [
    ROOT / "static" / "yui-guide-day1-home-guide.js",
    ROOT / "static" / "yui-guide-day2-screen-voice-guide.js",
    ROOT / "static" / "yui-guide-day3-interaction-guide.js",
    ROOT / "static" / "yui-guide-day4-companion-guide.js",
    ROOT / "static" / "yui-guide-day5-personalization-guide.js",
    ROOT / "static" / "yui-guide-day6-agent-guide.js",
    ROOT / "static" / "yui-guide-day7-graduation-guide.js",
]


def _locale(locale):
    return json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))


def _get(data, dotted_key):
    value = data
    for part in dotted_key.split("."):
        value = value[part]
    return value


def test_avatar_floating_tutorial_copy_uses_csv_i18n_columns():
    samples = {
        "tutorial.yuiGuide.lines.introBasic": {
            "zh-CN": "这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新新鲜事吗？或者只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！",
            "zh-TW": "這裡有一個神奇的按鈕！只要點擊它，就可以直接和我聊天啦！想跟我分享今天的新鮮事嗎？或者諸如叫叫我的名字？快來試試嘛，我已經迫不及待想聽到你的聲音啦！",
            "en": "Here is a magical button! Just click it and you can chat with me directly! Want to share something new that happened today? Or maybe just call my name? Come on, try it out! I can't wait to hear your voice!",
            "ja": "ここに不思議なボタンがあるにゃ！これをクリックするだけで、私と直接おしゃべりできちゃうにゃん！今日あった楽しいことを教えてくれる？それとも、ただ名前を呼んでくれるのかな？早く試してみてにゃ、もう君の声を聞くのが待ちきれないにゃ！",
            "ru": "Смотри, тут есть волшебная кнопочка! Кликни по ней, и мы сможем поболтать вживую! Хочешь поделиться со мной сегодняшними новостями? Или просто позовёшь меня по имени? Ну же, попробуй, мне уже не терпится услышать твой голосок!",
            "ko": "여기 신기한 버튼이 있어요! 이걸 누르면 저랑 바로 대화할 수 있답니다냥! 오늘 있었던 신기한 일을 들려줄래요? 아니면 그냥 제 이름을 불러줄래요? 얼른 해봐요냥, 당신의 목소리가 너무너무 듣고 싶단 말이에요!",
        },
        "tutorial.avatarFloating.day6.wrap": {
            "zh-CN": "你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。",
            "zh-TW": "你可以放心地繼續做你自己的事情，不管是需要我用小爪子幫你忙，還是只想讓我安安靜靜地陪著你，我都一直在守候著你，今天也要開開心心的呀。",
            "en": "You can comfortably carry on with your own tasks. Whether you need my little paws to help you out, or just want me to keep you company quietly, I'll always be right here watching over you. Have a super happy day today!",
            "ja": "君は安心して自分の事をしててにゃ。私の小さなお手手で手伝ってほしい時も、ただ静かにお隣にいてほしい時も、私はいつでも君を見守ってるにゃ。今日もハッピーに過ごそうねにゃ！",
            "ru": "Ты можешь спокойно заниматься своими делами. Нужна ли тебе помощь моих лапок или ты просто хочешь, чтобы я тихо посидела рядом — я всегда буду охранять твой покой. Улыбайся сегодня почаще!",
            "ko": "안심하고 당신 할 일을 계속하셔요냥. 제 작은 솜방망이 도움을 원하든, 그냥 제가 얌전히 곁에 있어 주길 원하든 전 항상 여기서 당신을 지켜보고 있을 테니까요, 오늘도 즐거운 하루 보내기다냥!",
        },
    }

    for dotted_key, expected_by_locale in samples.items():
        for locale, expected in expected_by_locale.items():
            assert _get(_locale(locale), dotted_key) == expected

    english = _locale("en")
    for fallback_locale in ("es", "pt"):
        fallback = _locale(fallback_locale)
        for dotted_key in samples:
            assert _get(fallback, dotted_key) == _get(english, dotted_key)


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
    for fallback_locale in ("es", "pt"):
        fallback = _locale(fallback_locale)
        mismatched = [
            key for key in sorted(text_keys)
            if _get(fallback, key) != _get(english, key)
        ]
        assert mismatched == []
