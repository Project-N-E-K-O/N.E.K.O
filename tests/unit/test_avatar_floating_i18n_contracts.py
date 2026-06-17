import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / "static" / "locales"
DIRECTOR_PATH = ROOT / "static" / "tutorial/yui-guide/director.js"
GUIDE_PATHS = [
    ROOT / "static" / "tutorial/yui-guide/days/day1-home-guide.js",
    ROOT / "static" / "tutorial/yui-guide/days/day2-screen-voice-guide.js",
    ROOT / "static" / "tutorial/yui-guide/days/day3-interaction-guide.js",
    ROOT / "static" / "tutorial/yui-guide/days/day4-companion-guide.js",
    ROOT / "static" / "tutorial/yui-guide/days/day5-personalization-guide.js",
    ROOT / "static" / "tutorial/yui-guide/days/day6-agent-guide.js",
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
            "zh-CN": "这里有一个神奇的按钮！只要点击它，就可以直接和我聊天啦！想跟我分享今天的新鲜事吗？或者就只是叫叫我的名字？快来试试嘛，我已经迫不及待想听到你的声音啦！",
            "zh-TW": "這裡有一個神奇的按鈕！只要點擊它，就可以直接和我聊天啦！想跟我分享今天的新鮮事嗎？或者就只是叫叫我的名字？快來試試嘛，我已經迫不及待想聽到你的聲音啦！",
            "en": "Here is a magical button! Just click it and you can chat with me directly! Want to share something new that happened today? Or maybe just say my name? Come on, try it out! I can't wait to hear your voice!",
            "ja": "ここに不思議なボタンがあるよ！これをクリックするだけで、私と直接おしゃべりできちゃうんだ。今日あった楽しいことを教えてくれる？それとも、ただ私の名前を呼んでくれるだけでもいいよ？早く試してみて、もう君の声を聞くのが待ちきれないよ！",
            "ru": "Смотри, тут есть волшебная кнопочка! Кликни по ней, и мы сможем поболтать вживую! Хочешь поделиться со мной сегодняшними новостями? Или просто произнесёшь моё имя? Ну же, попробуй, мне уже не терпится услышать твой голосок!",
            "ko": "여기 신기한 버튼이 있어요! 이걸 누르면 저랑 바로 대화할 수 있답니다! 오늘 있었던 신기한 일을 들려줄래요? 아니면 그냥 제 이름만 불러줘도 좋아요. 얼른 해봐요, 당신의 목소리가 너무너무 듣고 싶단 말이에요!",
        },
        "tutorial.avatarFloating.day6.wrap": {
            "zh-CN": "你可以放心地继续做你自己的事情，不管是需要我用小爪子帮你忙，还是只想让我安安静静地陪着你，我都一直在守候着你，今天也要开开心心的呀。",
            "zh-TW": "你可以放心地繼續做你自己的事情，不管是需要我用小爪子幫你忙，還是只想讓我安安靜靜地陪著你，我都一直在守候著你，今天也要開開心心的呀。",
            "en": "You can comfortably carry on with your own tasks. Whether you need my little paws to help you out, or just want me to keep you company quietly, I'll always be right here watching over you. Have a super happy day today!",
            "ja": "君は安心して自分の事をしててね。私の小さなお手手で手伝ってほしい時も、ただ静かにお隣にいてほしい時も、私はいつでも君を見守ってるよ。今日もハッピーに過ごそうね！",
            "ru": "Ты можешь спокойно заниматься своими делами. Нужна ли тебе помощь моих лапок или ты просто хочешь, чтобы я тихо посидела рядом — я всегда буду охранять твой покой. Улыбайся сегодня почаще!",
            "ko": "안심하고 당신 할 일을 계속하셔요. 제 작은 솜방망이 도움을 원하든, 그냥 제가 얌전히 곁에 있어 주길 원하든 전 항상 여기서 당신을 지켜보고 있을 테니까요, 오늘도 즐거운 하루 보내기예요!",
        },
    }

    for dotted_key, expected_by_locale in samples.items():
        for locale, expected in expected_by_locale.items():
            assert _get(_locale(locale), dotted_key) == expected


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


def test_avatar_floating_reset_toast_keys_exist_for_all_supported_locales():
    for locale in ("zh-CN", "zh-TW", "en", "ja", "ru", "ko", "es", "pt"):
        data = _locale(locale)
        assert _get(data, "tutorial.reset.daySuccess")
        assert _get(data, "tutorial.reset.dayFailed")


def test_day2_voice_used_intro_uses_matching_audio_key():
    day2_source = (ROOT / "static" / "tutorial/yui-guide/days/day2-screen-voice-guide.js").read_text(encoding="utf-8")
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
    assert _get(_locale("es"), voice_used_key) != voice_used_copy["en"]
    assert _get(_locale("pt"), voice_used_key) != voice_used_copy["en"]
    assert "return 'avatar_floating_day2_intro_voice_used';" in director_source
