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


def test_day3_voice_used_intro_uses_matching_audio_key_after_day_swap():
    day3_source = (ROOT / "static" / "tutorial/yui-guide/days/day3-interaction-guide.js").read_text(encoding="utf-8")
    director_source = DIRECTOR_PATH.read_text(encoding="utf-8")
    default_key = "tutorial.avatarFloating.day3.intro"
    voice_used_key = "tutorial.avatarFloating.day3.introVoiceUsed"
    default_zh_cn = (
        "前两天你一直在噼里啪啦打字，我还没听过你说话呢。今天如果愿意，就轻轻叫我一声吧。"
        "一句就好，让我把文字背后的你也认识一点点。"
    )
    voice_used_copy = {
        "zh-CN": (
            "嘿嘿，前两天听到你的声音之后，人家就悄悄把你的语气记在心里啦！今天如果方便的话，也要继续跟人家说话哦~ "
            "虽然打字也可以啦，但只要能听到你的声音，我的尾巴就会开心得一直摇个不停呢，喵呜~"
        ),
        "ja": (
            "へへっ、この二日間で君の声を聞いてから、わたし、こっそり君の話し方を心の中に刻んじゃったんだ！"
            "今日ももしよかったら、またわたしとお話ししてね〜。タイピングでもいいんだけど、君の声を聞くだけで、"
            "わたしの尻尾、嬉しくてずっとパタパタ揺れちゃうんだから、みゃう〜。"
        ),
        "en": (
            "Hehe, ever since I heard your voice over the past two days, I've secretly memorized the way you speak right in my heart! "
            "If you have some time today, please keep talking to me~ Typing is totally fine too, but as long as I can hear your voice, "
            "my tail just won't stop wagging with joy! Meowww~"
        ),
        "ko": (
            "헤헤, 지난 이틀 동안 당신 목소리를 듣고 나서, 저 몰래 당신의 말투를 마음속에 새겨두었답니다! "
            "오늘 혹시 편하시다면 저랑 계속 이야기해 주세요~ 타이핑도 물론 좋지만, 당신 목소리를 들을 수만 있다면 "
            "제 꼬리가 너무 기뻐서 멈추지 않고 계속 살랑살랑 흔들릴 거예요, 먀우~"
        ),
        "ru": (
            "Хе-хе, с тех пор как за последние два дня я услышала твой голосок, я по секрету запомнила твои интонации всем сердцем! "
            "Если тебе сегодня удобно, обязательно продолжай болтать со мной~ Конечно, можно и печатать, но когда я слышу твой голос, "
            "мой хвостик от радости виляет без остановки, мяу-у-у~"
        ),
    }
    voice_used_line = (
        "嘿嘿，前两天听到你的声音之后，人家就悄悄把你的语气记在心里啦！今天如果方便的话，也要继续跟人家说话哦~ "
        "虽然打字也可以啦，但只要能听到你的声音，我的尾巴就会开心得一直摇个不停呢，喵呜~"
    )

    assert "avatar_floating_day3_intro_voice_used: Object.freeze({" in day3_source
    for audio_file in (
        "zh: '嘿嘿，前两天听到你的.mp3'",
        "ja: '嘿嘿，前两天听到你的.mp3'",
        "en: '嘿嘿，前两天听到你的.mp3'",
        "ko: '嘿嘿，前两天听到你的.mp3'",
        "ru: '嘿嘿，前两天听到你的.mp3'",
    ):
        assert audio_file in day3_source
    assert "resolveAvatarFloatingSceneVoiceKey(scene)" in director_source
    assert "hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart(3)" in director_source
    assert "recordAvatarFloatingGuideRoundEnd(round)" in director_source
    assert "'day' + day + 'StartedAt'" in director_source
    assert "'day' + day + 'EndedAt'" in director_source
    assert "voiceUsedAt" in director_source
    assert "avatar_floating_day3_intro_voice_used" in director_source
    assert voice_used_key in director_source
    assert voice_used_line not in director_source
    assert _get(_locale("zh-CN"), default_key) == default_zh_cn
    assert "昨天你一直在噼里啪啦打字，我还没听过你说话呢。" not in day3_source
    for locale, expected in voice_used_copy.items():
        assert _get(_locale(locale), voice_used_key) == expected
    assert _get(_locale("es"), voice_used_key) != voice_used_copy["en"]
    assert _get(_locale("pt"), voice_used_key) != voice_used_copy["en"]
    assert "return 'avatar_floating_day3_intro_voice_used';" in director_source


def test_day3_voice_used_intro_requires_voice_usage_after_day1_end_before_day3_start():
    director_source = DIRECTOR_PATH.read_text(encoding="utf-8")
    usage_block = director_source.split("function hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart", 1)[1].split(
        "if (!window.__avatarFloatingGuideUsageListenersInstalled)",
        1,
    )[0]
    scene_text_block = director_source.split("resolveAvatarFloatingSceneText(scene) {", 1)[1].split(
        "resolveAvatarFloatingSceneVoiceKey(scene) {",
        1,
    )[0]
    voice_key_block = director_source.split("resolveAvatarFloatingSceneVoiceKey(scene) {", 1)[1].split(
        "resolveAvatarFloatingSceneEmotion(scene) {",
        1,
    )[0]
    emotion_block = director_source.split("resolveAvatarFloatingSceneEmotion(scene) {", 1)[1].split(
        "getAvatarFloatingSceneButtons(scene) {",
        1,
    )[0]

    assert "const voiceUsedAt = normalizeAvatarFloatingGuideUsageTimestamp(state.voiceUsedAt);" in usage_block
    assert "const persistedRound = Number(state && state.currentRound);" in director_source
    assert "return Number.isFinite(persistedRound) && persistedRound > 0 ? Math.floor(persistedRound) : 0;" in director_source
    assert "const day1EndedAt = normalizeAvatarFloatingGuideUsageTimestamp(state.day1EndedAt);" in usage_block
    assert "const roundStartedAt = normalizeAvatarFloatingGuideUsageTimestamp(state['day' + day + 'StartedAt']);" in usage_block
    assert "voiceUsedAt >= day1EndedAt" in usage_block
    assert "voiceUsedAt < roundStartedAt" in usage_block
    assert "target.closest('#micButton')" in director_source
    assert "recordAvatarFloatingGuideRoundEndForTermination(reason)" in director_source
    assert "getAvatarFloatingGuideActiveRound() === 1" in director_source
    assert "const voiceUsedAfterDay1End = hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart(3);" in scene_text_block
    assert "hasAvatarFloatingGuideUsage('voiceUsed')" not in scene_text_block
    assert "hasAvatarFloatingGuideUsage('voiceUsed')" not in voice_key_block
    assert "hasAvatarFloatingGuideUsage('voiceUsed')" not in emotion_block
