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
    # ⚠️ 共享的不止引导语和前缀，还有疑问守卫 `_ZH_PREFIXED_QUESTION_GUARD`。
    # 它挡的是「以收件人短语/意图词开头 + 以疑问语气词结尾」这一个形状；只加在
    # 一条规则上，另一条就会继续把 `請幫我停止播放嗎？` 当命令，整句静默换类。
    # 每往这段共享开头加一个常量，就得往这里加一项——漏了这个测试就不再覆盖它。
    shared_prefix = (
        "^"
        + mr._ZH_PREFIXED_QUESTION_GUARD
        + mr._ZH_CHANGED_MIND_PREFACE
        + mr._ZH_REQ_PREFIX
    )
    assert mr._ZH_CHANGED_MIND_PREFACE, "引导语常量是空的"
    assert mr._ZH_PREFIXED_QUESTION_GUARD, "疑问守卫常量是空的"
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


# ---------------------------------------------------------------------------
# 第十一轮：三条级联回归——全部由前几轮我自己的修复引出
# ---------------------------------------------------------------------------


def _alternation(pattern: str) -> list[str]:
    """把 `(?:a|b|c)` 这种闭集常量拆回词表。

    ⚠️ 用常量推导而不是手抄列表：这几个闭集这一轮已经被改过三次，手抄的清单
    只会 pin 住抄的那一刻。哪天有人往表里加个来源名，笛卡尔积自动覆盖它。
    """  # noqa: DOCSTRING_CJK
    words = pattern.removeprefix('(?:').removesuffix(')').split('|')
    # ⚠️ 拆法只对**扁平**闭集有效。常量一旦写成嵌套形式（`(?:紅心(?:歌單)?|日推)`），
    # 按 `|` 平切会得到 `紅心(?:歌單)?` 这种残片；残片拼进句子后不是合法中文输入，
    # 会被下面的前提守卫静默 skip 掉——覆盖被稀释，但一条都不红。
    # 所以这里让解析失效直接变成红灯。
    for word in words:
        assert word and all('一' <= ch <= '鿿' for ch in word), (
            f'{pattern} 不再是扁平闭集，_alternation 拆出了残片 {word!r}'
        )
    return words


def _stop_target_product():
    from main_logic import music_requests as mr

    verbs = ('取消', '停止', '关掉', '關掉')
    determiners = ('', '这个', '這個', '我的')
    nouns = _alternation(mr._ZH_STOP_SOURCE_NOUN) + _alternation(mr._ZH_STOP_MUSIC_NOUN)
    return [
        (verb, det, noun)
        for verb in verbs
        for det in determiners
        for noun in nouns
    ]


STOP_TARGET_PRODUCT = _stop_target_product()


def test_the_stop_target_product_is_not_empty():
    """⚠️ 词表是从常量拆出来的。拆法一旦失效，下面两条笛卡尔积用例会退化成
    空参数集、全绿在没跑上。
    """  # noqa: DOCSTRING_CJK
    assert len(STOP_TARGET_PRODUCT) > 100


@pytest.mark.parametrize(("verb", "determiner", "noun"), STOP_TARGET_PRODUCT)
def test_a_source_management_suffix_is_never_a_playback_stop(verb, determiner, noun):
    """⚠️⚠️ 「<停止对象>的收藏」永远不是停止播放。

    上一版在来源名后面挂了个单点后视，挡得住 `取消這個歌單的收藏`，挡不住
    `取消紅心歌單的收藏`——`紅心` 先匹配，紧跟的 `歌單` 正好满足后视，尾巴照样
    被吞。同族还有 `取消我喜歡的歌的收藏` / `取消日推歌單的收藏`。

    「在某一个点上做后视」和「把整个短语消费完再要求子句边界」是两件事：短语
    可以有任意多节，逐点判永远漏掉最后一节后面的东西。所以这里用笛卡尔积而不是
    举例——上一版那批举例式用例全是绿的。

    ⚠️ 前提守卫：只有「裸形式确实会取消」的组合才谈得上被后缀改写。没有这个
    守卫，`關掉我的曲` 这种本来就不取消的组合会让断言真空通过。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    bare = f'{verb}{determiner}{noun}'
    if not is_explicit_music_cancellation(bare):
        pytest.skip(f'{bare} 本身就不是取消播放')
    assert is_explicit_music_cancellation(f'{bare}的收藏') is False, bare


def test_the_product_leaves_enough_cases_unskipped():
    """⚠️ 上面那条几乎全靠前提守卫过滤。如果哪天守卫把所有组合都 skip 掉，
    整条用例就成了摆设——这里钉住实际跑到断言的数量。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    live = [
        (v, d, n)
        for v, d, n in STOP_TARGET_PRODUCT
        if is_explicit_music_cancellation(f'{v}{d}{n}')
    ]
    assert len(live) >= 60, f'只有 {len(live)} 个组合跑到断言'


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止红心歌单的音乐", "停止紅心歌單的音樂"),
        ("取消我喜欢的歌", "取消我喜歡的歌"),
        ("停止这个红心歌单了", "停止這個紅心歌單了"),
        ("停止这个红心歌单吧", "停止這個紅心歌單吧"),
    ],
)
def test_a_multi_part_target_reaching_the_clause_end_still_cancels(
    simplified, traditional
):
    """⚠️ 「停止紅心歌單的音樂」是关键样本：它后面也跟着「的」，但跟的是音乐
    名词而不是来源操作，短语能一路吃到句末，必须仍然取消。语气词同理。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("取消收藏这首歌", "取消收藏這首歌"),
        ("取消红心这首歌", "取消紅心這首歌"),
    ],
)
def test_unfavouriting_a_source_is_not_stopping_playback(simplified, traditional):
    """⚠️ 来源名词必须是**完整的**停止对象。

    上一轮为了让「停止這個紅心歌單」能取消，给来源名词前面开了限定词白名单。
    副作用是「取消這個歌單」这个前缀会先匹配上，把尾巴「的收藏」整个忽略——
    一次「取消收藏」于是变成了停止播放。base 简繁都是 False。

    修法跟 `別` 那条一样是**正向闭集**后视：来源名后面必须是句末、语气词、
    标点、播放动词，或者「的+音乐名词」。「的收藏」哪一支都不落。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("我要停止播放吗", "我要停止播放嗎"),
        ("我想暂停播放吗？", "我想暫停播放嗎？"),
        ("我要关掉音乐吗", "我要關掉音樂嗎"),
        ("帮我停止播放吗", "幫我停止播放嗎"),
        ("给我停止播放吗", "給我停止播放嗎"),
        ("请帮我停止播放吗", "請幫我停止播放嗎"),
    ],
)
def test_asking_whether_to_stop_is_not_ordering_a_stop(simplified, traditional):
    """⚠️ 本 PR 让这两条规则认得了两种新前缀：收件人短语（帮我/给我）和
    意图词（我想/我要）。认得开头之后就不再看句末——「用户在自问要不要停」
    被判成了「命令停」。base 简繁都是 False。

    这是 `(?!不)` 那条 A-not-A 修复的**同族漏洞**：都是可选前缀吃掉了一个字，
    让剩下的部分看起来像命令。前者靠 A-not-A 的「不」识别，这里没有「不」，
    只能看句末语气词。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "text",
    ["停止播放吗", "请停止播放吗", "麻烦停止播放吗", "我停止播放吗",
     "算了请停止播放吗", "关掉音乐吗", "别放了吗", "不要播放了吗"],
)
def test_the_question_guard_does_not_touch_pre_existing_behaviour(text):
    """⚠️⚠️ 疑问守卫**必须要求那两种前缀真的出现**。

    改成「凡是以疑问语气词结尾就不是命令」会顺手把这些改掉——它们在基线上
    全是 True。修繁体的洞不能拿简体既有行为去换；这一轮已经有两条回归是这么
    来的（`要不要停止播放`、`别放弃`）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要播放电影歌曲的视频", "不要播放電影歌曲的影片"),
        ("别放电影原声带的预告片", "別放電影原聲帶的預告片"),
        ("不要播放电视剧主题曲的视频", "不要播放電視劇主題曲的影片"),
    ],
)
def test_a_music_compound_does_not_hide_a_later_video_target(simplified, traditional):
    """⚠️ 撞上音乐复合词之后**必须继续往后扫**。

    「不要播放電影歌曲的影片」里先撞上的是「電影」，它确实只是「電影歌曲」
    这个复合词的一半——但句子后面还有个真的非音乐目标「影片」。上一版撞上
    复合词就直接丢弃、不再往后找，于是整句退化成取消音乐：用户说「别放视频」，
    系统听成「别放音乐」并把正在放的歌停了。简体在基线上是 False。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要播放电影歌曲", "不要播放電影歌曲"),
        ("不要放这个视频的歌", "不要放這個影片的歌"),
    ],
)
def test_a_bare_music_compound_is_still_a_music_refusal(simplified, traditional):
    """继续往后扫不能变成「永远找得到非音乐目标」——句子里只有复合词、
    没有第二个目标时，仍然是拒绝音乐。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


# ---------------------------------------------------------------------------
# 第十二轮
# ---------------------------------------------------------------------------


def _playback_compound_product():
    """从常量拆出 playlist/queue 的全部写法，做笛卡尔积。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    inner = mr._ZH_PLAYBACK_COMPOUND_NOUN.removeprefix('(?:播放(?:').removesuffix('))')
    nouns = ['播放' + w for w in inner.split('|')]
    return [
        (verb, noun, sep, tail)
        # ⚠️ 简繁必须成对。原来只有繁体的 暫停/關閉，于是 `暂停播放列表的收藏`
        # 和 `关闭播放列表收藏` 这两类**简体**输入一条都没被覆盖到——在一个
        # 主题就是简繁对等的文件里。
        for verb in ('取消', '停止', '关掉', '關掉', '暂停', '暫停', '关闭', '關閉')
        for noun in nouns
        for sep in ('的', '')
        for tail in ('', '了', '吧')
    ]


PLAYBACK_COMPOUND_PRODUCT = _playback_compound_product()


def test_the_playback_compound_product_is_not_empty():
    assert len(PLAYBACK_COMPOUND_PRODUCT) > 100


@pytest.mark.parametrize(
    ("verb", "noun", "sep", "tail"), PLAYBACK_COMPOUND_PRODUCT
)
def test_a_playback_compound_noun_is_not_a_playback_verb(verb, noun, sep, tail):
    """⚠️ 「播放清單 / 播放列表」是**名词**，里面的「播放」不是动词。

    停止动词后面直接跟它时，`取消播放` 这个前缀先吃掉「播放」二字，把真正的
    中心语「收藏」整段忽略——一次「取消歌单收藏」于是把用户正在放的歌停了。
    繁体侧特别容易踩：台湾就叫「播放清單」。

    ⚠️ 前提守卫：先断言裸形式确实取消，否则「加了收藏就不取消」可能真空通过。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    bare = f'{verb}{noun}{tail}'
    assert is_explicit_music_cancellation(bare) is True, f'{bare} 前提不成立'
    assert is_explicit_music_cancellation(f'{verb}{noun}{sep}收藏{tail}') is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("取消播放列表的收藏", "取消播放清單的收藏"),
        ("停止播放列表的收藏", "停止播放清單的收藏"),
        ("关掉播放列表收藏", "關閉播放清單收藏"),
        ("取消播放清单的收藏", "取消播放清單的收藏"),
    ],
)
def test_both_scripts_spell_the_playlist_compound(simplified, traditional):
    """⚠️ 词表是从常量拆出来的，缩表会让上面那条笛卡尔积跟着缩水而假绿。
    简/繁/混三种写法另外钉死。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "text",
    ["停止播放我的收藏", "停止播放收藏的歌", "停止播放清單裡的紅心歌",
     "停止播放那首紅心裡的歌", "停止播放紅心歌單", "暫停播放我喜歡的"],
)
def test_the_compound_guard_does_not_swallow_real_stops(text):
    """守卫只在两个闭集同时出现时开火，不能把真停止一起吞掉。

    ⚠️ 「停止播放我的收藏」是关键样本：它也含「收藏」，但「播放」在这里是动词。
    一刀切拒绝「…的收藏」后缀的方案就是栽在这条上。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


ZH_LOCALIZERS = ["中", "裡", "里", "裏", "內", "上", "裡面", "當中", "之中", "裏頭"]


@pytest.mark.parametrize("localizer", ZH_LOCALIZERS)
@pytest.mark.parametrize(
    ("target", "noun"), [("影片", "歌"), ("電影", "音樂"), ("遊戲", "曲目")]
)
def test_a_localizer_still_makes_it_a_music_refusal(target, noun, localizer):
    """⚠️ 「不要播放影片中的歌」拒的是**音乐**（来源是视频），必须仍能取消。

    枚举的是**方位词**不是「中的/裡的」这类连接短语——短语是开集
    （裡面的/當中的/之中的/內的…列不完），方位词是汉语的封闭词类。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'不要播放{target}{localizer}的{noun}'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "text",
    ["不要播放唱歌的視頻", "不要播放唱歌的视频", "不要播放有歌曲的遊戲",
     "不要播放遊戲了我要聽歌", "不要播放這個影片裡唱歌的人", "不要播放這個影片"],
)
def test_a_music_noun_elsewhere_does_not_revive_the_video_target(text):
    """⚠️ 反向：不能退成「目标词之后整片搜音乐名词」。

    那样的话后半句出现的「歌」会把视频目标撤销，把一次「别放视频」变成
    取消音乐。方位词必须**连续**，中间夹一个非方位词立刻失配。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "verb", ["讨论", "討論", "研究", "推荐", "推薦", "分析", "介绍", "介紹"]
)
@pytest.mark.parametrize("noun", ["音乐", "音樂"])
def test_a_negator_governing_another_verb_is_not_a_playback_stop(verb, noun):
    """⚠️ 名词尾（音乐/音樂/歌）不像动词尾那样自带播放义。

    否定词和名词之间原本是 `.{0,6}` 的开窗口，里面塞进任何一个别的动词，
    否定词就改嫁给它了——`停止討論音樂` 于是把用户正在放的歌停了。
    「能改嫁的动词」是开集，所以枚举**补集**（合法的修饰成分）。

    ⚠️ 简体侧在基线上就是 True，这一条顺带把它修了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for negator in ('停止', '不要', '取消'):
        text = f'{negator}{verb}{noun}'
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "text",
    ["不要播放音樂", "不要播放音乐", "别放歌了", "別放歌了", "停止播放",
     "不要給我放歌", "別幫我播放音樂", "停止紅心歌單的音樂", "取消我喜歡的歌",
     "關掉背景音樂", "暫停一下音樂", "停止音樂", "關掉音樂", "不要音樂了"],
)
def test_the_modifier_closed_set_still_lets_real_refusals_through(text):
    """⚠️ 收紧名词尾那一支时最容易漏掉的是**来源名和「收藏」**。

    漏了它们，`取消我喜歡的歌` / `停止紅心歌單的音樂` 会当场翻 False——而且
    `取消收藏這首歌` 会在**前提**上就错（进不了否定分支），是不好查的那种失败。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


# ---------------------------------------------------------------------------
# 第十三轮：R1 的正向闭集后视枚举错了边，把主用例整片打死
# ---------------------------------------------------------------------------

SONG_TITLES = ["晴天", "稻香", "七里香", "凉凉", "涼涼", "平凡之路", "光年之外"]
ARTIST_NAMES = ["周杰伦", "周杰倫", "林俊杰", "林俊傑", "五月天", "邓紫棋", "鄧紫棋"]
SINGLE_CHAR_PLAY_VERBS = ["放", "播", "听", "聽"]


@pytest.mark.parametrize("negator", ["别", "別"])
@pytest.mark.parametrize("verb", SINGLE_CHAR_PLAY_VERBS + ["播放"])
@pytest.mark.parametrize("target", SONG_TITLES + ARTIST_NAMES)
def test_refusing_a_named_track_or_artist_cancels_playback(negator, verb, target):
    """⚠️⚠️ 这是这个功能**最主要的用法**，上一轮被我整片打死了。

    上一轮为了挡 `別放棄` / `別聽信` 加了一道**正向**闭集后视——播放动词后面
    必须紧跟句末 / 语气词 / 标点 / 音乐名词。但动词后面跟的是**歌名和歌手**，
    那是任意字符串：《晴天》《稻香》《七里香》里没有任何一个音乐名词，于是
    `别放晴天` / `别听周杰伦` 全变成 False（简体在基线上是 True）。

    枚举错了边。拿一个假阳性换来一整类假阴性，是坏交易。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'{negator}{verb}{target}'
    assert is_explicit_music_cancellation(text) is True, text


def _compound_tails():
    """从三张常量表里把黑名单字拆出来，逐字生成用例。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    return (
        [('放', ch) for ch in mr._ZH_NON_PLAYBACK_AFTER_FANG]
        + [('播', ch) for ch in mr._ZH_NON_PLAYBACK_AFTER_BO]
        + [('听', ch) for ch in mr._ZH_NON_PLAYBACK_AFTER_TING]
        + [('聽', ch) for ch in mr._ZH_NON_PLAYBACK_AFTER_TING]
    )


COMPOUND_TAILS = _compound_tails()


def test_the_compound_tables_are_not_empty():
    """⚠️ 用例是从常量表拆出来的。表被清空的话参数化会退化成空集，下面那条
    用例「全绿在没跑上」——所以先钉住规模。
    """  # noqa: DOCSTRING_CJK
    assert len(COMPOUND_TAILS) > 100


@pytest.mark.parametrize(("verb", "tail"), COMPOUND_TAILS)
def test_a_lexicalised_compound_is_not_a_playback_verb(verb, tail):
    """⚠️ 放 / 聽 / 播 本身是高频非播放义动词的词头。

    `別放棄` / `別放心上` / `別聽信` / `別播種` 都以播放动词字形开头，全都不是
    取消播放。要枚举的是「以这些字为首的**词汇化复合**的第二个字」——那是
    词典问题，有限；不是「别 后面能跟什么」也不是「播放动词后面能跟什么」，
    那两侧都是开集，前面两版分别栽在上面。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'别{verb}{tail}') is False
    assert is_explicit_music_cancellation(f'別{verb}{tail}') is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别听他的歌", "別聽他的歌"),
        ("别听她的音乐", "別聽她的音樂"),
        ("别听你的歌单", "別聽你的歌單"),
    ],
)
def test_a_pronoun_followed_by_a_music_noun_is_still_a_cancellation(
    simplified, traditional
):
    """⚠️ 「别听他的」不是取消播放，「别听他的歌」是。

    人称救援分支必须排在人称黑名单**前面**，否则 `別聽他的歌` 会被黑名单先吃掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


def test_the_hearsay_pronoun_set_never_contains_first_person():
    """⚠️⚠️ `_ZH_HEARSAY_PRONOUN` 里绝不能有「我」。

    `别听我喜欢的` 必须先命中 `_ZH_NEGATIVE_MUSIC`、再由
    `_excluded_personalization_source` 判成窄范围来源排除。把「我」收进人称表，
    窄排除会在**前提**上就失效——结果仍是 False，但机制错了，`别听我喜欢的，
    放日推` 会退化成什么都不做。这类「结果对了但先在前提上错」的失败最难查。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert '我' not in mr._ZH_HEARSAY_PRONOUN
    for text in ('别听我喜欢的', '別聽我喜歡的'):
        assert mr._ZH_NEGATIVE_MUSIC.search(text), f'{text} 没进否定分支'
        assert mr.is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别放鸽子", "別放鴿子"),
        ("别放我鸽子", "別放我鴿子"),
        ("别放你鸽子", "別放你鴿子"),
        ("别放弃", "別放棄"),
        ("别放手", "別放手"),
        ("别听信谣言", "別聽信謠言"),
        ("别播种太早", "別播種太早"),
        ("别听他的", "別聽他的"),
    ],
)
def test_specific_compounds_stay_pinned(simplified, traditional):
    """⚠️ 上面那条用例是从常量表拆出来的——**缩表会让它跟着缩水而不是变红**。

    实测过：把「鴿鸽」从表里删掉，参数化少两条，878 条照样全绿。所以高价值的
    几条必须另外钉死。同一个坑在这个文件里已经踩过一次（播放清單那组）。

    ⚠️ 「别放我鸽子」走的是另一条后视（人称+鸽），不是字符表——「我」不能进
    字符表，否则会打死 `别放我喜欢的`。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


# ---------------------------------------------------------------------------
# 第十四轮
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["我想停止播放？", "我要停止播放？", "請幫我停止播放？", "帮我停止播放？",
     "我要关掉音乐?", "给我暂停播放？"],
)
def test_a_bare_question_mark_is_also_interrogative(text):
    """⚠️ 裸问号挡不进那条语气词守卫——`_split_music_request_clauses` 会先把
    句末标点剥掉，正则拿到的子句是「我想停止播放」，根本看不到问号。

    所以这一条只能放在**入口**判，作用在未切分的原文上。两条机制互补：
    语气词在正则里（语气词不会被切分剥掉），裸问号在入口。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止他的红心歌单", "停止他的紅心歌單"),
        ("停止我们的红心歌单", "停止我們的紅心歌單"),
        ("停止您的红心歌单", "停止您的紅心歌單"),
        ("停止你们的歌单", "停止你們的歌單"),
        ("停止她的歌单", "停止她的歌單"),
    ],
)
def test_every_possessive_person_reaches_the_stop_target(simplified, traditional):
    """⚠️ 所有格要列全人称。只有 我的/你的 时，`停止他的紅心歌單` 判 False。

    人称是**封闭词类**，一次列干净。而且这张表必须在 `_ZH_MUSIC_NOUN_MODIFIER`
    和 `_ZH_DIRECT_MUSIC_STOP` 里保持一致，否则两条判据对同一句话给出不同答案。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要听信谣言", "不要聽信謠言"),
        ("无需听从他的安排", "無需聽從他的安排"),
        ("不想听取意见", "不想聽取意見"),
        ("不要放弃", "不要放棄"),
        ("停止放松", "停止放鬆"),
        ("不要播种", "不要播種"),
    ],
)
def test_the_compound_guard_also_covers_the_multi_char_negator(
    simplified, traditional
):
    """⚠️ 三张复合词表只挂在「别」那一支是不够的。

    `不要` / `無需` / `停止` 这些多字否定词后面同样接单字播放动词，
    `不要聽信謠言` 会走那一支。简体侧 base 就是 True，这条顺带一起修了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("取消音乐节的行程", "取消音樂節的行程"),
        ("停止音乐课", "停止音樂課"),
        ("不要音乐理论", "不要音樂理論"),
        ("取消音乐比赛", "取消音樂比賽"),
    ],
)
def test_a_music_noun_must_be_a_complete_target(simplified, traditional):
    """⚠️ 音樂 / 歌 可以是更长复合词的**词头**：音樂節 / 音樂課 / 音樂理論。

    名词尾那一支原本不校验后面跟什么，于是活动、课程、理论全被当成播放对象。
    「音樂X 能组成什么词」是开集，所以正向要求右边界。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "text",
    ["停止音乐", "停止音樂", "关掉音乐", "關掉音樂", "不要音乐了", "不要音樂了",
     "关掉音乐吗", "關掉音樂嗎", "暂停一下音乐", "暫停一下音樂", "关掉背景音乐",
     "停止这个红心歌单", "停止這個紅心歌單", "停止红心歌单的音乐", "停止紅心歌單的音樂"],
)
def test_the_music_noun_boundary_does_not_over_tighten(text):
    """⚠️ 右边界收得太紧会切断 `歌單`——`歌` 匹配后卡在 `單` 上。

    音乐名词自身的后缀（单/單/曲/目）要先吃完再判边界；语气词也要收进来，
    `关掉音乐吗` 在基线上是 True，不能被这道边界顺手改掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "pronoun", ["我", "你", "妳", "您", "他", "她", "它", "牠",
                "我们", "我們", "你们", "你們", "他们", "他們", "她们", "她們"],
)
def test_a_pronoun_never_becomes_an_artist_search(pronoun):
    """⚠️ 人称是封闭词类，简繁都要列全。漏了繁体的 妳 / 你們 / 您，
    `來一首妳的歌` 会去搜一个名叫「妳」的歌手。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    result = parse_explicit_user_music_request(f'来一首{pronoun}的歌')
    assert not getattr(result, 'song_artist', None), pronoun


@pytest.mark.parametrize("artist", ["周杰伦", "周杰倫", "五月天", "邓紫棋", "鄧紫棋"])
def test_a_real_artist_is_still_searchable(artist):
    """反向：人称表不能宽到把真歌手也挡掉。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    result = parse_explicit_user_music_request(f'来一首{artist}的歌')
    assert getattr(result, 'song_artist', None) == artist


def test_named_targets_do_not_collide_with_the_compound_tables():
    """⚠️ 两组用例断言**相反**的结果，靠首字不重合来共存。

    `test_refusing_a_named_track_or_artist_cancels_playback` 要 True，
    `test_a_lexicalised_compound_is_not_a_playback_verb` 要 False。哪天有人往
    复合词表里加一个常用字、而它正好是某条歌名的首字，前一组会整片打红，
    排查时看不出根因。让冲突在源头就报出来。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    blacklist = (
        set(mr._ZH_NON_PLAYBACK_AFTER_FANG)
        | set(mr._ZH_NON_PLAYBACK_AFTER_BO)
        | set(mr._ZH_NON_PLAYBACK_AFTER_TING)
    )
    collisions = [
        name for name in SONG_TITLES + ARTIST_NAMES if name[0] in blacklist
    ]
    assert collisions == [], (
        f'这些用例的首字落进了复合词黑名单，两组断言会互相打架: {collisions}'
    )


@pytest.mark.parametrize(
    "quantifier", ["每", "整", "下一", "上一", "这", "這", "那一", "一"]
)
def test_a_track_quantifier_does_not_block_the_stop(quantifier):
    """⚠️ 名词尾那个闭集窗口漏了量词/选择词。

    `停止每首歌` / `停止整首歌` / `停止下一首歌` 在基线上都是 True，闭集少列
    几个字就把它们打成 False——收紧一个开窗口时最容易漏的就是这类高频虚成分。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{quantifier}首歌') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("我想先听一下，别播放音乐了，好吗？", "我想先聽一下，別播放音樂了，好嗎？"),
        ("我要先看看，别放歌了，可以吗？", "我要先看看，別放歌了，可以嗎？"),
    ],
)
def test_a_trailing_question_does_not_kill_an_earlier_stop_clause(
    simplified, traditional
):
    """⚠️ 裸问号守卫作用在**未切分**的整句上，所以必须限定在单子句内。

    允许跨子句的话，`我想先听一下，别播放音乐了，好吗？` 会被整句否掉——
    里面那个明确的取消子句就丢了（base 是 True）。这是「入口守卫」这种做法
    自带的风险：它绕过了子句切分，就得自己负责不越界。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "text", ["聽一下您說話的聲音", "听一下您说话的声音", "聽一下牠說話的聲音"]
)
def test_an_honorific_speech_subject_is_not_an_artist(text):
    """人称是封闭词类，简繁都要列全。漏了敬语「您」，这句会变成搜歌手
    「您說話」的歌「聲音」。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    result = parse_explicit_user_music_request(text)
    assert not getattr(result, 'song_artist', None), text


# ⚠️ 这里原本有一条 `test_listening_to_a_person_is_not_playback`，断言
# `別聽一下老師的意見` 不是取消播放。它已被**刻意删除**：实现那一侧靠的是把
# 「一」收进 _ZH_NON_PLAYBACK_AFTER_TING，而那违反了那张表自己的准入条件 (c)
# 「X 不是高频歌名首字」——代价是 `别听一剪梅` / `别听一生所爱` / `别听一路向北`
# 整类被打死（Codex P2）。两害相权，保住歌名。
#
# 现在 `別聽一下老師的意見` 会被误判成取消播放。那是简体侧既有的缺陷
# （`别听一下老师的意见` 在 base 上就是 True），繁体与简体一致，不是新引入。
# 见下面 test_a_song_name_starting_with_a_compound_char_still_cancels。


@pytest.mark.parametrize(
    "text", ["聽一下我的健身播放清單", "听一下我的健身播放列表", "播放我的健身播放清单"],
)
def test_the_taiwanese_playlist_noun_is_a_playlist(text):
    """台湾说「播放清單」、大陆也说「播放列表」。缺了它们，这句会去搜歌手
    「我」的歌「健身播放清單」。这几个词在 `_ZH_PLAYBACK_COMPOUND_NOUN` 里
    已经作为「播放不是动词」的证据枚举过一次了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    result = parse_explicit_user_music_request(text)
    assert getattr(result, 'playlist_name', None) == '健身', f'{text} -> {result}'


@pytest.mark.parametrize("artist", ["周杰倫", "周杰伦", "五月天"])
def test_the_speech_subject_guard_does_not_block_a_real_artist(artist):
    """⚠️ 前提守卫：上面那条敬语「您」的用例断言的是「不是歌手」，而
    `result is None` 时它同样通过。

    所以需要证明 `聽一下X的歌` 这个句式**确实会**走到歌手解析——否则那条
    用例测的就不是「敬语在人称表里」，而是「这句压根没进解析分支」。
    隔壁 `来一首{artist}的歌` 那组有同样的兜底，这组之前漏了（CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    result = parse_explicit_user_music_request(f'聽一下{artist}的歌')
    assert getattr(result, 'song_artist', None) == artist


def _playback_adverbs():
    """从共用常量拆出副词闭集。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    inner = mr._ZH_PLAYBACK_ADVERB.split('(?:', 1)[1].split(')', 1)[0]
    return [w for w in inner.split('|') if w]


PLAYBACK_ADVERBS = _playback_adverbs()


def test_the_adverb_table_is_not_empty():
    assert len(PLAYBACK_ADVERBS) >= 12, PLAYBACK_ADVERBS


@pytest.mark.parametrize("adverb", PLAYBACK_ADVERBS)
@pytest.mark.parametrize("negator", ["别", "別"])
def test_an_adverb_between_the_negator_and_the_verb(negator, adverb):
    """⚠️ 「别」和播放动词之间不止能塞「再」。

    `别继续播放` / `别现在播放` / `别马上放晴天` 在基线上都是 True，只允许
    「再」把它们整类打成 False。

    ⚠️ 这张表与名词尾那一支**共用同一个常量**——两处漂开就会出现「同一句话
    两条判据给不同答案」，这个文件已经因为前缀漂开踩过两次坑。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'{negator}{adverb}播放') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止网易云音乐", "停止網易雲音樂"),
        ("关掉网易云音乐", "關掉網易雲音樂"),
        ("停止目前的音乐", "停止目前的音樂"),
        ("停止当前的音乐", "停止當前的音樂"),
    ],
)
def test_a_service_or_time_qualifier_still_stops_playback(simplified, traditional):
    """服务名和时间限定词也在名词尾的闭集里——收紧开窗口时最容易漏的就是
    这类高频限定成分，这已经是同一处的第三批漏项（前两批是量词和所有格）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别继续播放", "別繼續播放"),
        ("别现在播放", "別現在播放"),
        ("别马上放晴天", "別馬上放晴天"),
        ("别一直放歌", "別一直放歌"),
        ("别再放了", "別再放了"),
    ],
)
def test_specific_adverbs_stay_pinned(simplified, traditional):
    """⚠️ 上面那条用例是从常量拆出来的——**删词会让它跟着缩水而不是变红**。

    实测：把「繼續|继续」从副词表里删掉，参数化少两条，1015 条照样全绿。
    这个坑在这个文件里已经是第三次了（前两次是播放清單那组、复合词那组），
    所以高价值的几条必须另外钉死。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "text", ["停止QQ音乐", "关掉QQ音乐", "取消QQ音樂", "停止qq音乐", "停止Qq音乐"],
)
def test_a_branded_service_name_matches_either_case(text):
    """⚠️ 服务名的品牌写法是大写 QQ，而这条正则是大小写敏感的。

    闭集里塞小写 `qq` 只覆盖了没人会打的那种写法——加词进闭集时要连大小写
    一起想，这跟「加词要连简繁孪生一起想」是同一类疏漏。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("别听一剪梅", "別聽一剪梅"),
        ("别听一生所爱", "別聽一生所愛"),
        ("别听一路向北", "別聽一路向北"),
        ("别听任贤齐", "別聽任賢齊"),
        ("别放一千个伤心的理由", "別放一千個傷心的理由"),
    ],
)
def test_a_song_name_starting_with_a_compound_char_still_cancels(
    simplified, traditional
):
    """⚠️⚠️ 复合词黑名单的准入条件 (c) 是「X 不是高频歌名首字」。

    我为了挡 `別聽一下老師的意見` 把「一」收了进去，直接违反了自己写下的规则——
    一剪梅 / 一生所愛 / 一路向北 都是高频歌名，任賢齊 是知名歌手。拉黑它们的
    首字等于把这个功能最主要的用法打死，跟之前 `别放晴天` 那次是同一个错误。

    代价：`別聽一下老師的意見` 会被误判成取消播放。那是简体侧既有的缺陷
    （`别听一下老师的意见` 在 base 上就是 True），繁体与简体一致。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("算了我想停止播放吗？", "算了我想停止播放嗎？"),
        ("还是算了我想停止播放吗？", "還是算了我想停止播放嗎？"),
        ("算了我要停止播放？", "算了我要停止播放？"),
    ],
)
def test_a_changed_mind_preface_does_not_defeat_the_question_guard(
    simplified, traditional
):
    """⚠️ 两条疑问守卫都锚在 `^` 上，却没允许「算了」引导语——而真正的取消
    正则是在守卫**之后**才消费引导语的。于是加个「算了」就绕过去了。

    锚定守卫和它保护的正则必须消费同样的前缀，否则中间那段就是个缺口。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "title", ["影片", "電影", "動畫", "遊戲", "視頻", "晴天"]
)
def test_a_quoted_title_is_a_song_not_a_video_target(title):
    """⚠️ 书名号里的内容是**歌名**。同一个模块的引用式请求分支会把
    `播放《影片》` 解析成 song_name='影片'，非音乐目标检查却把同一个词当成
    视频目标、把明确取消压掉（base 是 True）。

    成对符号是闭集，扫描前先把括起来的片段挖掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'不要播放《{title}》') is True


@pytest.mark.parametrize("style", ["電音", "獨立", "環境音", "說唱", "輕音樂"])
def test_a_traditional_style_keyword_still_expands(style):
    """⚠️ 路由关键词表已经补了繁体，曲风扩展表还是简体——`來點電音的歌` 能
    选中 indie 分支，却只带着未翻译的原词去搜 Bandcamp/SoundCloud，
    常常 track_not_found（Codex P2）。

    按繁→简折叠补出繁体键指向同一份扩展词，而不是手抄——上面那张表会长，
    手抄必然落后。
    """  # noqa: DOCSTRING_CJK
    from utils.music_crawlers import expand_style_keyword

    assert len(expand_style_keyword(style)) > 1, style


@pytest.mark.parametrize(
    "place", ["手机", "手機", "车", "車", "电脑", "電腦", "客厅", "客廳", "耳机"]
)
@pytest.mark.parametrize("localizer", ["里", "裡", "上"])
def test_a_location_qualified_music_object_still_stops(place, localizer):
    """⚠️ 设备/地点是**开集**（手机/车/电脑/客厅/耳机…），不能枚举。

    但它们的结构是闭的：`X里的` / `X上的`——方位词是汉语的封闭词类。这跟
    `_ZH_MUSIC_NOUN_AFTER_TARGET` 用的是同一招：枚举那个能枚举干净的维度。

    ⚠️ 这已经是同一处闭集的**第四批**漏项（量词、所有格、服务名之后）。
    每次都是「收紧一个开窗口时漏掉高频虚成分」——所以这次改成结构而非清单。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{place}{localizer}的音乐') is True


@pytest.mark.parametrize(
    ("opening", "closing"), [("《", "》"), ("“", "”"), ("‘", "’"), ('"', '"')]
)
def test_every_quote_pair_shields_a_title(opening, closing):
    """⚠️ 同一个模块的 `_QUOTE_PAIRS` 认得弯引号，引用片段正则却不认——
    「同一张表在一处认得、另一处不认得」在这个 PR 里已经是第三次了
    （前两次是 播放清單、简繁孪生）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'不要播放{opening}影片{closing}'
    ) is True


@pytest.mark.parametrize("marker", ["是否可以", "是否合适", "能否停下", "可否暂停"])
@pytest.mark.parametrize("prefix", ["我想", "我要", "帮我", "幫我"])
def test_a_shifou_question_is_not_a_command(prefix, marker):
    """⚠️ 汉语的是非问不止靠句末语气词——「是否/能否/可否」在句中就已经标记了
    疑问。守卫只认句末 吗/嗎/呢 和裸问号，这一族整类漏掉。这些词是封闭类。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'{prefix}停止播放{marker}') is False


@pytest.mark.parametrize(
    "compound",
    ["主題曲", "主题曲", "插曲", "片頭曲", "配樂", "配乐", "原聲", "背景音樂", "背景音乐"],
)
def test_a_soundtrack_compound_is_a_music_target(compound):
    """⚠️ 影视配乐类复合词也是音乐名词。`不要播放影片的主題曲` 拒的是**音乐**，
    只认通用名词（歌/音樂/曲）会把它判成视频目标（base 是 True）。

    这一族是可枚举的：主題曲/插曲/片頭曲/片尾曲/配樂/原聲/背景音樂。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'不要播放影片的{compound}') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [("停止红心歌单吗", "停止紅心歌單嗎"), ("取消我喜欢的歌吗", "取消我喜歡的歌嗎")],
)
def test_the_two_predicates_share_one_particle_table(simplified, traditional):
    """⚠️⚠️ 语气词表必须与 `_ZH_NEGATIVE_MUSIC` 那一支**同一套**。

    少了 吗/嗎，`停止紅心歌單嗎` 会被否定判据认下、却被直接停止判据拒掉，
    于是降级成窄范围来源排除、音乐继续放。

    **两条判据漂开在这个文件里已经是第四次了**（前三次是共享前缀、疑问守卫、
    引号对）。每次都是同一个形状：同一件事的两个方面各自维护一份表。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


# ---------------------------------------------------------------------------
# 第十二轮：#2655 合并后 Codex 核实成立、当时没修的两条（base 均为 False）
# ---------------------------------------------------------------------------


QUESTION_GUARD_PREFIXES = ["我想", "我要", "帮我", "幫我", "给我", "給我"]


def _a_not_a_tails() -> list[str]:
    from main_logic import music_requests as mr

    return _alternation(mr._ZH_A_NOT_A_QUESTION_TAIL)


A_NOT_A_TAILS = _a_not_a_tails()


def test_the_a_not_a_tail_table_is_derived_not_transcribed():
    """⚠️ 表是从常量拆出来的，拆法一旦失效下面的笛卡尔积会静默缩水。

    断言**相等**：下界断言放不住「删掉一个词」，而删掉一个词就是放一族疑问句
    去当命令。往常量里加词时必须同步改这里——刻意的摩擦。
    """  # noqa: DOCSTRING_CJK
    assert set(A_NOT_A_TAILS) == {
        "可不可以", "能不能", "行不行", "好不好", "是不是", "对不对", "對不對",
    }, A_NOT_A_TAILS


@pytest.mark.parametrize("tail", A_NOT_A_TAILS)
@pytest.mark.parametrize("prefix", QUESTION_GUARD_PREFIXES)
def test_an_a_not_a_tail_is_not_a_command(prefix, tail):
    """⚠️ A-not-A 尾（可不可以/行不行/好不好）跟 是否/能否/可否 是同一族疑问
    标记，守卫上一版只收了后者，于是 `我想停止播放可不可以` 被当成命令、当场把
    用户的歌停掉（Codex P2，base 是 False）。

    ⚠️ 配对的正向断言在同一个参数上跑：去掉疑问尾的**同一句话**必须仍然是命令。
    没有它，判据整个失效（永远返回 False）时这条也是绿的。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'{prefix}停止播放{tail}') is False
    assert is_explicit_music_cancellation(f'{prefix}停止播放') is True


# ⚠️ 四条播放动词分支**逐条**过一遍。守卫只挂在「播放」那条是没用的：
# `我要停止播放的代碼` 里 `.{0,6}` 会把「播」吃掉再由单字 `放` 命中，或者由单字
# `播` 命中（它后面跟的是「放」，看不到那个「的」）。所以这里既逐条挂守卫，也
# 要求「播放」是完整的词（`播(?!放)` / `(?<!播)放`）。
PLAYBACK_VERBS = ["播放", "放", "播", "听", "聽"]


@pytest.mark.parametrize("verb", PLAYBACK_VERBS)
def test_a_nominalized_playback_verb_is_not_a_command(verb):
    """⚠️ 「停止播放」后面紧跟「的」时它是**名词性成分的词头**，不是命令。

    `我要停止播放的代碼` / `我想停止播放的教程` 问的是代码和教程，却被判成取消
    播放、把歌停掉（Codex P2，base 是 False）。

    ⚠️ 配对正向断言同参数：换成音乐名词的**同一句话**必须仍然是命令。缺了它，
    这条在「整条判据失效」时照样绿。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'我要停止{verb}的代码') is False
    assert is_explicit_music_cancellation(f'我要停止{verb}的教程') is False
    assert is_explicit_music_cancellation(f'我要停止{verb}音乐') is True


def _playback_ui_nouns() -> list[str]:
    from main_logic import music_requests as mr

    return _alternation(mr._ZH_PLAYBACK_UI_NOUN)


PLAYBACK_UI_NOUNS = _playback_ui_nouns()


def test_the_ui_noun_table_is_derived_not_transcribed():
    assert set(PLAYBACK_UI_NOUNS) == {
        "按钮", "按鈕", "功能", "键", "鍵", "控件", "組件", "组件",
    }, PLAYBACK_UI_NOUNS


@pytest.mark.parametrize("ui_noun", PLAYBACK_UI_NOUNS)
@pytest.mark.parametrize("stop_verb", ["停止", "关掉", "關掉", "取消"])
def test_a_playback_ui_control_is_not_a_command(stop_verb, ui_noun):
    """⚠️ `幫我停止播放按鈕換個顏色` 说的是界面控件，不是要停歌（base 是 False）。

    「播放X」这类界面控件名是小闭集，跟「播放后面能跟什么」（歌名歌手，开集）
    不是一回事——后者这个文件已经栽过两次，不再去枚举。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'帮我{stop_verb}播放{ui_noun}换个颜色'
    ) is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        # 「的」后面是音乐名词 → 仍是命令（这才是「停止正在播放的音乐」）。
        ("停止正在播放的音乐", "停止正在播放的音樂"),
        ("暂停正在播放的歌", "暫停正在播放的歌"),
        ("取消正在播放的歌曲", "取消正在播放的歌曲"),
        ("停止播放的红心歌单", "停止播放的紅心歌單"),
        # 「的」是补语标记「得」的误写 → 仍是命令。
        ("不要放的太大声", "不要放的太大聲"),
        ("不要放的很大声", "不要放的很大聲"),
    ],
)
def test_a_music_head_after_de_is_still_a_command(simplified, traditional):
    """⚠️ 反向用例：名物化守卫不能一刀切拒绝所有「播放 + 的」。

    没有这一条，把守卫写成裸 `(?!的)` 也是绿的——那会把 `停止正在播放的音樂`
    这类最自然的说法（base 是 True）整片打成不取消。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


# --- Codex 在本 PR 上评审出的三条（两条在这个文件里）--------------------------


def _quote_pairs() -> list[tuple[str, str]]:
    from main_logic import music_requests as mr

    pairs = list(mr._QUOTE_PAIRS.items())
    assert pairs, "_QUOTE_PAIRS 是空的"
    return pairs


QUOTE_PAIRS = _quote_pairs()
# 《好不好》《是不是》《好嗎》都是真实歌名。前三个来自 A-not-A 表，后面几个是
# 守卫里原有的疑问标记——这个洞在它们身上是**既有的**，一起收口。
QUESTION_MARKERS = A_NOT_A_TAILS + ["好吗", "好嗎", "是否", "能否", "可否"]


@pytest.mark.parametrize(("opening", "closing"), QUOTE_PAIRS)
@pytest.mark.parametrize("marker", QUESTION_MARKERS)
def test_a_question_marker_inside_a_title_is_still_a_command(marker, opening, closing):
    """⚠️ 疑问标记落在书名号/引号里时那是**歌名**，不是在提问。

    `帮我停止播放《好不好》` 会被读成「用户在问」，歌停不下来（Codex P2）。
    闭合符号从 `_QUOTE_PAIRS` 取，跟引用式点歌解析同一张表。

    ⚠️ 配对反向断言：**同一个标记不带引号时仍然是疑问**，否则把守卫整个删掉
    这条也是绿的。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'帮我停止播放{opening}{marker}{closing}'
    ) is True
    assert is_explicit_music_cancellation(f'帮我停止播放{marker}') is False


# ⚠️ 音频对象不止歌和歌单。这几个词都在 `_ZH_MUSIC_HEAD_AFTER_DE` 里，下面先断言
# 「确实在表里」（表被缩掉就红），再断言行为——这张表是**逃生**用的，往里加词只
# 会恢复基线行为，所以不做相等断言，只钉住不许减。
AUDIO_HEAD_NOUNS = [
    "声音", "聲音", "音效", "音轨", "音軌", "旋律", "伴奏", "曲子",
    "铃声", "鈴聲", "BGM", "bgm", "音乐", "音樂", "歌", "歌单", "歌單",
]


@pytest.mark.parametrize("noun", AUDIO_HEAD_NOUNS)
def test_an_audio_object_after_de_is_still_a_command(noun):
    """⚠️ `停止正在播放的聲音` / `停止播放的音效` 是明确的停止命令（base 是 True）。

    名物化守卫只放行歌/歌单时，这一族全被打成「不是命令」，歌停不下来
    （Codex P2）。
    """  # noqa: DOCSTRING_CJK
    import re as _re

    from main_logic import music_requests as mr

    assert _re.compile(mr._ZH_MUSIC_HEAD_AFTER_DE).match(noun), (
        f'{noun} 不在 _ZH_MUSIC_HEAD_AFTER_DE 里，下面的断言只是碰巧绿'
    )
    assert mr.is_explicit_music_cancellation(f'停止正在播放的{noun}') is True
    assert mr.is_explicit_music_cancellation(f'停止播放的{noun}') is True


# --- Codex 第二轮：七条边界（全部 base=True/False 与我这一版不一致）----------


@pytest.mark.parametrize("marker", QUESTION_MARKERS)
def test_a_quote_after_the_marker_does_not_disable_the_guard(marker):
    """⚠️⚠️ 判据是「标记**在不在**引号里」，不是「后面有没有闭合引号」。

    第一版写成后者，于是 `我想停止播放是否会影响《原神》` 里一个跟标记无关的
    书名号把整道守卫关掉，一句提问被当成停止命令（Codex P2，base 是 False）。
    这是**危险方向**的误判：用户在问，歌被停了。

    两种形状都必须仍然判成提问：标记后面出现引用、标记前面有**完整闭合**的引用。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}会影响《原神》'
    ) is False
    assert is_explicit_music_cancellation(
        f'我想停止播放《晴天》{marker}'
    ) is False


def _soundtrack_nouns() -> list[str]:
    from main_logic import music_requests as mr

    return _alternation(mr._ZH_SOUNDTRACK_NOUN)


SOUNDTRACK_NOUNS = _soundtrack_nouns()


def test_the_soundtrack_table_has_exactly_one_definition():
    """⚠️ 配乐类词表原本内联在 `_ZH_MUSIC_NOUN_AFTER_TARGET` 里，名物化守卫也要
    用同一族词。提成常量而不是复制——这个文件已经因为「同一张表两处各写一份、
    然后漂开」栽过四次。这里钉住「两处确实用的是同一个常量」。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert mr._ZH_SOUNDTRACK_NOUN in mr._ZH_MUSIC_NOUN_AFTER_TARGET.pattern
    assert mr._ZH_SOUNDTRACK_NOUN in mr._ZH_MUSIC_HEAD_AFTER_DE
    assert len(SOUNDTRACK_NOUNS) >= 12, SOUNDTRACK_NOUNS


@pytest.mark.parametrize("noun", SOUNDTRACK_NOUNS)
def test_a_soundtrack_noun_after_de_is_still_a_command(noun):
    """`停止正在播放的配樂` base 是 True，只列歌/歌单会把这一族打成名物化。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{noun}') is True


@pytest.mark.parametrize(
    "determiner", ["这首", "這首", "下一首", "上一首", "那首", "这个", "這個", "我的"]
)
def test_a_determiner_before_the_music_head_is_still_a_command(determiner):
    """⚠️ 「的」后面要求音乐名词**紧贴**是收得太死了。

    `停止正在播放的這首歌` / `停止正在播放的下一首歌` base 都是 True，中间那个
    限定词是 `_ZH_MUSIC_NOUN_MODIFIER` 里已经列过四批的闭集，直接复用它，而不是
    再写第三张同族的表（Codex P2）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{determiner}歌') is True


def _degree_words() -> list[str]:
    from main_logic import music_requests as mr

    return _alternation(mr._ZH_DEGREE_AFTER_DE)


DEGREE_WORDS = _degree_words()


def test_the_degree_table_is_derived_not_transcribed():
    """⚠️ 相等断言：这张表少一个词就是一句「不要放的X大声」被判成名物化。"""  # noqa: DOCSTRING_CJK
    assert set(DEGREE_WORDS) == {
        "太", "很", "最", "更", "挺", "真", "非常", "特别", "特別",
        "超级", "超級", "这么", "這麼", "那么", "那麼", "有点", "有點",
        "大声", "大聲", "小声", "小聲",
    }, DEGREE_WORDS


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要放的超级大声", "不要放的超級大聲"),
        ("不要放的这么大声", "不要放的這麼大聲"),
        ("不要放的那么大声", "不要放的那麼大聲"),
        ("不要放的有点大声", "不要放的有點大聲"),
        ("不要放的非常大声", "不要放的非常大聲"),
    ],
)
def test_a_multi_char_degree_complement_is_still_a_command(simplified, traditional):
    """⚠️ 用户把补语标记「得」写成「的」是高频误写，base 全是 True。

    只收单音节程度副词时，`不要放的超級大聲` / `不要放的這麼大聲` 全被判成
    名物化（Codex P2）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("请停止播放的同时关闭屏幕", "請停止播放的同時關閉螢幕"),
        ("停止播放的同时把灯关了", "停止播放的同時把燈關了"),
    ],
)
def test_the_coordination_construction_is_still_a_command(simplified, traditional):
    """⚠️ 「V 的同时 W」里的「的」既不是名物化标记也不是补语标记（base 是 True）。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [("帮我停止播放功能音乐", "幫我停止播放功能音樂")],
)
def test_a_ui_noun_prefixing_a_music_word_is_still_a_command(simplified, traditional):
    """⚠️ 界面控件表是**前缀匹配**：`功能音樂` 里的「功能」是词头不是控件名。

    base 是 True，被前缀匹配打成 False（Codex P2）。要求控件名后面**不是**
    音乐名词就能分开，两侧都是已有的闭集。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    # 配对反向：控件名后面不是音乐名词时，仍然不是命令。
    for text in ("帮我停止播放功能吧", "幫我停止播放功能吧"):
        assert is_explicit_music_cancellation(text) is False, text
