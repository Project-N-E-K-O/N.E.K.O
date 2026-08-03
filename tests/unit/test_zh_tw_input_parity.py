"""Traditional/Simplified parity for the user-input matchers (issue #2500).

Second batch of the "0 hit" class: tables and regexes that get matched against
what the user actually typed. Simplified and Traditional are distinct code
points, so a Simplified-only lexicon does not degrade for a Traditional writer —
the feature simply does not exist for them.

As in ``test_zh_tw_guard_parity``, the assertions are **parity** rather than
per-case expected values: none of these matchers is supposed to care about
orthography, so parity holds by construction while a hand-written expectation
would drift as the lexicons grow.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# main_logic/music_requests.py — explicit song-request parsing
# ---------------------------------------------------------------------------

MUSIC_PAIRS = [
    ("听一首晴天", "聽一首晴天"),
    ("来一首轻松的音乐", "來一首輕鬆的音樂"),
    ("帮我放一首治愈的歌", "幫我放一首治癒的歌"),
    ("请给我播放林俊杰的音乐", "請給我播放林俊傑的音樂"),
    ("换成歌曲：晴天", "換成歌曲：晴天"),
    ("从我的健身歌单里随机放一首", "從我的健身歌單裡隨機放一首"),
    ("播放我的红心歌单", "播放我的紅心歌單"),
    ("放点每日推荐", "放點每日推薦"),
    ("播放《告白气球》", "播放《告白氣球》"),
    ("放一首周杰伦的稻香", "放一首周杰倫的稻香"),
    ("来点摇滚", "來點搖滾"),
    ("播放网易云的日推", "播放網易雲的日推"),
]


def _music_shape(text: str):
    """The decision, with free-text payloads reduced to "present or not".

    Payload text is necessarily different between scripts (「稻香」 is the same
    but 「輕鬆」 is not), so comparing it verbatim would be wrong. What must match
    is the *routing*: which branch fired and which fields it decided to fill.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    request = parse_explicit_user_music_request(text)
    if request is None:
        return None
    return (
        request.personalization_source,
        bool(request.playlist_name),
        bool(request.song_name),
        bool(request.song_artist),
        bool(request.keyword),
    )


@pytest.mark.parametrize(("simplified", "traditional"), MUSIC_PAIRS)
def test_music_requests_parse_the_same_in_both_scripts(simplified, traditional):
    # Guard the premise: `None == None` would make this a vacuous pass if the
    # Simplified side ever stopped matching too (CodeRabbit).
    shape = _music_shape(simplified)
    assert shape is not None, f"{simplified}: 简体侧本身就没命中，用例前提不成立"
    assert _music_shape(traditional) == shape


EXCLUSION_PAIRS = [
    ("别放红心歌单，播放每日推荐", "別放紅心歌單，播放每日推薦"),
    ("别听我喜欢的", "別聽我喜歡的"),
    # ⚠️ 原本这条写的是 ("不要日推", "不要日推薦")，两个毛病：左右不是同一句
    # （「日推」简繁同形，右边是换了措辞不是换了字形），而且 _ZH_NEGATIVE_MUSIC
    # 要求否定词后 6 字内出现 播放/放/听/音乐/歌，「不要日推」一个都没有 → 根本
    # 进不了否定分支，断言 False 恒真、测的是「没被识别成否定」而不是「窄排除
    # 生效」。和之前那条 `None == None` 是同一类空测试（CodeRabbit）。
    ("不要放每日推荐的歌", "不要放每日推薦的歌"),
]


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("听一下这个视频", "聽一下這個影片"),
        ("播放这个视频", "播放這個影片"),
        ("看一下这个电影", "看一下這個電影"),
    ],
)
def test_video_requests_are_not_parsed_as_music(simplified, traditional):
    """⚠️ Taiwan says 影片, not 視頻.

    Backfilling only the character-mapped form left the most common Taiwanese
    word out, so 「聽一下這個影片」 fell through to the generic music parser and
    started searching for a song called 這個影片 (Codex P2).
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in (simplified, traditional):
        assert parse_explicit_user_music_request(text) is None, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [("别人都在听音乐", "別人都在聽音樂"), ("别人的歌很好听", "別人的歌很好聽")],
)
def test_the_noun_other_people_is_not_a_cancellation(simplified, traditional):
    """⚠️ Single-character 别/別 must not match the noun 别人/別人.

    「別人都在聽音樂」 is a statement about other people, not an imperative to
    stop. Simplified had this bug already — 「别人都在听音乐」 cancelled playback
    on main — so the negative lookahead fixes both scripts (Codex P2).
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要取消红心歌单", "不要取消紅心歌單"),
        ("别取消我喜欢的歌", "別取消我喜歡的歌"),
        ("不要停止播放红心歌单", "不要停止播放紅心歌單"),
    ],
)
def test_a_negated_stop_verb_is_not_a_cancellation(simplified, traditional):
    """⚠️ ``_ZH_DIRECT_MUSIC_STOP`` has to be anchored, not a bare search.

    An unanchored search finds 取消 inside 「不要取消」 and reads "don't cancel"
    as "cancel" — the exact reversal it was added to prevent (Codex P2). Anchored
    at the clause start (polite prefixes only), 「停止播放…」 still counts as a
    direct stop while 「不要取消…」 falls back to a narrow source exclusion.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("请帮我停止播放红心歌单", "請幫我停止播放紅心歌單"),
        ("给我暂停播放每日推荐", "給我暫停播放每日推薦"),
    ],
)
def test_a_polite_prefix_does_not_defeat_cancellation(simplified, traditional):
    """⚠️ The blocker was ``_ZH_NEGATIVE_MUSIC``, not the stop pattern.

    Its polite prefix only allowed 请/麻烦, so 「请帮我停止播放红心歌单」 never
    entered the refusal branch at all — a pre-existing, script-symmetric gap
    (greptile pointed at ``_ZH_DIRECT_MUSIC_STOP``, which by then already
    matched). Both now reuse the same prefix fragments as the request parser.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要播放电影歌曲", "不要播放電影歌曲"),
        ("不要播放这个视频的歌", "不要播放這個影片的歌"),
    ],
)
def test_a_compound_naming_music_is_still_a_cancellation(simplified, traditional):
    """⚠️ 電影歌曲 / 影片的歌 name music explicitly — the video word inside them
    must not suppress the refusal.

    The English side already had ``_EN_EXPLICIT_MUSIC_TARGET`` for this; Chinese
    had no counterpart, so 「不要播放电影歌曲」 silently stopped cancelling on the
    Simplified side too (Codex P2). Fixed for both.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要播放唱歌的视频", "不要播放唱歌的影片"),
        ("不要播放有歌曲的游戏", "不要播放有歌曲的遊戲"),
    ],
)
def test_a_music_noun_elsewhere_does_not_override_a_video_target(simplified, traditional):
    """⚠️ The explicit-music override must sit **next to** the target.

    Searching the whole clause meant 「不要播放唱歌的影片」 — a refusal about a
    video that merely mentions singing — had its video target discarded and
    turned into a playback cancellation. Only a compound formed by the target
    itself (電影歌曲 / 影片的歌) should override it (Codex P2).
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [("听一下这个视频吧", "聽一下這個影片吧"), ("播放这个游戏呢", "播放這個遊戲呢")],
)
def test_a_trailing_particle_does_not_defeat_the_non_music_guard(simplified, traditional):
    """The guard uses ``fullmatch``, so one trailing 吧/呢 used to make the
    payload miss and fall through to a music search. Pre-existing and
    script-symmetric — 「听一下这个视频吧」 searched for a song on main too
    (Codex P2). ⚠️ Target *continuations* like 影片內容 are still missed; that
    needs more than particle stripping and is not in this batch.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in (simplified, traditional):
        assert parse_explicit_user_music_request(text) is None, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别的歌播放不了吗", "別的歌播放不了嗎"),
        ("别的地方也播放音乐", "別的地方也播放音樂"),
        ("别致的音乐", "別緻的音樂"),
        ("别具一格的音乐", "別具一格的音樂"),
        ("别有风味的歌曲", "別有風味的歌曲"),
    ],
)
def test_the_single_char_negator_must_govern_a_playback_verb(simplified, traditional):
    """⚠️ Positive requirement, not a blacklist.

    Earlier rounds tried excluding the nouns that follow 别/別 one by one —
    first 别人, then 别的 — but what can follow a one-character negator is an
    open set (別緻 / 別具一格 / 別有風味 …), so the blacklist could never close.
    The single-char branch now requires 别/別 to sit directly on a playback verb
    (with an optional 再), which covers all of them at once and keeps the
    genuine imperatives.

    ⚠️ Simplified benefits too: 「别致的音乐」 and 「别的歌播放不了吗」 both
    cancelled playback on main.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别放音乐了", "別放音樂了"),
        ("别再放了", "別再放了"),
        ("别再播音乐", "別再播音樂"),
        # ⚠️ A recipient phrase may sit between the negator and the verb. The
        # first version of the positive rule allowed only whitespace and 再,
        # which dropped these (Codex P2). What is allowed is a *closed* set —
        # the same `_ZH_FOR_ME` fragment the request parser uses — not another
        # wildcard window; that is what separates this from the blacklist it
        # replaced.
        ("别给我放歌", "別給我放歌"),
        ("别帮我播放音乐", "別幫我播放音樂"),
        ("别再给我放歌", "別再給我放歌"),
    ],
)
def test_the_single_char_negator_still_matches_imperatives(simplified, traditional):
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("取消收藏这首歌", "取消收藏這首歌"),
        ("请取消收藏这首歌", "請取消收藏這首歌"),
        # 引导语不该把来源编辑变成取消播放。
        ("算了取消收藏这首歌", "算了取消收藏這首歌"),
    ],
)
def test_a_source_edit_is_not_a_playback_cancellation(simplified, traditional):
    """⚠️ 取消 here governs 收藏 (unfavourite), not playback.

    Anchoring the stop verb at the clause start was not enough — it also has to
    govern a playback verb, or a source-management command cancels the pending
    request instead (Codex P2). Simplified returned False on main.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("帮我放一段你们说话的声音", "幫我放一段你們說話的聲音"),
        ("听一下你们说话的声音", "聽一下你們說話的聲音"),
    ],
)
def test_plural_second_person_speech_requests_are_rejected(simplified, traditional):
    """他們/她們/我們 were all listed; 你們 was simply missed — and Simplified
    「你们」 was missing too, so 「帮我放一段你们说话的声音」 searched for a song
    called 声音 by the artist 一段你们说话 on main (Codex P2)."""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in (simplified, traditional):
        assert parse_explicit_user_music_request(text) is None, text


DIRECT_STOP_PAIRS = [
    ("停止播放红心歌单", "停止播放紅心歌單"),
    # ⚠️ A stop verb may govern the **source noun** directly, with no separate
    # playback verb. Requiring one dropped 「停止紅心歌單」 (Codex P2). The source
    # nouns are a closed set and deliberately exclude 收藏, which is a verb in
    # 「取消收藏这首歌」 and governs the favourite, not playback.
    ("停止红心歌单", "停止紅心歌單"),
    ("停止红心歌单音乐", "停止紅心歌單音樂"),
    # ⚠️ 「算了」这类改主意的引导语，两个模式必须认同一套。不带逗号时切不出
    # 子句，只有 _ZH_NEGATIVE_MUSIC 收了它、_ZH_DIRECT_MUSIC_STOP 没收，
    # 就会被判成窄排除而不是取消播放（greptile P1）。前缀已提成共用常量。
    ("算了停止播放红心歌单", "算了停止播放紅心歌單"),
    ("还是算了暂停播放每日推荐", "還是算了暫停播放每日推薦"),
    # 前缀里的「我想/我要」——上一轮只统一了引导语，这一格还漂着。
    ("我想停止播放红心歌单", "我想停止播放紅心歌單"),
    ("算了我想停止播放红心歌单", "算了我想停止播放紅心歌單"),
    ("我要暂停播放每日推荐", "我要暫停播放每日推薦"),
    # 来源名前允许所有格「我的」（Codex P2）。
    ("停止我的红心歌单", "停止我的紅心歌單"),
    ("暂停播放我喜欢的", "暫停播放我喜歡的"),
    ("取消播放每日推荐", "取消播放每日推薦"),
]


@pytest.mark.parametrize(("simplified", "traditional"), DIRECT_STOP_PAIRS)
def test_an_explicit_stop_naming_a_source_still_cancels(simplified, traditional):
    """⚠️ ``_is_source_exclusion_preference`` used only ``_EN_DIRECT_MUSIC_STOP``.

    With no Chinese counterpart, *any* Chinese clause naming a personalization
    source read as a narrow exclusion — so 「停止播放红心歌单」 ("stop playing…")
    did not stop anything. Simplified had this all along; Traditional only fell
    into it once the source lexicon started matching (Codex P2).

    Both scripts must now cancel, which is a **behaviour change on the
    Simplified side too** — it is the same bug, fixed on both.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, f"{text}: 明确停止却没取消"


@pytest.mark.parametrize(("simplified", "traditional"), EXCLUSION_PAIRS)
def test_exclusion_pairs_actually_reach_the_negative_branch(simplified, traditional):
    """Premise guard for the test below.

    ``is_explicit_music_cancellation`` returns False both when a clause is a
    narrow exclusion *and* when it was never recognised as a refusal at all — so
    asserting False alone cannot tell those apart. Pin that these inputs do match
    the negative pattern, otherwise the next assertion is vacuous.
    """
    from main_logic.music_requests import _ZH_NEGATIVE_MUSIC

    for text in (simplified, traditional):
        assert _ZH_NEGATIVE_MUSIC.search(text), f"{text}: 没进否定分支，下面那条断言是空的"


@pytest.mark.parametrize(("simplified", "traditional"), EXCLUSION_PAIRS)
def test_excluding_one_source_is_not_read_as_stopping_playback(simplified, traditional):
    """⚠️ ``_ZH_NEGATIVE_MUSIC`` and ``_excluded_personalization_source`` are a
    pair and must list the same scripts.

    The first decides "this clause is a refusal"; the second decides "…of one
    source only, not of playback". Backfilling the first alone turned
    「別放紅心歌單，播放每日推薦」 from a narrow exclusion into a full stop —
    i.e. the Traditional user's music got cut off entirely (greptile P1).
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import (
        _excluded_personalization_source,
        is_explicit_music_cancellation,
    )

    for text in (simplified, traditional):
        assert _excluded_personalization_source(text), f"{text}: 认不出被排除的来源"
        assert is_explicit_music_cancellation(text) is False, f"{text}: 被当成全局取消"
    assert _excluded_personalization_source(simplified) == (
        _excluded_personalization_source(traditional)
    )


def test_traditional_liked_playlist_is_not_parsed_as_an_artist_search():
    """The worst case here was not a miss but a *misparse*.

    「播放我的紅心歌單」 used to fall through the personalization branch into the
    artist/song branch and come out as "search for the song 紅心歌單 by the artist
    我" — i.e. a wrong search instead of the user's liked playlist.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    request = parse_explicit_user_music_request("播放我的紅心歌單")
    assert request is not None
    assert request.personalization_source == "liked"
    assert not request.song_artist
    assert not request.song_name


CANCEL_PAIRS = [
    ("别放音乐了", "別放音樂了"),
    ("把音乐关掉", "把音樂關掉"),
    ("暂停播放", "暫停播放"),
    ("不想听歌了", "不想聽歌了"),
    ("取消播放音乐", "取消播放音樂"),
]


@pytest.mark.parametrize(("simplified", "traditional"), CANCEL_PAIRS)
def test_music_cancellation_is_detected_in_both_scripts(simplified, traditional):
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(simplified) is True
    assert is_explicit_music_cancellation(traditional) is True


@pytest.mark.parametrize(
    "text",
    [
        "我们来聊聊天气吧",
        "我們來聊聊天氣吧",
        "帮我放一段你说话的声音",
        "幫我放一段你說話的聲音",
        "播放这个视频",
        "播放這個視頻",
    ],
)
def test_non_music_requests_are_still_rejected(text):
    from main_logic.music_requests import parse_explicit_user_music_request

    assert parse_explicit_user_music_request(text) is None


def test_mood_words_are_not_mistaken_for_artist_names():
    """「放輕鬆的歌」 must route as a style keyword, not as an artist search."""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in ("放轻松的歌", "放輕鬆的歌"):
        request = parse_explicit_user_music_request(text)
        assert request is not None, text
        assert not request.song_artist, f"{text}: 曲风被当成歌手名"


# ---------------------------------------------------------------------------
# brain/openclaw_adapter.py — zero-LLM magic-command classifier
# ---------------------------------------------------------------------------

MAGIC_PAIRS = [
    # ⚠️ /clear 的触发词不在这里：它不可逆地清掉对话历史，判据又是自由文本子串，
    # 所以和 approve 一样刻意保持简体。见
    # test_destructive_command_triggers_stay_simplified_only。
    ("换个话题", "換個話題"),
    ("说点别的", "說點別的"),
    ("重新开始", "重新開始"),
    # 台湾用「搜尋」，所以这一条不是「搜索」的字形转换。
    ("停止搜索", "停止搜尋"),
    ("取消这个任务", "取消這個任務"),
    ("停下来", "停下來"),
    # `没问题 / 沒問題` 走的是**整句精确匹配**，不是子串触发词——见下面
    # test_approve_substring_triggers_stay_simplified_only。
    ("没问题", "沒問題"),
]


@pytest.mark.parametrize(("simplified", "traditional"), MAGIC_PAIRS)
def test_magic_commands_resolve_the_same_in_both_scripts(simplified, traditional):
    from brain.openclaw_adapter import OpenClawAdapter

    resolved = OpenClawAdapter.rule_magic_command(simplified)
    assert resolved is not None, f"{simplified}: 简体侧本身就没命中，用例前提不成立"
    assert OpenClawAdapter.rule_magic_command(traditional) == resolved


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("我忘了带钥匙", "我忘記帶鑰匙"),
        ("雨停了", "雨停了"),
        ("停电了", "停電了"),
        ("想听听你的看法", "想聽聽你的看法"),
    ],
)
def test_high_precision_negatives_still_suppress_in_both_scripts(simplified, traditional):
    """The conservative negative list has to move with the trigger list, or the
    Traditional side loses its suppression while gaining the triggers."""
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(simplified) is None
    assert OpenClawAdapter.rule_magic_command(traditional) is None


@pytest.mark.parametrize(
    "text",
    [
        # /daemon approve — executes a high-risk action
        "去執行吧", "刪吧", "準了", "沒問題，去執行",
        "拒絕去執行", "禁止去執行", "要去執行嗎？", "他說去執行", "別去執行",
        # /clear — irreversibly wipes the conversation history
        "忘了剛才的事", "清空聊天記錄", "清除聊天記錄", "刪掉剛才的記錄",
        "我想知道如何清除聊天記錄",
    ],
)
def test_destructive_command_triggers_stay_simplified_only(text):
    """⚠️ Deliberate gap: ``/daemon approve``'s substring triggers are NOT
    backfilled to Traditional in this batch.

    That command makes the caller actually run a high-risk action, and it is
    triggered by *substring containment over free text*. On main that already
    approves 我准了假 (「准了」), 删吧台的记录 (「删吧」), 他说去执行,
    可以去执行吗, 拒绝去执行. Adding Traditional triggers doubles the exposure
    of that pre-existing hole.

    The same reasoning covers ``/clear``: it wipes the conversation history, and
    on main a plain question — 「我想知道如何清除聊天记录」 — already returns
    ``/clear`` (Codex P2). ``/new`` and ``/stop`` are *not* in this list: the
    worst a false positive does there is change the subject or halt a task, so
    those keep full Traditional parity.

    A "reject when a negator precedes the trigger" guard was tried and an
    adversarial pass broke it on 196 inputs: negation to the *right* of the
    trigger (「去執行？我不要」), the anchor landing on an unrelated substring
    (「这标准了不起，但不要去执行」 anchors on 「准了」), and questions
    (「要去執行嗎？」) all sailed through — while it *also* rejected
    「没错，去执行」/「没意见，去执行」, i.e. the affirmations that an approval
    context is literally built out of negation words. A blacklist cannot work
    here; the fix is a normalized whole-sentence whitelist, which changes
    Simplified behaviour and so does not belong in a zh-TW batch.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Non-destructive commands keep full parity — a false positive here only
        # changes the subject or stops a task.
        ("這個遊戲要怎麼重新開始？", "/new"),
        ("这个游戏要怎么重新开始？", "/new"),
    ],
)
def test_non_destructive_commands_keep_traditional_parity(text, expected):
    """Recorded rather than fixed: both scripts behave the same, and that same
    behaviour (a question about the command triggering it) is what main already
    does for Simplified. Narrowing it is a separate, script-neutral change.
    """
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected


def test_traditional_can_still_approve_by_whole_sentence():
    """The exact-match branch is the safe shape — whole sentence, no substring,
    no free text — so Traditional is backfilled there."""
    from brain.openclaw_adapter import OpenClawAdapter

    for text in ("沒問題", "同意", "我同意", "没问题"):
        assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


def test_simplified_approve_behaviour_is_untouched_by_this_batch():
    """Pins the claim that this batch does not move the Simplified side at all.

    Includes the known-bad pre-existing cases on purpose: they must keep
    behaving exactly as on main, so that fixing them later is a visible,
    deliberate change rather than something this batch smuggled in.
    """
    from brain.openclaw_adapter import OpenClawAdapter

    approve = "/daemon approve"
    for text in ("去执行吧", "删吧", "准了", "没问题，去执行"):
        assert OpenClawAdapter.rule_magic_command(text) == approve, text
    # ⚠️ Known pre-existing false approvals (substring containment). Asserted as
    # the *current* behaviour, not as desirable behaviour.
    for text in ("我准了假", "删吧台的记录", "他说去执行", "可以去执行吗"):
        assert OpenClawAdapter.rule_magic_command(text) == approve, text


@pytest.mark.parametrize("text", ["别找了", "別找了", "算了别查了", "算了別查了"])
def test_stop_triggers_containing_a_negator_are_untouched(text):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/stop"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我不同意这个方案，换个话题", "/new"),
        ("我不同意這個方案，換個話題", "/new"),
        ("我还没同意，停止搜索", "/stop"),
        ("我還沒同意，停止搜尋", "/stop"),
        ("我不同意，清空聊天记录", "/clear"),
        # 繁体那条不在这里：/clear 的触发词刻意保持简体（见
        # test_destructive_command_triggers_stay_simplified_only），所以
        # 「我不同意，清空聊天記錄」本来就该是 None，不是被否定短语压掉的。
    ],
)
def test_negation_does_not_suppress_the_other_commands(text, expected):
    """⚠️ The negation check is scoped to the approve branch on purpose.

    A first attempt put the negated-approval phrases in the global
    high-precision list, which is consulted before *every* mapping — so an
    unrelated "I don't agree with the plan, change the topic" stopped
    dispatching ``/new`` at all (Codex P2). Only ``/daemon approve`` executes
    anything, so only it gets the fail-closed treatment.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected


# ---------------------------------------------------------------------------
# utils/music_crawlers.py — which crawler a keyword routes to
# ---------------------------------------------------------------------------

ROUTING_TABLES = [
    "ROUTING_STRONG_CLASSICAL_KEYWORDS",
    "ROUTING_INSTRUMENT_KEYWORDS",
    "ROUTING_MODERN_STYLE_KEYWORDS",
    "ROUTING_INDIE_KEYWORDS",
    "ROUTING_CHINESE_KEYWORDS",
]

# Simplified -> Traditional for exactly the characters these five tables use.
# Explicit rather than converter-driven: a converter would just restate itself.
#
# ⚠️ 杰 is deliberately absent. It is *not* a 1:1 mapping — 周杰倫 keeps 杰 while
# 林俊傑 takes 傑, so a character map gets one of the two wrong whichever way it
# is set. Both names are listed below instead.
_ROUTING_CHAR_MAP = str.maketrans({
    "贝": "貝", "扎": "札", "响": "響", "协": "協", "鸣": "鳴", "钢": "鋼",
    "说": "說", "电": "電", "松": "鬆", "独": "獨", "众": "眾", "环": "環",
    "华": "華", "语": "語", "国": "國", "伦": "倫", "邓": "鄧",
    "陈": "陳", "张": "張", "学": "學", "刘": "劉", "静": "靜",
    "荣": "榮", "谦": "謙", "赵": "趙", "许": "許", "莹": "瑩", "闽": "閩",
})

# Entries a plain character map cannot produce: proper names whose Taiwan
# rendering is a different choice of character, not a different spelling.
_TAIWAN_RENDERINGS = {
    "莫扎特": "莫札特",
    "周杰伦": "周杰倫",
    "林俊杰": "林俊傑",
}

# Rows that belong to a *different language's* section of the same table, where
# Chinese conversion rules do not apply. `中国語` is the Japanese word for
# "Chinese language" — 国 is correct there and must not become 國.
_NOT_CHINESE_ROWS = {"中国語"}


@pytest.mark.parametrize("table_name", ROUTING_TABLES)
def test_every_simplified_routing_keyword_has_a_traditional_sibling(table_name):
    from utils import music_crawlers

    table = getattr(music_crawlers, table_name)
    present = {entry.lower() for entry in table}
    missing = []
    converted_any = False
    for entry in table:
        if entry in _NOT_CHINESE_ROWS:
            continue
        if not any("一" <= ch <= "鿿" for ch in entry):
            continue  # latin / kana / hangul row
        traditional = _TAIWAN_RENDERINGS.get(entry, entry.translate(_ROUTING_CHAR_MAP))
        if traditional == entry:
            continue  # identical in both scripts
        converted_any = True
        if traditional.lower() not in present:
            missing.append((entry, traditional))
    assert converted_any, f"{table_name}: 字符映射没转出任何东西，用例已失效"
    assert not missing, f"{table_name} 缺繁体对应条目：{missing}"


def test_routing_tables_are_module_level_so_they_can_be_asserted():
    """They used to be locals inside the scheduler, where nothing could see a
    missing entry until a user reported bad routing."""
    from utils import music_crawlers

    for name in ROUTING_TABLES:
        table = getattr(music_crawlers, name)
        # 只要求「可迭代且非空」——这几张表只做成员查找，将来改成 tuple/frozenset
        # 是自然的优化，钉死 list 会无谓地红（CodeRabbit nitpick）。
        assert isinstance(table, (list, tuple, set, frozenset)), name
        assert table, f"{name}: 表为空"


def test_the_two_cancellation_patterns_share_one_preface():
    """⚠️ Structural guard, not a sample.

    ``_ZH_NEGATIVE_MUSIC`` decides "this clause is a refusal" and
    ``_ZH_DIRECT_MUSIC_STOP`` decides "…and it stops playback rather than
    narrowing a source". They are consulted on the same clause, so a prefix
    accepted by one and not the other silently reclassifies the utterance —
    which is exactly how 「算了停止播放红心歌单」 became a narrow exclusion. Both
    now build from the same constant; assert that rather than adding yet another
    sample sentence.
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    # ⚠️ 断言**完整前缀**而不只是引导语。第一版只对了引导语，结果没抓住
    # `_ZH_DIRECT_MUSIC_STOP` 比对方多一个 `(?:想|要)?`——`我想停止播放紅心歌單`
    # 因此被静默忽略（greptile P1 第二次）。守卫要覆盖到会漂的整段。
    shared_prefix = "^" + mr._ZH_CHANGED_MIND_PREFACE + mr._ZH_REQ_PREFIX
    assert mr._ZH_CHANGED_MIND_PREFACE, "引导语常量是空的"
    assert mr._ZH_NEGATIVE_MUSIC.pattern.startswith(shared_prefix), (
        "_ZH_NEGATIVE_MUSIC 的前缀与 _ZH_DIRECT_MUSIC_STOP 漂开了"
    )
    assert mr._ZH_DIRECT_MUSIC_STOP.pattern.startswith(shared_prefix), (
        "_ZH_DIRECT_MUSIC_STOP 的前缀与 _ZH_NEGATIVE_MUSIC 漂开了"
    )


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("来点评一下这张卡", "來點評一下這張卡"),
        ("来点评论", "來點評論"),
    ],
)
def test_a_two_char_action_does_not_swallow_a_longer_verb(simplified, traditional):
    """⚠️ 来点/來點 is the shortest action here and 点 also heads other verbs
    (点评 / 点击 / 点赞 / 点名 / 点菜).

    Without the guard 「來點評一下這張卡」 splits into 來點 + 評一下這張卡 and
    searches for a song by that name (Codex P2). ⚠️ Pre-existing on the
    Simplified side — 「来点评一下这张卡」 did the same on main — so the guard
    fixes both. The rejected set is **not** claimed to be exhaustive; 点 takes
    an open set of complements, which is inherent to a two-character action and
    not something the zh-TW backfill introduced.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in (simplified, traditional):
        assert parse_explicit_user_music_request(text) is None, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [("来点摇滚", "來點搖滾"), ("来点周杰伦的歌", "來點周杰倫的歌")],
)
def test_the_two_char_action_still_parses_real_requests(simplified, traditional):
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in (simplified, traditional):
        assert parse_explicit_user_music_request(text) is not None, text


def test_a_stop_verb_governing_a_source_without_a_music_noun_is_unchanged():
    """Recorded, not fixed: 「暫停每日推薦」 is False on main too.

    ``_ZH_NEGATIVE_MUSIC`` needs 播放/放/听/音乐/歌 within six characters of the
    negator, and 每日推薦 supplies none — 「停止紅心歌單」 only passes because
    歌單 contains 歌. Widening the refusal pattern's object is script-neutral
    work and is not part of this batch.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in ("暂停每日推荐", "暫停每日推薦"):
        assert is_explicit_music_cancellation(text) is False, text


# ---------------------------------------------------------------------------
# 对抗扫描（推送前自查）找出的四条回归
# ---------------------------------------------------------------------------

NON_PLAYBACK_BIE_PAIRS = [
    ("别放弃", "別放棄"),
    ("别放在心上", "別放在心上"),
    ("别放手", "別放手"),
    ("别放过我", "別放過我"),
    ("别听他的", "別聽他的"),
    ("别听信谣言", "別聽信謠言"),
    ("别播种太早", "別播種太早"),
]


@pytest.mark.parametrize(("simplified", "traditional"), NON_PLAYBACK_BIE_PAIRS)
def test_bie_plus_a_playback_verb_in_a_non_playback_sense(simplified, traditional):
    """⚠️⚠️ 放 / 聽 / 播 head many non-playback verbs.

    Requiring 别/別 to sit on a playback verb was **not** sufficient, contrary to
    what the previous round's comment claimed: 放棄 / 放心上 / 放手 / 放過 /
    聽信 / 播種 all start with one. 「別放棄」 is an everyday phrase and it was
    cancelling the user's music.

    The fix adds a **closed** lookahead — the playback verb must be followed by
    end-of-clause, a modal particle, punctuation, or a music/source noun. That
    set is enumerable, unlike "what can follow 別", which is not.

    ⚠️ Traditional was entirely safe on main (the character class held only the
    Simplified 别), so this batch imported the whole class; the fix also clears
    it on the Simplified side, where 「别放弃」 cancelled playback on main.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


A_NOT_A_PAIRS = [
    ("要不要停止播放", "要不要停止播放"),
    ("想不想关掉音乐", "想不想關掉音樂"),
    ("我要不要取消播放", "我要不要取消播放"),
    ("要不要放歌", "要不要放歌"),
]


@pytest.mark.parametrize(("simplified", "traditional"), A_NOT_A_PAIRS)
def test_an_a_not_a_question_is_not_a_command(simplified, traditional):
    """⚠️ The only regression in this batch that broke Simplified too.

    `_ZH_REQ_PREFIX` gained an optional `(?:想|要)` so that 「我想停止播放…」
    would enter the refusal branch. It also ate the first 要/想 of an A-not-A
    question, leaving 不要/不想 to match the negator — so a user *wondering*
    whether to stop was read as commanding it. `(?!不)` closes that.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止这个红心歌单", "停止這個紅心歌單"),
        ("停止那个红心歌单", "停止那個紅心歌單"),
        ("停止你的红心歌单", "停止你的紅心歌單"),
    ],
)
def test_a_determiner_before_the_source_still_cancels(simplified, traditional):
    """Only 我的 was allowed, so 「停止這個紅心歌單」 read as a narrow exclusion
    and playback kept running — while 「停止這個歌單」 (two characters shorter)
    cancelled correctly. Determiners are a closed set; all of them are listed.
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


def test_the_taiwanese_spelling_of_like_is_rejected_too():
    """⚠️ 點讚, not 點贊 — 讚 is praise, 贊 is sponsorship, and they are not
    interchangeable in Traditional.

    The blacklist was written by mechanically transliterating the Simplified
    赞, so 「来点赞吧」 was blocked while 「來點讚吧」 sailed through into a music
    search. Same class of error as 着/著 in the topic stop-chars: **glyph
    correspondence is not a bijection.**
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    for text in ("来点赞吧", "來點讚吧", "來點贊吧"):
        assert parse_explicit_user_music_request(text) is None, text
    for text in ("来点摇滚", "來點搖滾"):
        assert parse_explicit_user_music_request(text) is not None, text
