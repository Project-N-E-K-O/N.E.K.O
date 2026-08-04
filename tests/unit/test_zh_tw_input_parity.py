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


def _music_frames() -> list[str]:
    """任指/条件/让步/认知框架词从**实现侧**取，不手抄。

    ⚠️ 手抄那一版漏了简体的 `忘记`（实现侧当时也没收），
    是 CodeRabbit 对着实现表核出来的。两边同源之后这一类漏项不会再发生。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import _ZH_NON_INTERROGATIVE_FRAMES

    table = list(_ZH_NON_INTERROGATIVE_FRAMES)
    assert table and len(table) == len(set(table)), table
    return table


NON_INTERROGATIVE_FRAMES = _music_frames()


def test_the_frame_table_is_derived_not_transcribed():
    """⚠️ 派生的盲点是「改常量=改测试」：实现侧删一个词，上面那些笛卡尔积
    跟着缩水、照样全绿——变异「删掉简体忘记」当场 SURVIVED。所以钉相等。

    ⚠️ 同时钉住**简繁成对**：这个 PR 就是为繁体对等性开的，实现里只收一半
    （当时 `忘記` 有、`忘记` 没有）正是这类缺陷的典型形状。
    """  # noqa: DOCSTRING_CJK
    assert set(NON_INTERROGATIVE_FRAMES) == {
        "无论", "無論", "不论", "不論", "不管", "任凭", "任憑", "随便", "隨便",
        "如果", "假如", "若是", "要是", "倘若", "万一", "萬一", "假若", "设若", "設若",
        "即使", "即便", "就算", "哪怕", "纵使", "縱使", "就是",
        "不知道", "不记得", "不記得", "忘了", "忘记", "忘記",
        "不清楚", "不确定", "不確定", "没注意", "沒注意",
        # ⚠️ **肯定**的认知谓词一样管着宾语从句（Codex P2 第五十八轮）。
        "知道", "曉得", "晓得", "记得", "記得", "清楚", "确定", "確定",
    }
    for simplified, traditional in (
        ("无论", "無論"), ("不论", "不論"), ("任凭", "任憑"), ("随便", "隨便"),
        ("万一", "萬一"), ("设若", "設若"), ("纵使", "縱使"),
        ("不记得", "不記得"), ("忘记", "忘記"),
        ("不确定", "不確定"), ("没注意", "沒注意"),
    ):
        assert simplified in NON_INTERROGATIVE_FRAMES, simplified
        assert traditional in NON_INTERROGATIVE_FRAMES, traditional



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
    # ⚠️ /clear 的触发词不在这里：它不可逆地清掉上游会话上下文，判据又还是自由文本
    # 子串，所以刻意保持简体。见 test_clear_triggers_stay_simplified_only。
    ("换个话题", "換個話題"),
    ("说点别的", "說點別的"),
    ("重新开始", "重新開始"),
    # 台湾用「搜尋」，所以这一条不是「搜索」的字形转换。
    ("停止搜索", "停止搜尋"),
    ("取消这个任务", "取消這個任務"),
    ("停下来", "停下來"),
    ("没问题", "沒問題"),
    # approve 的子串支已改成整子句白名单，繁体随之补齐——整子句判据下补繁体不再
    # 放大暴露面。见 test_whole_clause_whitelist_kills_free_text_misfires。
    ("去执行", "去執行"),
    ("去执行吧", "去執行吧"),
    ("删吧", "刪吧"),
    ("准了", "準了"),
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
        # /clear — irreversibly wipes the upstream QwenPaw session context
        "忘了剛才的事", "清空聊天記錄", "清除聊天記錄", "刪掉剛才的記錄",
        "我想知道如何清除聊天記錄",
    ],
)
def test_clear_triggers_stay_simplified_only(text):
    """⚠️ Deliberate gap: ``/clear``'s triggers are NOT backfilled to Traditional.

    ``/clear`` is the one command still judged by *substring containment over
    free text*, and it irreversibly wipes the upstream session context. A plain
    question — 「我想知道如何清除聊天记录」 — already returns ``/clear`` on the
    Simplified side; adding Traditional triggers would double the exposure of
    that pre-existing hole.

    ``/daemon approve``, ``/stop`` and ``/new`` are no longer in this list: they
    moved to the whole-clause whitelist, where a Traditional entry cannot fire
    from inside an unrelated sentence, so backfilling them is safe. ``/clear``
    can follow the same route, but that was scoped out of this change
    deliberately rather than smuggled in.

    Traditional users can still reach it by typing the literal magic word
    ``/clear`` (whole-string match in ``normalize_magic_command``).

    ⚠️ Do NOT lean on "the LLM classifier still runs after a None" — that is not
    unconditional. See test_a_rule_miss_can_skip_the_llm_classifier_entirely.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_a_rule_miss_can_skip_the_llm_classifier_entirely():
    """⚠️ Pins that the rule table is NOT merely a zero-LLM fast path.

    ``rule_magic_command`` is also the only OpenClaw signal in
    ``_deterministic_action_signal``, and the cheap pre-gate in
    ``_analyze_and_execute_inner`` returns None on
    ``external_intent < threshold and not _deterministic_action_signal(...)`` —
    which sits BEFORE ``classify_magic_intent``. So on a low-external-intent turn
    a rule miss means the LLM classifier is never reached, and narrowing the
    table costs real recall rather than one extra assessment.

    Asserted structurally (the gate ordering in the source) plus behaviourally
    (the signal really does flip with the table).
    """  # noqa: DOCSTRING_CJK
    import inspect

    from brain.openclaw_adapter import OpenClawAdapter
    from brain.task_executor import DirectTaskExecutor

    source = inspect.getsource(DirectTaskExecutor._analyze_and_execute_inner)
    gate_at = source.index("_deterministic_action_signal")
    llm_at = source.index("classify_magic_intent")
    assert gate_at < llm_at, "前置闸不再位于 LLM magic 分类器之前，这条测试的前提变了"

    executor = object.__new__(DirectTaskExecutor)
    executor.plugin_list = []
    signal = executor._deterministic_action_signal
    # 表内 → 刹车豁免；表外 → 不豁免（低 external_intent 时整轮被跳过）
    assert signal("停下来", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert OpenClawAdapter.rule_magic_command("我准了假") is None
    assert signal("我准了假", openclaw_enabled=True, user_plugin_enabled=False) is False


@pytest.mark.parametrize(
    "text",
    [
        # A question *about* restarting is not a request to restart. Both scripts.
        "這個遊戲要怎麼重新開始？", "这个游戏要怎么重新开始？",
    ],
)
def test_question_about_a_command_no_longer_triggers_it(text):
    """Was recorded-not-fixed while the judgement was substring containment;
    the whole-clause whitelist fixes it in both scripts at once.

    The clause is 這個遊戲要怎麼重新開始 — not a whitelist entry — so it no
    longer dispatches. This IS a Simplified behaviour change, and a deliberate
    one: the prior test docstring called it out as "narrowing it is a separate,
    script-neutral change", which is exactly what this is.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_traditional_can_still_approve_by_whole_sentence():
    """Bare affirmations stay a valid approval in both scripts.

    ⚠️ These four are still an unconditional approve at the classifier layer and
    a whole-clause whitelist cannot narrow them — they ARE whole clauses. What
    stops a stray 「没问题」 from approving something is the dispatch-side live
    task gate; see test_approve_is_dropped_without_a_live_openclaw_task.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    for text in ("沒問題", "同意", "我同意", "没问题"):
        assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


def test_legitimate_approvals_survive_the_whole_clause_switch():
    """Every approval spelling that worked under substring containment, plus the
    Traditional counterparts the substring table never had."""
    from brain.openclaw_adapter import OpenClawAdapter

    approve = "/daemon approve"
    for text in (
        # were already approving on the Simplified side
        "去执行吧", "删吧", "准了", "没问题，去执行", "没问题去执行",
        # Traditional, newly reachable
        "去執行吧", "刪吧", "準了", "沒問題，去執行", "沒問題去執行",
        # leading function words are stripped, so these still land
        "那你去执行吧", "先去執行吧", "我同意，去执行",
    ):
        assert OpenClawAdapter.rule_magic_command(text) == approve, text


@pytest.mark.parametrize(
    "text",
    [
        # 「准了」inside an unrelated word or sentence
        "我准了假下周去旅游", "领导批准了我的申请", "这标准了不起", "他的水准了得",
        # 「删吧」inside an unrelated word
        "删吧台的记录", "那个删吧的老哥",
        # 「去执行」reported, questioned, or explicitly refused
        "他说去执行了", "可以去执行吗", "拒绝去执行这种命令", "禁止去执行危险操作",
        "军人必须去执行命令", "这个方案没人去执行", "这标准了不起，但不要去执行",
        # the shapes that broke the negator-blacklist attempt
        "去執行？我不要", "要去執行嗎？", "他說去執行", "別去執行", "拒絕去執行",
        # /stop — a world event stopping, not a command
        "雨停下来了", "雨停下來了", "電梯停下來了", "公交车停下来了我要上车了",
        "他跑着跑着突然停下来", "我想让时间停下来", "音乐停下来之后房间好安静",
        "心跳停下來那一刻", "钥匙别找了我已经拿到了", "新闻说救援队停止搜索了",
        "他喊快停下来的时候已经晚了",
        # /new — a game/match/life restarting, or a comment about the phrase
        "比賽即將重新開始", "比赛即将重新开始", "遊戲重新開始倒數",
        "我想重新开始新的人生", "这局输了要重新开始吗", "下半場重新開始了",
        "他老是换个话题就想蒙混过去", "我不喜歡別人換個話題的樣子",
        "他除了工作说点别的都不会",
    ],
)
def test_whole_clause_whitelist_kills_free_text_misfires(text):
    """⚠️ The core regression set for this change.

    Every one of these dispatched a magic command before the switch — 22/22 for
    /stop, 16/16 for /new and 17/28 for /daemon approve on the adversarial set.
    They are ordinary sentences a user really types; the trigger word just
    happens to appear inside one.

    A "reject when a negator precedes the trigger" guard was tried first and an
    adversarial pass broke it on 196 inputs: negation to the *right* of the
    trigger (「去執行？我不要」), the anchor landing on an unrelated substring
    (「这标准了不起，但不要去执行」 anchors on 「准了」), and questions
    (「要去執行嗎？」) all sailed through — while it *also* rejected
    「没错，去执行」, i.e. the affirmations an approval context is literally
    built out of negation words. A blacklist cannot work here.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


def test_approve_whitelist_content_is_pinned():
    """⚠️ Equality, not containment, and the two tables must stay separate.

    These are the entire judgement for ``/daemon approve``: every entry is a
    phrase that, said alone, dispatches a real high-risk action upstream.
    Widening is a security decision, so adding a word must turn a test red.

    ⚠️ No bare single characters. An earlier revision derived the tables by
    closing them under the clause normalizer, which put 删 / 刪 / 准 / 準 in —
    and then 帮我删一下 (a fresh delete request) dispatched an approval. The
    tables are literal now; the *lookup* widens, not the table.

    Broad affirmations (可以 / 好 / 好的 / 行) are deliberately absent.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _APPROVE_AFFIRMATIONS,
        _APPROVE_COMPANIONS,
    )

    assert _APPROVE_AFFIRMATIONS == frozenset({"同意", "我同意", "没问题", "沒問題"})
    assert _APPROVE_ACTIONS == frozenset({
        "删吧", "刪吧", "准了", "準了",
        "去执行", "去执行吧", "去執行", "去執行吧",
        "没问题去执行", "沒問題去執行",
    })
    # ⚠️ 第三张表也要钉。它单独出现永远不授权，但它决定了「应答 + 动作」这一整类
    # 说法认不认——往里加词同样是扩大 approve 的命中面，必须让评审在同一个 commit 里
    # 说清楚。漏掉它的那一轮，`對` 缺失活了整整一个 PR。
    assert _APPROVE_COMPANIONS == frozenset({
        "好", "好的", "好吧", "行", "行了", "可以", "嗯",
        "对", "對", "没错", "沒錯", "没意见", "沒意見",
        "批准", "允许", "允許",
    })
    single_chars = sorted(
        w for w in (_APPROVE_ACTIONS | _APPROVE_AFFIRMATIONS) if len(w) < 2
    )
    assert not single_chars, f"单字条目会让任意祈使句落到批准上：{single_chars}"
    # 应答表里有单字（好 / 行 / 对 / 嗯）是有意的——它们单独一句永远是 None，
    # 只能陪同动作子句出现。这条断言把「单字仅限应答表」钉住。
    from brain.openclaw_adapter import OpenClawAdapter

    assert all(
        OpenClawAdapter.rule_magic_command(word) is None
        for word in sorted(_APPROVE_COMPANIONS)
    ), "应答词单独成句必须是 None"


@pytest.mark.parametrize(
    "text",
    [
        # 裸应答只认整条子句原样：剥首尾都不行，否则主动搭话轮里猫娘自己的口癖
        # 「没问题喵~」就会自批准。这些在改造前全是 None，必须保持。
        "没问题喵~", "没问题喵！", "沒問題喔", "同意~", "我同意喵", "没问题啦",
        "沒問題囉", "同意啦", "不如同意", "那就同意", "马上同意", "同意了",
        # 单字派生曾把这些变成批准 —— 它们是**新的删除请求**，不是批准
        "帮我删了", "帮我删一下", "删一下", "删啦", "删", "准", "刪", "準",
        "幫我刪了", "请删了", "那就删了吧", "删了吧", "快删了", "准一下", "删喵",
    ],
)
def test_approve_never_widens_beyond_the_pre_change_behaviour(text):
    """⚠️ 收口改动扩大高风险命令的命中面是本末倒置。

    Every input here returned None before the change. The clause normalizer made
    them approvals in an intermediate revision — via the table closure (删吧 -> 删)
    and via tail stripping on the bare affirmations (没问题喵~ -> 没问题).
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


# 这三张白名单里出现过的**全部**简繁异体字，闭集。opencc 不是本仓库依赖（只在
# scripts/gen_activity_fold_map.py 里用 `uv run --with` 临时装），所以 importorskip
# 会让这条守卫在 CI 上永远跳过——等于没写。改成闭集自带：新加的字形没进这张表时，
# 下面的双向折叠会折不出对侧形态，断言直接报出缺哪条。
_T2S = {
    "沒": "没", "問": "问", "題": "题", "刪": "删", "準": "准", "執": "执",
    "別": "别", "來": "来", "這": "这", "個": "个", "務": "务", "尋": "寻",
    "說": "说", "開": "开", "話": "话", "點": "点", "換": "换",
    "對": "对", "錯": "错", "見": "见", "許": "许",
}
# 白名单里简繁同形的字，单列。用途和 _FUNCTION_NEUTRAL_CHARS 一样：**发现表外字形**。
# 折叠表折不出对侧时 _fold 返回词条本身，而词条本身当然在表里 —— 于是一个用了表外字
# 的单侧词条会静默通过。`對` 就是这么漏进来的：它不在 _T2S 里，所以 `对` 折不出 `對`，
# 守卫查不出 _APPROVE_COMPANIONS 少了繁体侧。
_CLAUSE_NEUTRAL_CHARS = set(
    "下了以任停允去取可同吧嗯好始快意我批找搜新查止消的算索聊行重"
)
_S2T = {simplified: traditional for traditional, simplified in _T2S.items()}
# ⚠️ 台湾用「搜尋」不用「搜索」——这是**词汇**差异，不是字形转换，折叠折不出来。
# 只有这两组，单独豁免；别把豁免集当垃圾桶，每加一条都要说明为什么不是字形对。
_LEXICAL_NOT_A_FOLD = frozenset({
    "停止搜索", "停止搜尋", "取消这个搜索", "取消這個搜尋",
    # ⚠️ 准 是**一简对多繁**：許可義的繁体就写作「准」（批准 / 准許 / 不准），
    # 「準」是準確義。所以 `批准` 两侧同形，机械折叠折出来的 `批準` 不是词，不能收
    # 进白名单去凑对称——收了等于给 approve 白加一个词条。
    # （表里同时有 `准了`/`準了` 是另一回事：那是把用户可能打错的写法一起认了，
    #   属于放宽召回，不是对称性要求。）
    "批准",
})


def test_clause_whitelists_are_script_symmetric():
    """Auto-discovered, not a checklist: fold every entry BOTH ways and require
    the counterpart to be in the same table.

    A missing counterpart means the command silently does not exist for users of
    one script — the exact failure #2500 is about. Folding both directions
    catches it whichever side was forgotten; a pairwise list only catches the
    pairs somebody remembered to write down.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _APPROVE_AFFIRMATIONS,
        _APPROVE_COMPANIONS,
        _NEW_CLAUSES,
        _STOP_CLAUSES,
    )

    def _fold(text, table):
        return "".join(table.get(char, char) for char in text)

    tables = (
        # ⚠️ approve 的判据是**三**张表。少列一张不会让任何断言变红，而 approve 的命中
        # 面照样变宽——`對` 就是这么漏了一整轮：companions 一张守卫都没盖到。
        ("approve_actions", _APPROVE_ACTIONS),
        ("approve_affirmations", _APPROVE_AFFIRMATIONS),
        ("approve_companions", _APPROVE_COMPANIONS),
        ("stop", _STOP_CLAUSES),
        ("new", _NEW_CLAUSES),
    )

    # ⚠️ 表外字形必须报错，否则这条守卫在它身上是空转的：_fold 折不出对侧时返回词条
    # 本身，而词条本身当然在表里 → 静默通过。补完 companions 还不够，`對` 当时也不在
    # _T2S 里，两个漏洞叠在一起才让它活下来。
    unknown = {
        char
        for _, entries in tables
        for entry in entries
        for char in entry
        if "㐀" <= char <= "鿿"
        and char not in _T2S
        and char not in _S2T
        and char not in _CLAUSE_NEUTRAL_CHARS
    }
    assert not unknown, (
        f"这些字形不在折叠表也不在中性清单里，简繁对称无法验证 → {sorted(unknown)}"
    )

    for name, entries in tables:
        missing = []
        for entry in sorted(entries):
            if entry in _LEXICAL_NOT_A_FOLD:
                continue
            for direction, table in (("t2s", _T2S), ("s2t", _S2T)):
                counterpart = _fold(entry, table)
                if counterpart not in entries:
                    missing.append(f"{entry} --{direction}--> {counterpart}")
        assert not missing, f"{name}: 对侧字形缺失 → {missing}"


def test_approve_requires_every_clause_but_stop_and_new_only_the_last():
    """⚠️ The asymmetry is load-bearing, not an oversight.

    ``/daemon approve`` runs a high-risk action upstream, so it is fail-closed:
    ANY clause outside the whitelist kills it, which is what stops
    「我不同意，去执行」 from approving. ``/stop`` and ``/new`` only halt a task
    or change the subject, so they read the trailing imperative — otherwise
    「我还没同意，停止搜索」 would stop dispatching at all.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    # approve: fail-closed on a non-whitelisted clause anywhere
    assert OpenClawAdapter.rule_magic_command("我不同意，去执行") is None
    assert OpenClawAdapter.rule_magic_command("我不同意，去執行") is None
    assert OpenClawAdapter.rule_magic_command("同意，去执行") == "/daemon approve"
    # stop / new: the TRAILING clause decides — a whitelist phrase sitting in a
    # non-final clause is narration, not an imperative, and must not dispatch.
    # ⚠️ 这四条是「末子句」和「任意子句」判据的唯一区分点：换成 any(...) 时只有它们会红。
    assert OpenClawAdapter.rule_magic_command("我还没同意，停止搜索") == "/stop"
    assert OpenClawAdapter.rule_magic_command("我不同意这个方案，换个话题") == "/new"
    assert OpenClawAdapter.rule_magic_command("停下来，这是我当时唯一的念头") is None
    assert OpenClawAdapter.rule_magic_command("停下來，這是我當時唯一的念頭") is None
    assert OpenClawAdapter.rule_magic_command("换个话题，他总是这么逃避") is None
    assert OpenClawAdapter.rule_magic_command("換個話題，他總是這麼逃避") is None
    assert OpenClawAdapter.rule_magic_command("别找了，他说，然后转身走了") is None
    assert OpenClawAdapter.rule_magic_command("重新开始，说起来简单做起来难") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # leading function words stripped
        ("我们换个话题吧", "/new"), ("我們換個話題吧", "/new"),
        ("请停下来", "/stop"), ("請停下來", "/stop"),
        ("帮我停下来", "/stop"), ("幫我停下來", "/stop"),
        ("那你去执行吧", "/daemon approve"),
        # ⚠️ 第二人称的复数/敬称写法要**整词**排在单字 `你` 前面，否则 `你们停下来`
        # 被 `你` 吃掉首字、剩下 `们停下来`。这条和 `那么`/`快点` 是同一个坑。
        ("你们停下来", "/stop"), ("你們停下來", "/stop"),
        ("您换个话题吧", "/new"), ("您們換個話題", "/new"),
        ("您去执行吧", "/daemon approve"), ("你们去执行吧", "/daemon approve"),
        # ⚠️ 多字前缀必须整词剥。`那` 排在 `那么` 前面时正则会吃掉首字、留下一个
        # `么` 粘在后面（子句变成「么停下来」），整条判据失效。`快`/`快点` 同理。
        ("那么停下来吧", "/stop"), ("那麼換個話題吧", "/new"),
        ("快点停下来", "/stop"), ("快點停下來", "/stop"),
        ("快点去执行", "/daemon approve"),
        # 祈使副词：中文祈使句最常见的修饰，闭集缺了它们等于这套口令只认「裸命令」
        ("赶紧停下来", "/stop"), ("馬上取消這個任務", "/stop"),
        ("立刻停止搜尋", "/stop"), ("现在别找了", "/stop"),
        ("能不能停下来", "/stop"), ("不如換個話題", "/new"),
        ("干脆换个话题", "/new"), ("还是换个话题吧", "/new"),
        ("拜託停下來", "/stop"), ("我想取消这个任务", "/stop"),
        # ⚠️ `要不要` 必须排在 `要不` 前面，否则被咬成 `要停下来`——这是这套表
        # 第五次栽在「多字词排在它的首字/前缀后面」上（那么·快点·我想·你们·要不要）。
        ("要不要停下来？", "/stop"), ("要不要停下來", "/stop"),
        ("要不要换个话题", "/new"), ("要不要換個話題", "/new"),
        ("我想重新开始", "/new"), ("马上去执行", "/daemon approve"),
        # trailing particles stripped
        ("重新开始吧", "/new"), ("重新開始吧", "/new"),
        ("停下来吧", "/stop"), ("停下來吧", "/stop"),
        # 征询/疑问尾：「…好吗 / …行不行」是最常见的礼貌祈使口吻
        ("停下来好吗", "/stop"), ("停下來好嗎", "/stop"),
        ("停下来行不行", "/stop"), ("停下来好不好", "/stop"),
        ("换个话题好吗", "/new"), ("換個話題好嗎", "/new"),
        ("换个话题怎么样", "/new"), ("重新開始好嗎", "/new"),
        # ⚠️ 语气词也是简繁两侧的东西：只收繁体「囉」会让同一句话繁体命中简体不命中
        ("停下來囉", "/stop"), ("停下来啰", "/stop"), ("停下来咯", "/stop"),
        ("停下來咯", "/stop"), ("停下来喽", "/stop"),
        ("说点别的呗", "/new"), ("說點別的唄", "/new"),
        # ...but stripping must not resurrect a misfire
        ("雨停下来了", None), ("我准了假", None), ("比賽即將重新開始", None),
        ("我想重新开始新的人生", None), ("我想让时间停下来", None),
        ("可以去执行吗", None), ("能不能去執行嗎？我還沒決定", None),
    ],
)
def test_clause_normalization_strips_only_function_words(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 中文里最常见的授权说法是「应答子句 + 命令子句」，改造前靠子串命中。
        # `没错，去执行` 尤其要保住——它正是本文件论证「黑名单会误伤」时点名的例子，
        # 白名单方案曾把它一起误伤，注释和行为对不上。
        ("没错，去执行", "/daemon approve"), ("沒錯，去執行", "/daemon approve"),
        ("没意见，去执行", "/daemon approve"), ("好的，去执行", "/daemon approve"),
        ("行，去执行", "/daemon approve"), ("可以，去执行", "/daemon approve"),
        ("嗯，去执行", "/daemon approve"), ("对，去执行", "/daemon approve"),
        ("批准，去执行", "/daemon approve"), ("可以，删吧", "/daemon approve"),
        ("好的，去执行吧", "/daemon approve"),
        # 不带分隔符的写法走中性首部表
        ("好的去执行", "/daemon approve"), ("可以去执行", "/daemon approve"),
        ("同意去执行", "/daemon approve"), ("批准去执行", "/daemon approve"),
        ("允许去执行", "/daemon approve"), ("没错去执行", "/daemon approve"),
        # ⚠️ 但应答词**不能单独授权**：这些在改造前都是 None（旧的整句精确匹配表
        # 只有那四条），当成授权就是扩大批准面。
        ("好的", None), ("可以", None), ("行", None), ("嗯", None), ("对", None),
        ("批准", None), ("好的，好的", None), ("好的喵~", None),
        # ⚠️ 应答子句剥两端装饰是安全的（它单独出现永远不算授权），而这些形态在旧
        # 实现里靠子串命中，逐字原样匹配会丢。裸应答不能这么放宽——对比
        # test_a_question_never_approves 里的 `没问题喵~`。
        ("好的喵~，去执行", "/daemon approve"), ("OK，去执行", "/daemon approve"),
        ("okay，去执行", "/daemon approve"),
        # ⚠️ 否定符号也能落在**应答**子句上：`可以❌，去執行` 里的 ❌ 否定的是那句
        # 应答。所以应答子句的装饰表也用严格那张——👌 和 ❌ 在这一层分不出来，
        # 只能整类不剥。代价：`好的👌，去执行` 相对旧实现丢了。
        ("可以❌，去執行", None), ("可以❌，去执行", None), ("好的🚫，去执行", None),
        ("好的👌，去执行", None), ("请❌，去执行", None),
        # 拉丁前缀的大小写写法是开集，靠整体小写候选覆盖而不是枚举
        ("oK去执行", "/daemon approve"), ("oKaY去执行", "/daemon approve"),
        ("Okay去执行", "/daemon approve"),
        # 中英混排且不带分隔符的写法走中性首部表
        ("OK去执行", "/daemon approve"), ("ok去执行", "/daemon approve"),
        # ⚠️ 中性首部词**独立成句**时也算应答子句：`请，去执行` 在旧实现里靠子串
        # 命中，而同样的词贴着写（`请去执行`）一直是通的。判据一致：剥掉它不改变
        # 「谁被授权做什么」，那么它单独成句同样不改变。
        ("请，去执行", "/daemon approve"), ("麻烦，去执行", "/daemon approve"),
        ("拜託，去執行", "/daemon approve"), ("那，去执行", "/daemon approve"),
        ("你，去执行", "/daemon approve"), ("马上，去执行", "/daemon approve"),
        # 但礼貌词单独出现仍不算授权
        ("请", None), ("麻烦", None), ("那", None), ("请，麻烦", None),
        ("okay去执行", "/daemon approve"),
        ("好的喵~", None),
        # ⚠️ 单字应答**不做前缀剥离**：`对方去执行` 不是授权
        ("对方去执行", None), ("好人去执行", None),
    ],
)
def test_an_affirmative_clause_needs_a_real_command_beside_it(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 子句两端的装饰性字符：省略号、破折号、引号、括号、emoji。它们既不在
        # _CLAUSE_SPLIT 也不是语气词，整条就落不到白名单上——而中文聊天里这是最常见
        # 的收尾方式之一。用「非词字符」的补集来剥，不去枚举符号（符号是开集）。
        ("去执行…", "/daemon approve"), ("去执行……", "/daemon approve"),
        ("去执行~", "/daemon approve"), ("去执行！", "/daemon approve"),
        ("去执行。", "/daemon approve"),
        # ⚠️ 句尾同样能带否定：`去執行❌` 也是「别执行」。「哪些符号带否定语义」
        # 是开集（❌✖✗🚫⛔🙅🆖……），黑名单堵不完——所以 approve 的句尾装饰改成
        # 一张**明确无语义**的标点闭集，emoji 一律不剥。代价：`去执行👌` 相对旧
        # 实现丢了（👌 和 ❌ 在这一层区分不了），记账在此。
        ("去執行❌", None), ("去执行❌", None), ("去執行🚫", None),
        ("去执行✖", None), ("删吧❌", None), ("准了❌", None),
        ("去执行👌", None),
        ("停下来…", "/stop"), ("停下來……", "/stop"), ("停下來——", "/stop"),
        ("別找了…", "/stop"), ("「停下來」", "/stop"), ("“停下来”", "/stop"),
        ("換個話題…", "/new"), ("换个话题👍", "/new"), ("重新開始……", "/new"),
        # 剥完装饰仍要过白名单——装饰不是万能钥匙
        ("雨停下来了…", None), ("我准了假👌", None), ("比賽即將重新開始……", None),
        # ⚠️ 中文省略号是**句中分隔符**，不只是句尾装饰
        ("同意……去执行", "/daemon approve"), ("同意⋯⋯去执行", "/daemon approve"),
        # 破折号同理，也是句中分隔符
        ("同意——去执行", "/daemon approve"), ("同意—去执行", "/daemon approve"),
        ("好吧——换个话题", "/new"), ("停下來——別找了", "/stop"),
        ("好吧……换个话题", "/new"), ("停下來……別找了", "/stop"),
        # ⚠️⚠️ approve **只剥句尾装饰**：句首那一格是语义位。`❌去執行` 是「别执行」，
        # `「去執行」` 是在**提及**这个词而不是下令。一律当装饰剥掉就全变成了授权。
        ("❌去執行", None), ("❌去执行", None), ("🚫去執行🚫", None),
        ("🚫去执行", None), ("「去執行」", None), ("「去执行」", None),
        ("『去执行』", None), ("（去执行）", None),
        # /stop 与 /new 后果小，两端照剥
        ("「停下來」", "/stop"), ("『別找了』", "/stop"), ("（换个话题）", "/new"),
        # ⚠️ 空白也是分隔符，会把语气词切成独立末子句；末子句判据要往回跳过它们
        ("停下来 吧", "/stop"), ("停下來 👍", "/stop"), ("换个话题 喵", "/new"),
        # ⚠️ 剥这段尾巴要试**所有**匹配的词尾并取最长：`_TAIL_TOKENS` 里 `了` 排在
        # `好了` 前面，只取第一个匹配会把 `好了` 剥成 `好`，于是这段尾巴不被认成
        # 「纯语气词」、反倒被当成命令子句。和 _clause_hits 里那个坑是同一个。
        ("换个话题 好了", "/new"), ("停下来 好了", "/stop"), ("別找了 好了", "/stop"),
        # 礼貌收尾也是纯语气：`谢谢` 不该被当成命令子句
        ("停下来谢谢", "/stop"), ("停下來，謝謝", "/stop"), ("换个话题，谢谢", "/new"),
        ("别找了 谢谢", "/stop"), ("停下来多谢", "/stop"),
        ("别找了 吧", "/stop"),
        # ⚠️ 但**不给 approve 用**：那样 `同意 吧` 会变成裸应答被批准（旧实现是 None）。
        # 代价是 `去执行 吧` 也丢了，二选一，选了 fail-closed 那边。
        ("同意 吧", None), ("去执行 吧", None),
    ],
)
def test_decorative_characters_do_not_hide_a_command(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


def test_peeling_is_bounded_for_pathological_input():
    """⚠️ 分类器跑在用户输入路径上，剥词的候选集必须有硬上界。

    Every peel step pushes another prefix of the clause, so the candidate count
    grows with the run of trailing particles and each one costs a slice — a 20k
    character tail used to stall the event loop for seconds.
    """  # noqa: DOCSTRING_CJK
    import time

    from brain.openclaw_adapter import OpenClawAdapter

    start = time.perf_counter()
    # 走 _clause_hits 的剥词
    assert OpenClawAdapter.rule_magic_command("去执行" + "吧" * 20000) is None
    assert OpenClawAdapter.rule_magic_command("停下来" + "啊呀" * 10000) is None
    # ⚠️ 也要走 _command_clause 的剥词：空格把语气词切成独立末子句之后，往回跳过
    # 它们的那段循环是**另一处**剥词，界得单独加（变异验证抓出来的）。
    OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 60000)
    OpenClawAdapter.rule_magic_command("随便说说 " + "啊" * 60000)
    # ⚠️ _command_clause 里那段剥词的界按**行为**断言而不是按耗时。
    # 耗时断言在这里不可靠：阈值要写多大取决于机器，而它的可观察后果是确定的——
    # 超长尾巴剥不完 → 不跳过它 → 返回 None。
    # （早先我按耗时写过一版并据此宣称「无界版也不慢」，那是拿**改剥词逻辑之前**的
    # 数字说话。现在每步都多一次整串正则，无界版实测连 120k 那一档都跑不完，
    # 界是必需的。）
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 5) == "/stop"
    assert OpenClawAdapter.rule_magic_command("停下来 " + "吧" * 200) is None
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"病态输入耗时 {elapsed:.2f}s，剥词的界没生效"


@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ 表内条目**被语气词截短后的残形**不是有效命令，改造前也是 None
        # （旧表里只有完整的「别找了」「算了别查了」）。
        # 一旦查表改成「把表闭包到归一化形态」而不是「把查询归一化后去比对」，这些
        # 残形会全部命中——那正是单字「删 / 准」混进 approve 表的同一个错误。
        "别找", "別找", "算了别查", "算了別查",
        # 单字动词同理——它们曾因表闭包进过 approve 表
        "删", "刪", "准", "準", "删了吧", "删一下",
    ],
)
def test_truncated_table_entries_are_not_commands(text):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 语气词必须**逐个剥、每剥一个查一次表**。一次性把词尾整串吃掉会连表内
        # 条目自带的那个字一起吃掉：`别找了吧` 的 `了吧` 被整串剥成 `别找`，而表里
        # 的条目是 `别找了`——一句再自然不过的话就停不掉任务了。
        ("别找了吧", "/stop"), ("別找了吧", "/stop"),
        ("算了别查了吧", "/stop"), ("算了別查了吧", "/stop"),
        ("快别找了吧", "/stop"), ("别找了啊", "/stop"), ("別找了囉", "/stop"),
        ("准了吧", "/daemon approve"), ("準了吧", "/daemon approve"),
        # ⚠️ 每一步要对**所有**能匹配的词尾各试一次，不能只试正则挑中的那一个。
        # 多选支从左优先：`停下来行吗` 里 `行吗` 会先命中、把它剥成 `停下来行`，
        # 再也剥不出 `停下来`。换词表顺序解决不了，`行吗` 本身必须收。
        # （approve 侧不能拿来测这个——疑问式对批准是一票否决，见
        # test_a_question_never_approves。）
        ("停下来行吗", "/stop"), ("停下來行嗎", "/stop"), ("换个话题行吗", "/new"),
        ("停下来吗", "/stop"), ("停下來嗎", "/stop"), ("换个话题怎么样", "/new"),
    ],
)
def test_particles_are_peeled_one_at_a_time(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("clause", "table_name"),
    [
        # ⚠️ 这条**直接测查表层**，不经 rule_magic_command。
        # 「多选支从左优先咬掉内容字」这个 bug 只在**以「行」结尾的表内条目**上发作
        # （`去执行吗` 被 `行吗` 咬成 `去执`），而那些形式现在被疑问否决先拦掉了——
        # 从 rule_magic_command 那一层再也观察不到，变异测试会误判成等价变异。
        # 判据和否决是两层，分开钉，否则哪天否决被调整，剥词的 bug 会静默复活。
        ("去执行吗", "_APPROVE_ACTIONS"), ("去執行嗎", "_APPROVE_ACTIONS"),
        ("去执行行不行", "_APPROVE_ACTIONS"), ("去執行好不好", "_APPROVE_ACTIONS"),
        ("停下来行吗", "_STOP_CLAUSES"), ("停下來行嗎", "_STOP_CLAUSES"),
        ("换个话题好不好", "_NEW_CLAUSES"),
    ],
)
def test_the_lookup_layer_peels_every_matching_tail(clause, table_name):
    from brain.openclaw_adapter import (
        _APPROVE_ACTIONS,
        _NEW_CLAUSES,
        _STOP_CLAUSES,
        _clause_hits,
    )

    tables = {
        "_APPROVE_ACTIONS": _APPROVE_ACTIONS,
        "_NEW_CLAUSES": _NEW_CLAUSES,
        "_STOP_CLAUSES": _STOP_CLAUSES,
    }
    assert _clause_hits(clause, tables[table_name]), f"{clause} 剥不出 {table_name} 里的条目"


@pytest.mark.parametrize(
    "text",
    [
        # 标点
        "去執行？", "去执行？", "刪吧？", "删吧？", "准了？", "準了？", "去執行?",
        "没问题，去执行？", "沒問題，去執行？", "同意？", "我同意？",
        # ⚠️ 光认标点不够：归一化会把疑问语气整个抹掉。句末语气词……
        "去執行嗎", "去执行吗", "刪吧嗎", "删吧吗", "準了嗎", "准了吗",
        # ……正反问 / 选择问（首部或句中），剥完同样落在表内的动作短语上
        "能不能去執行", "能不能去执行", "可不可以去執行", "可不可以去执行",
        "去執行行不行", "去执行好不好", "要不要去执行", "是不是该去执行",
        "是否可以去执行", "去执行怎么样", "去執行怎麼樣",
        # ⚠️ 试探/提议型首部词同理——归一化把它们剥掉之后，一句「要不就去执行？」
        # 的**提议**就变成了授权。这些也全是首部虚词表新放行出来的暴露面。
        "要不去執行", "要不去执行", "要不去执行吧", "要不然去執行",
        "不如去執行", "不如去执行", "不如刪吧", "不如删吧", "不如準了",
        "還是去執行", "还是去执行", "还是去执行吧", "乾脆去執行", "干脆去执行",
        # ⚠️ 第一人称意图前缀：它们改变的是**谁打算做**，不是加强祈使语气。
        # 「我想去執行」是在陈述自己的打算，不是授权别人去做。
        "我想去執行", "我想去执行", "我要去執行", "我要去执行", "想去执行",
        "我想删吧", "我要準了",
        # ⚠️ 光挡 `我想`/`我要` 不够，**裸的第一人称主语**同理：「我去執行」是用户在说
        # 自己要去做，不是授权 agent 去做。第二人称留着——`你去执行吧` 恰恰是授权。
        "我去執行", "我去执行", "我删吧", "我刪吧", "我准了",
        "咱去执行", "我们去执行吧", "我們去執行吧", "咱们去执行",
        # ⚠️ 体标记 `了`：`去執行了` 是在报告「已经执行了」，是陈述不是授权。
        "去執行了", "去执行了", "去執行了喔", "删吧了", "去执行了吧",
    ],
)
def test_a_question_never_approves(text):
    """⚠️ 问句和提议都不是授权。

    Normalization erases the mood entirely and everything lands on a whitelisted
    action phrase: 去執行嗎 loses its 嗎, 能不能去執行 loses its 能不能, 要不去執行
    loses its 要不. The Traditional spellings here were all None on main — the
    whole-clause switch newly exposed them, which is exactly backwards for a
    hardening change.

    Vetoing the whole utterance is fail-closed and costs nothing: nobody granting
    permission phrases it as a question or floats it as a suggestion.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


# 四张虚词表里出现过的简繁异体字，闭集。中性字形（简繁同形）单列，用于**发现表外
# 字形**——这是上一版守卫的盲区：折不出对侧形态时 _fold 返回词条本身，而词条本身当然
# 在表里，于是新加一个用了表外字的单侧词条会静默通过。
_FUNCTION_T2S = {
    "錯": "错", "見": "见", "許": "许", "麼": "么", "點": "点", "這": "这",
    "幫": "帮", "給": "给", "煩": "烦", "託": "托", "勞": "劳", "駕": "驾",
    "請": "请", "趕": "赶", "緊": "紧", "馬": "马", "現": "现", "盡": "尽",
    "務": "务", "記": "记", "繼": "继", "續": "续", "們": "们", "還": "还",
    "問": "问",
    "乾": "干", "囉": "啰", "嘍": "喽", "唄": "呗", "喲": "哟", "噠": "哒",
    "吶": "呐", "嗎": "吗", "樣": "样", "沒": "没", "欸": "诶", "謝": "谢",
}
_FUNCTION_NEUTRAL_CHARS = set(
    "一上下不了以你允先准刻即可同吧呀呢呦咧咯咱哈哦唷啊啦喔喵嘛嘞噢在好如妳定就得"
    "心必忙快怎您想意我批拜捏接放是替有然的直立耶能脆行要那麻齁多感否"
)


def test_function_word_tables_are_pinned():
    """⚠️ 等值钉死四张虚词表——这是**唯一**能挡住「新加一类前缀」的守卫。

    The per-category assertions below only catch tokens someone already thought
    of: they check that known hedges live in the soft tables. A brand-new hedge
    (或许 / 恐怕 / 说不定 …) dropped into the neutral table is in *neither* list,
    so every one of those assertions sails past it — verified by mutation: adding
    或许|恐怕|说不定 to _NEUTRAL_LEAD left the whole suite green while
    「或许去执行」 became a real approval.

    Equality is what closes that. Any edit to these tables now turns this red and
    the reviewer has to state, in the same commit, which category the new token
    belongs to and what its cross-script counterpart is.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    assert set(_NEUTRAL_LEAD.split("|")) == {
        "OKAY", "Okay", "okay", "OK", "Ok", "ok",
        "没错", "沒錯", "没意见", "沒意見", "批准", "允许", "允許", "同意",
        "好的", "好吧", "行了", "可以",
        "那么", "那麼", "快点", "快點", "就这么", "就這麼", "这就", "這就",
        "帮我", "幫我", "帮忙", "幫忙", "给我", "給我", "替我",
        "麻烦", "麻煩", "拜托", "拜託", "劳驾", "勞駕", "有劳", "有勞",
        "烦请", "煩請",
        "赶紧", "趕緊", "赶快", "趕快", "马上", "馬上", "立刻", "立即",
        "现在", "現在", "尽快", "盡快", "直接",
        "务必", "務必", "记得", "記得", "一定", "放心", "继续", "繼續",
        "你们", "你們", "您们", "您們", "您",
        "那", "就", "先", "快", "请", "請", "你", "妳",
    }
    assert set(_SOFT_LEAD.split("|")) == {
        "要不要", "能不能", "可不可以", "要不然", "要不", "不如", "还是", "還是",
        "是否可以", "是否能", "是否", "能否", "可否",
        "请问", "請問",
        "干脆", "乾脆", "我想", "我要", "我们", "我們", "咱们", "咱們",
        "想", "我", "咱",
    }
    assert set(_NEUTRAL_TAIL.split("|")) == {
        "好了", "吧", "啊", "呀", "喔", "哦", "嘛", "囉", "啰", "咯", "喽",
        "嘍", "呗", "唄", "嘞", "啦", "一下", "喵",
        "谢谢", "謝謝", "多谢", "多謝", "感谢", "感謝",
        "拜托", "拜託", "麻烦", "麻煩",
        "耶", "唷", "哟", "喲", "欸", "诶", "咧", "哈", "噢", "呐", "吶",
        "呦", "哒", "噠", "齁", "捏", "~", "～",
    }
    assert set(_SOFT_TAIL.split("|")) == {
        "好不好", "好吗", "好嗎", "行不行", "行吗", "行嗎", "可以吗", "可以嗎",
        "怎么样", "怎麼樣", "吗", "嗎", "呢", "了",
    }


def test_function_word_tables_are_script_symmetric():
    """⚠️ 虚词表也是简繁两侧的东西，不只子句白名单。

    The file's whole thesis is that these tables hit the characters a user
    actually types, so both scripts must be collected in the same pass — yet
    until now only the *clause* whitelists had a symmetry guard. Dropping 現在 /
    嘍 / 可以嗎 / 給我 individually left the suite green while their Simplified
    twins were covered: exactly the asymmetry this series exists to kill.

    ⚠️ Unknown characters fail loudly. The previous guard folded with a partial
    map and returned the entry unchanged when a character was missing — and the
    entry is of course in its own table, so a one-sided addition using a new
    character passed silently. Here every CJK character must be accounted for.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    s2t = {v: k for k, v in _FUNCTION_T2S.items()}
    tables = {
        "neutral_lead": _NEUTRAL_LEAD,
        "soft_lead": _SOFT_LEAD,
        "neutral_tail": _NEUTRAL_TAIL,
        "soft_tail": _SOFT_TAIL,
    }

    unknown = {
        char
        for table in tables.values()
        for word in table.split("|")
        for char in word
        if "㐀" <= char <= "鿿"
        and char not in _FUNCTION_T2S
        and char not in s2t
        and char not in _FUNCTION_NEUTRAL_CHARS
    }
    assert not unknown, (
        f"这些字形不在折叠表也不在中性清单里，简繁对称无法验证 → {sorted(unknown)}"
    )

    def _fold(text, mapping):
        return "".join(mapping.get(char, char) for char in text)

    for name, table in tables.items():
        entries = set(table.split("|"))
        missing = []
        for entry in sorted(entries):
            for direction, mapping in (("t2s", _FUNCTION_T2S), ("s2t", s2t)):
                counterpart = _fold(entry, mapping)
                if counterpart not in entries:
                    missing.append(f"{entry} --{direction}--> {counterpart}")
        assert not missing, f"{name}: 对侧字形缺失 → {missing}"


def test_narrow_and_wide_lead_sets_are_disjoint_where_it_matters():
    """⚠️ 结构性守卫：approve 的窄表**不得**包含试探/意图前缀。

    Three rounds of Codex P1 landed on the same shape — a wide strip set plus a
    veto list that kept missing a category (punctuation, then bare interrogative
    particles, then tentative proposals, then first-person intent). "Which prefix
    turns an imperative into a non-authorization" is an open set; a blacklist
    cannot close it. The fix was two whitelists, and this test pins that they
    stay separated: widening approve now means editing the narrow table, which is
    a visible, reviewable act.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import (
        _NEUTRAL_LEAD,
        _NEUTRAL_TAIL,
        _SOFT_LEAD,
        _SOFT_TAIL,
    )

    narrow_lead = set(_NEUTRAL_LEAD.split("|"))
    soft_lead = set(_SOFT_LEAD.split("|"))
    narrow_tail = set(_NEUTRAL_TAIL.split("|"))
    soft_tail = set(_SOFT_TAIL.split("|"))

    assert not (narrow_lead & soft_lead), "宽首部词漏进了 approve 的窄首部表"
    assert not (narrow_tail & soft_tail), "宽词尾漏进了 approve 的窄词尾表"

    # ⚠️ 入表判据只有一条：**剥掉它会不会把授权变成非授权**。下面按类逐一钉住，
    # 因为这一系列 Codex P1 全是「又发现一类漏在中性表里」——问句、试探提议、
    # 第一人称意图、裸第一人称主语、体标记，一轮一类。
    for token in (
        # 试探提议：是在抛方案，不是批准
        "要不", "不如", "还是", "還是", "干脆", "乾脆", "能不能", "可不可以",
        # 第一人称意图：陈述自己的打算
        "我想", "我要", "想",
        # 裸第一人称主语：宣告自己动手，不是授权 agent
        "我", "咱", "我们", "我們", "咱们", "咱們",
    ):
        assert token in soft_lead, f"{token} 不在 soft 首部表里"
        assert token not in narrow_lead, f"{token} 混进了 approve 的窄首部表"
    for token in (
        # 疑问尾：问句不是授权
        "吗", "嗎", "呢", "好不好", "行不行", "可以吗", "怎么样",
        # 体标记：`去執行了` 是在报告已发生，不是授权
        "了",
    ):
        assert token in soft_tail, f"{token} 不在 soft 词尾表里"
        assert token not in narrow_tail, f"{token} 混进了 approve 的窄词尾表"

    # 反向：第二人称主语必须留在中性表——`你去执行吧` 正是指向 agent 的授权。
    # 复数/敬称写法一并要有，否则单字 `你` 会把 `你们` 咬断。
    for token in ("你", "妳", "你们", "你們", "您", "您们", "您們"):
        assert token in narrow_lead, f"{token} 是第二人称，不该被挪走"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 冒号也是子句边界。`同意：去执行` 在改造前靠子串命中，不切冒号的话整条
        # 落不到任何白名单条目上（Codex P2）。
        ("同意：去执行", "/daemon approve"), ("沒問題:去執行", "/daemon approve"),
        ("没问题：去执行", "/daemon approve"), ("同意:删吧", "/daemon approve"),
        ("先说明一下：停下来", "/stop"), ("这样吧：换个话题", "/new"),
    ],
)
def test_colons_are_clause_boundaries(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    ["马上去执行", "赶紧去执行", "立刻去执行", "现在就去执行", "直接去执行",
     "那就去执行吧", "先去執行吧", "馬上去執行", "趕緊去執行", "盡快去執行"],
)
def test_decisive_adverbs_still_approve(text):
    """⚠️ The hedge veto must not swallow decisive adverbs.

    馬上 / 趕緊 / 立刻 / 現在 / 盡快 / 直接 / 那就 / 先 intensify an imperative
    rather than propose one; every Simplified spelling here approves on main, so
    rejecting them would be pure recall loss with no safety gain. The line is
    "tentative proposal vs. emphasised command", not "has an adverb".
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 问号否决**只作用于 approve**：对 /stop 和 /new 而言，
        # 「停下来好吗？」是完全正常的礼貌祈使，不能一并毙掉。
        ("停下来好吗？", "/stop"), ("停下來好嗎？", "/stop"),
        ("换个话题好吗？", "/new"), ("換個話題好嗎？", "/new"),
        ("能不能停下来？", "/stop"),
        # 无标点的疑问式同理——「能不能停下来」是最常见的礼貌祈使之一
        ("能不能停下来", "/stop"), ("可不可以停下來", "/stop"),
        ("停下来吗", "/stop"), ("停下来行不行", "/stop"),
        ("换个话题怎么样", "/new"), ("換個話題怎麼樣", "/new"),
        # 试探/提议型对 /stop 和 /new 同样是完全正常的祈使
        ("要不换个话题", "/new"), ("不如换个话题", "/new"), ("还是换个话题吧", "/new"),
        ("要不停下来", "/stop"), ("不如停下來", "/stop"), ("還是停下來吧", "/stop"),
        ("干脆换个话题", "/new"), ("乾脆停下來", "/stop"),
    ],
)
def test_the_question_veto_is_scoped_to_approve(text, expected):
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


def test_no_magic_command_fires_on_the_projects_own_ui_copy():
    """⚠️ Auto-discovered corpus, not a hand-written list.

    static/locales/{zh-CN,zh-TW}.json is ~4257 strings of product copy with zero
    command intent. Before the whole-clause switch, 6 strings in EACH script
    dispatched a magic command — including the day6 tutorial line 「随时都可以戳
    一下让我停下来」, i.e. N.E.K.O.'s own script would have halted a task.

    This corpus grows with the product, so it keeps finding regressions a fixed
    adversarial list cannot. It is a floor, not a ceiling: UI copy is written
    prose, and real speech carries these phrases far more densely.
    """  # noqa: DOCSTRING_CJK
    import json
    from pathlib import Path

    from brain.openclaw_adapter import OpenClawAdapter

    def _walk(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for value in node.values():
                yield from _walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from _walk(value)

    repo_root = Path(__file__).resolve().parents[2]
    checked = 0
    hits = []
    for locale in ("zh-CN", "zh-TW"):
        path = repo_root / "static" / "locales" / f"{locale}.json"
        if not path.exists():
            pytest.skip(f"{path} missing")
        for text in _walk(json.loads(path.read_text(encoding="utf-8"))):
            if not text.strip():
                continue
            checked += 1
            command = OpenClawAdapter.rule_magic_command(text)
            if command:
                hits.append((locale, command, text[:80]))

    assert checked > 1000, f"语料没读到，只有 {checked} 条"
    assert not hits, f"UI 文案触发了 magic command：{hits}"


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


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("对，去执行", "對，去執行"),
        ("对，删吧", "對，刪吧"),
        ("对，去执行吧", "對，去執行吧"),
        # ⚠️ 这里全是「应答 + **动作**」。`对，同意` 那种「应答 + 裸应答」两侧都是 None
        # ——它在 main 上也是 None，不在本次要补的召回里，见
        # test_an_affirmation_needs_a_real_action_beside_it。
    ],
)
def test_the_companion_clause_works_in_both_scripts(simplified, traditional):
    """⚠️ 应答子句表漏了繁体 `對`，同一句话简体通、繁体不通。

    ``对`` was the only one-sided entry in _APPROVE_COMPANIONS — every other pair
    (没错/沒錯, 没意见/沒意見, 允许/允許) was collected on both sides. Two guards
    should have caught it and neither did: the table was absent from the symmetry
    checklist, and ``對`` was absent from the fold map, so even adding the table
    would have folded ``对`` to itself and passed.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    resolved = OpenClawAdapter.rule_magic_command(simplified)
    assert resolved == "/daemon approve", f"{simplified}: 简体侧前提不成立"
    assert OpenClawAdapter.rule_magic_command(traditional) == resolved


@pytest.mark.parametrize("text", ["對", "对", "對嗎", "對吧", "對，好的", "對啊"])
def test_a_traditional_companion_still_cannot_authorize_alone(text):
    """补 `對` 不扩大批准面：应答子句单独出现永远不是授权。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ASCII `-`：Unicode 破折号收了一整排，最常打的那个反而漏了
        ("同意--去执行", "/daemon approve"),
        ("好吧--换个话题", "/new"),
        ("同意——去执行", "/daemon approve"),
        ("停下来-好吗", "/stop"),
        # 句尾礼貌语：_command_clause 只剥得掉 `了`，剩下的 `拜托` 成了命令子句
        ("停下来，拜托了", "/stop"),
        ("停下來，拜託了", "/stop"),
        ("换个话题，麻烦了", "/new"),
        ("換個話題，麻煩了", "/new"),
        ("别找了，拜托了", "/stop"),
        # `_` 在 Python 的 \w 里，所以 \W 剥不掉它
        ("_停下来_", "/stop"),
        ("__停下來__", "/stop"),
        ("_换个话题_", "/new"),
        ("停下来 _", "/stop"),
        ("**停下来**", "/stop"),
    ],
)
def test_ordinary_chat_punctuation_does_not_hide_a_command(text, expected):
    """⚠️ 三处都是「装饰/边界」判据没盖全，命令被包在里面整条落空。

    All were live on main through substring matching, and a rule miss is not
    merely a slower path: the cheap pre-gate returns None before ever reaching
    the LLM classifier, so nothing recovers them.

    The underscore one is a plain bug rather than an omission — Python's word
    class includes ``_``, so its complement never strips it, and a separated
    ``停下来 _`` even makes the underscore itself the selected command clause.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    [
        # 礼貌语本身不是命令，加进词尾表不能把它们变成命令
        "拜托了", "麻烦了", "拜託了", "麻煩了", "太麻烦了", "别麻烦了",
        # `-` 成为边界后，这些普通文本不能冒出命令
        "2024-01-01", "e-mail", "rock-paper-scissors", "--", "-",
        # 纯装饰不能变成命令子句
        "_", "__", "**", "___",
        # 叙述句仍然不是命令（`-` 不该把它们切出一个命令尾巴）
        "我不想停下来，太麻烦了", "他说的是-停下来-那句台词的意思",
    ],
)
def test_the_new_boundaries_do_not_manufacture_commands(text):
    """把 `-` 和 `_` 纳入边界/装饰，不能让普通文本冒出命令。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        # 裸应答带任何句尾标点 —— 子句切分器替它做掉了它自己拒绝做的归一化
        "同意。", "同意！", "同意……", "同意…", "同意——", "我同意——", "没问题……",
        "沒問題⋯⋯", "沒問題。", "我同意…", "同意、", "同意：",
        # 「应答词 + 裸应答」凑不出授权 —— 要补的召回是「应答 + **动作**」
        "好的，同意", "嗯，同意", "对，同意", "對，同意", "可以，我同意",
        "行了，没问题", "批准，同意", "同意，同意", "我同意，没问题",
    ],
)
def test_an_affirmation_needs_a_real_action_beside_it(text):
    """⚠️ 这些在 main 上全是 None，收口改动不该把它们变成批准。

    Two separate leaks, both letting a bare affirmation authorize on its own:

    1. ``_APPROVE_AFFIRMATIONS`` says bare affirmations are matched verbatim with
       no normalization — but the clause splitter performs that normalization for
       it, so ``同意……`` arrives as the exact string ``同意``. The widened set is
       precisely the hesitant, unfinished register.
    2. An affirmation clause used to satisfy the "at least one real clause"
       requirement even with company, so ``好的，同意`` approved. The recall this
       change set out to restore is 应答 + **动作** (``好的，去执行``), not this.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        "同意", "我同意", "没问题", "沒問題", "同意 ", " 同意",
        "同意，去执行", "同意……去执行", "同意——去执行", "好的，去执行",
        "对，去执行", "對，去執行", "好的，删吧", "没问题去执行", "去执行", "删吧",
    ],
)
def test_the_affirmation_narrowing_keeps_every_real_authorization(text):
    """收窄不能连真授权一起收掉：整句就是裸应答、或带了动作子句的，都必须保住。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == "/daemon approve", text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 斜杠当分隔符用（聊天里很常见）
        ("好吧/换个话题", "/new"),
        ("同意/去执行", "/daemon approve"),
        ("停下来/谢谢", "/stop"),
        # 前缀在外、包裹在内：剥完首部虚词还得再剥一次装饰
        ("请「停下来」", "/stop"),
        ("麻烦（换个话题）", "/new"),
        ("你_停下来_", "/stop"),
        ("請「停下來」", "/stop"),
        # 书面疑问式请求（只进宽表，approve 永远看不到）
        ("能否停下来？", "/stop"),
        ("可否换个话题", "/new"),
        ("是否可以停下来", "/stop"),
        ("是否能换个话题", "/new"),
        ("能否停下來", "/stop"),
    ],
)
def test_more_ordinary_phrasings_still_reach_their_command(text, expected):
    """三类召回损失，全部相对 main：`/` 没当分隔符、前缀+包裹的嵌套只剥了一层、
    书面疑问式请求（能否/可否/是否可以）不在任何首部表里。
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    "text",
    [
        # 疑问式请求绝不能授权 —— 它们只在宽表里
        "能否去执行", "可否去執行", "是否可以同意", "是否能删吧", "能否准了",
        # 斜杠成为边界后普通文本不能冒出命令
        "和/或", "a/b", "读/写", "24/7",
        # 包裹里是否定就不算
        "请「不要停下来」", "你_不同意_",
        # 字面 magic word 仍然照走归一化，不受 `/` 分隔符影响
    ],
)
def test_the_new_leads_and_separators_do_not_manufacture_approvals(text):
    """⚠️ `能否/可否/是否可以` 只能进**宽**表：它们是征询，不是授权。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("/stop", "/stop"), ("/daemon approve", "/daemon approve"), ("/new", "/new")],
)
def test_literal_magic_words_survive_the_slash_separator(text, expected):
    """⚠️ `/` 成了子句分隔符，但字面命令由 normalize_magic_command 在更早一层拦下。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("请问能不能停下来？", "/stop"),
        ("請問能不能停下來", "/stop"),
        ("请问可以换个话题吗", "/new"),
        ("请问停下来", "/stop"),
        ("請問換個話題", "/new"),
    ],
)
def test_the_politest_interrogative_lead_still_reaches_its_command(text, expected):
    """⚠️ `请问` 必须进**宽**表，不能只靠中性表里的 `请`。

    The combined expression is ``^(?:SOFT|NEUTRAL)+`` and soft is tried first, so
    a two-character entry there wins over the one-character neutral one. Leave it
    out and neutral's ``请`` eats a single character, stranding ``问能不能停下来``
    with nothing left that matches — the same multi-character-before-its-prefix
    trap these tables have hit eight times now.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) == expected, text


@pytest.mark.parametrize("text", ["请问去执行吗", "請問去執行嗎", "请问同意吗", "请问", "請問"])
def test_the_politest_interrogative_lead_never_approves(text):
    """它是**征询**，approve 一侧看不见它（只在宽表里）。"""  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    assert OpenClawAdapter.rule_magic_command(text) is None, text
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
    # ⚠️⚠️ 钉的是**生成器的输入**（情态表），不是成品表。
    #
    # 前几轮一直在往成品表里补词，每补一轮 reviewer 就找出下一个（应该不应该 /
    # 需不需要 / 愿不愿意…）。真正封闭的那一维是「能进这个位置的情态词」，成品
    # 是它的两种构式（全叠 + 简叠），所以相等断言挂在情态表上。
    #
    # ⚠️ 边界不变：只收情态。能产的那一侧（停不停/听不听/走不走）不枚举——那是
    # 开集，收了会把真命令判成提问。
    from main_logic import music_requests as mr

    assert set(mr._ZH_A_NOT_A_MODALS) == {
        "可以", "能", "能够", "能夠", "会", "會", "该", "該", "应该", "應該",
        "需要", "愿意", "願意", "要", "想", "行", "好", "是", "对", "對",
        "敢", "肯", "值得", "舍得", "捨得", "用", "配",
        "允许", "允許", "乐意", "樂意", "情愿", "情願",
        # 评价类谓词同族（Codex P2 第五十四轮，base 都是 False）。
        "合适", "合適", "方便", "容易", "可能", "清楚", "明显", "明顯",
        "靠谱", "靠譜", "划算", "劃算", "合理", "恰当", "恰當",
    }, mr._ZH_A_NOT_A_MODALS
    # 两种构式都要生成出来，外加用「没」做中缀的 有没有。
    for form in ("应该不应该", "应不应该", "需不需要", "需要不需要",
                 "可不可以", "可以不可以", "是不是", "有没有", "有沒有"):
        assert form in A_NOT_A_TAILS, form
    # 能产的那一侧绝不能混进来。
    for open_form in ("停不停", "听不听", "走不走", "放不放"):
        assert open_form not in A_NOT_A_TAILS, open_form


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
    """⚠️ 排除 ASCII 单引号，而且是**按模块常量**排除、不是手写跳过。

    它在西文名字里是撇号（Guns N' Roses），当开引号会把疑问守卫整个关掉，所以
    实现里刻意把它从开引号集合里去掉了（见 `_ZH_AMBIGUOUS_QUOTE_OPENERS`）。这里
    跟着排除，并单独有一条用例说明这个取舍——不能让它变成「测试悄悄少跑一格」。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    pairs = [
        (o, c) for o, c in mr._QUOTE_PAIRS.items()
        if o not in mr._ZH_AMBIGUOUS_QUOTE_OPENERS
    ]
    assert len(pairs) == len(mr._QUOTE_PAIRS) - len(
        mr._ZH_AMBIGUOUS_QUOTE_OPENERS
    ), "撇号那两格没被排除掉"
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
    # 第三轮补的核心音乐宾语——`停止正在播放的專輯` base 也是 True。
    "专辑", "專輯", "单曲", "單曲", "唱片",
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
        "有一点", "有一點", "稍微", "稍稍", "略微", "比较", "比較",
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


# --- Codex 第三轮 -----------------------------------------------------------


def _playback_ui_nouns() -> list[str]:
    from main_logic import music_requests as mr

    return _alternation(mr._ZH_PLAYBACK_UI_NOUN)


UI_NOUNS = _playback_ui_nouns()


@pytest.mark.parametrize("ui_noun", UI_NOUNS)
def test_a_ui_noun_heading_a_music_object_is_still_a_command(ui_noun):
    """⚠️ 控件名表是前缀匹配，键/功能 又是 键盘/功能性 的词头。

    `停止播放鍵盤音樂` / `幫我停止播放功能性音樂` base 都是 True，被当成「在说
    控件」（Codex P2 第三轮）。判据放在**宾语中心语**上：控件名后面几个字之内
    出现音乐名词，那整段就是音乐宾语。

    ⚠️ 配对反向断言：同一个控件名后面没有音乐名词时，仍然不是命令——反向要求
    「控件名后不许跟汉字」行不通，`停止播放按鈕換個顏色` 后面跟的正是动词。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止播放{ui_noun}音乐') is True
    assert is_explicit_music_cancellation(f'帮我停止播放{ui_noun}换个颜色') is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止播放键盘音乐", "停止播放鍵盤音樂"),
        ("帮我停止播放功能性音乐", "幫我停止播放功能性音樂"),
    ],
)
def test_the_ui_prefix_phrasings_codex_named_are_commands(simplified, traditional):
    """上一条是笛卡尔积，这两句是 Codex 点名的原句（词头比控件名长），另外钉死。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "name", ["Guns N' Roses", "Rock'n'Roll", "Sittin' On The Dock"]
)
def test_an_apostrophe_in_a_latin_name_is_not_a_quote_opener(name):
    """⚠️ ASCII 单引号在西文名字里是**撇号**，不是开引号。

    把它当成没闭合的开引号会把疑问守卫整个关掉：
    `我想停止播放Guns N' Roses是否合适` base 是 False，却被判成停止命令
    （Codex P2 第三轮，又是危险方向——用户在问，歌被停了）。

    ⚠️ 配对断言：真正的成对引号仍然要挡住守卫（那是上一轮修的《好不好》）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'我想停止播放{name}是否合适') is False
    assert is_explicit_music_cancellation(f'我想停止播放{name}可不可以') is False
    assert is_explicit_music_cancellation('帮我停止播放《好不好》') is True


@pytest.mark.parametrize("marker", ["好不好", "可不可以", "好吗", "是不是"])
@pytest.mark.parametrize("conjunction", ["然后", "再", "并", "接着", "顺便"])
def test_a_trailing_question_suppresses_the_clause_by_design(conjunction, marker):
    """⚠️⚠️⚠️ 三轮的账记在这里，别再来第四次。

    * 第三轮：reviewer 提 `帮我停止播放再看看效果好不好`（疑问尾管的是后半句），
      我以「同形状的 吗 在 base 上也是 False」为由驳回。
    * 第十八轮：reviewer 改用**结构信号**（并列连接词）重提，我接受并实现。
    * 第十九轮：reviewer 立刻找出实现的硬伤——连接词是**子串匹配**，而歌名里就
      可能含它。`并蒂莲` / `再见` / `然后呢` 都是真实歌名，于是
      `我想停止播放并蒂莲是否合适`（base 是 False）变成执行取消，一句提问把歌
      停了。三条替代实现（只收多字连接词 / 要求后跟动词 / 要求离播放动词足够远）
      都试过，没有一个能在不引入危险方向的前提下把两类分开。

    ⚠️ 所以按代价取舍：保留边界 = 提问被执行成取消（危险）；不保留 = 并列命令里
    的停止动作被忽略（轻）。取轻的那一侧。用户把歌名加引号时走跨度那一支，
    不受影响。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'帮我停止播放{conjunction}看看效果{marker}'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "title", ["并蒂莲", "再见", "然后呢", "同时", "以及"]
)
def test_a_song_title_containing_a_conjunction_does_not_disable_the_guard(title):
    """⚠️ 与上一条成对：歌名里含并列词时，疑问守卫**必须照常开火**。

    这是第十八轮那版实现的破坏面（base 全是 False，被我改成了执行取消）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{title}是否合适'
    ) is False


@pytest.mark.parametrize("marker", ["好不好", "可不可以", "行不行", "好吗", "是不是"])
def test_a_trailing_question_without_coordination_is_still_a_question(marker):
    """⚠️ 与上一条成对：**没有并列动作**时，句末疑问尾仍然把整句判成提问。

    这是这个 PR 最初要修的那条 P2（`我想停止播放可不可以`）。上一条放开并列结构
    时，最容易顺手把这一整类也放行——两条必须一起看。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for prefix in ("我想", "我要", "帮我"):
        text = f'{prefix}停止播放{marker}'
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("marker", ["好不好", "是不是", "好吗"])
@pytest.mark.parametrize("quote", ["'", "‘"])
def test_an_apostrophe_style_quote_no_longer_shields_a_title(marker, quote):
    """⚠️ 刻意接受的代价：直单引号括起来的歌名不再被当成歌名。

    `帮我停止播放'好不好'` 于是被判成提问而不是命令。换来的是西文名字里的撇号
    （`Guns N' Roses` / `Rock'n'Roll` / `Don't`）不再把守卫关掉——后者在真实
    歌单里常见得多，而且它的失败方向更危险（用户在问，歌被停了）。

    ⚠️ 中文书名号/引号那几对**不受影响**，上面那条笛卡尔积仍然覆盖它们。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr
    from main_logic.music_requests import is_explicit_music_cancellation

    closing = mr._QUOTE_PAIRS[quote]
    assert is_explicit_music_cancellation(
        f'帮我停止播放{quote}{marker}{closing}'
    ) is False
    assert is_explicit_music_cancellation(f'帮我停止播放《{marker}》') is True


# --- Codex 第四轮：两条 P1（回溯爆炸 / 空格漏整卡覆盖）+ 两条 P2 -------------


def test_quoted_span_matching_cannot_backtrack_exponentially():
    """⚠️⚠️ P1：这条判据跑在**用户可控文本**上。

    上一版引用那一支的体内写 `[^。！？!?]*?`（连定界符一起吃），于是
    `《》《》《》…` 能被切成指数多种分段：实测 23 对 542ms、每加一对翻倍，
    而入口只卡 160 字——70 对照样进得来，一条短输入就能占死一个请求 worker。

    ⚠️ 断言**结构 + 耗时**两样：只测耗时的话，哪天体内又被放开、而 CI 机器
    恰好快一点，这条就悄悄过了。
    """  # noqa: DOCSTRING_CJK
    import re
    import time

    from main_logic import music_requests as mr

    # 结构断言按**行为**写，不按写法写：跨度体内必须排除自己那对定界符（这才是
    # 「从任一开引号起只有唯一一种匹配方式」的来源），但**不**排除句末标点
    # （`《你好吗？》` 是合法歌名，见 test_punctuation_inside_a_title_...）。
    # ⚠️ 上一版直接断言正则字面量，于是「放开句末标点」这个正确改动把它打红了——
    # 结构守卫钉到写法上就会这样。
    span = re.compile(f"^(?:{mr._ZH_PAIRED_QUOTED_SPAN})$")
    for opening, closing in mr._QUOTE_PAIRS.items():
        # 直/弯单引号按撇号处理，不进引用体系（见
        # test_an_apostrophe_style_quote_no_longer_shields_a_title）。
        if opening in mr._ZH_AMBIGUOUS_QUOTE_OPENERS:
            continue
        assert span.match(f"{opening}你好吗？{closing}"), (
            f"{opening}{closing} 跨度不认带标点的歌名"
        )
        # ⚠️ 第三十轮之后：**嵌套**（一层）是合法的，而**杂闭合符**必须让跨度失效
        # ——`《晴天」是否合适》` 曾被当成合法标题、把真疑问标记藏了进去。
        assert not span.match(f"{opening}晴天」好不好{closing}"), (
            f"{opening}{closing} 跨度体内没排除其它定界符——杂闭合符会藏住疑问标记"
        )

    # 耗时：入口卡 160 字，这里直接顶到上限。线性实现是微秒级。
    worst = "我想停止播放" + "《》" * 70 + "X"
    assert len(worst) <= 160
    start = time.perf_counter()
    mr.is_explicit_music_cancellation(worst)
    assert time.perf_counter() - start < 1.0, "引用匹配又开始回溯了"


@pytest.mark.parametrize(
    "title",
    [
        # `’` 是 `‘` 的闭合符，不该被当成把 `《` 闭上了。
        "《Don’t好不好》",
        # 嵌套引用：`”` 不该提前闭掉外层的 `《`。
        "《“晴天”好不好》",
        "「Don’t是不是」",
    ],
)
def test_only_the_matching_closer_ends_a_quoted_title(title):
    """⚠️ 上一版用的是**全局闭合符集合**，`《Don’t好不好》` 里 `’` 就把 `《`
    闭上了，`好不好` 又变回「没被引号保护的疑问标记」，整句被判成提问
    （Codex P2）。现在逐对配平：只有 `》` 能闭 `《`。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'帮我停止播放{title}') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止正在播放的", "停止正在播放的"),
        ("帮我停止正在播放的", "幫我停止正在播放的"),
        ("停止正在播放的吧", "停止正在播放的吧"),
        ("关掉正在播放的了", "關掉正在播放的了"),
    ],
)
def test_an_elliptical_object_after_de_is_still_a_command(simplified, traditional):
    """⚠️ 「的」直接收尾时宾语是**省略**掉的，不是名物化（base 全是 True）。

    ⚠️ 配对反向断言：显式的非音乐中心语仍然要挡住，否则这个逃生口等于把
    整条守卫作废。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize(("opening", "closing"), QUOTE_PAIRS)
@pytest.mark.parametrize("determiner", ["", "那首", "這首", "我的"])
def test_a_quoted_title_after_de_is_still_a_command(determiner, opening, closing):
    """⚠️ 括起来的就是**歌名**，不用也没法进词表。

    `停止正在播放的《晴天》` / `停止播放的「夜曲」` / `停止正在播放的那首《晴天》`
    base 全是 True，只认通用音乐名词会把「点名停某一首」整片打成名物化
    （Codex P2 第五轮）。跨度复用逐对配平那个常量。

    ⚠️ 配对反向断言：不带引号的非音乐中心语仍然要挡住。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'停止正在播放的{determiner}{opening}晴天{closing}'
    assert is_explicit_music_cancellation(text) is True, text
    # ⚠️ 反向断言**不带限定词**：`的这首` / `的那首` 里的「首」本身就是完整的
    # 播放对象（`停止正在播放的这首` base 是 True），所以 `的这首代码` 逃生是
    # 对的——那也不是一句中文。把限定词叠进反向句里只会测出一个假缺陷。
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize("space", [" ", "\u3000", "  "])
def test_whitespace_before_a_playback_object_is_skipped(space):
    r"""⚠️ 逃生项前面的空白只能**跳过**，而且判据只在跳过后落到白名单上才放行。

    `停止正在播放的 音乐` base 是 True，被判成名物化（Codex P2 第六轮）。

    ⚠️⚠️ `\s*` 必须写在**内层前视里面**。写成 `的\s*(?!…)` 的话 `\s*` 会回溯成
    零宽、前视在空格那个位置再判一次，等于没改——这条用例连同下面的反向断言
    一起把这个坑钉住。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for obj in ("音乐", "音樂", "《晴天》", "这首歌"):
        text = f'停止正在播放的{space}{obj}'
        assert is_explicit_music_cancellation(text) is True, text
    # 反向：跳过空白后仍然是非音乐中心语时，照旧判成名物化。
    for obj in ("代码", "教程"):
        text = f'我要停止播放的{space}{obj}'
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("punctuation", ["？", "?", "！", "!", "。", "，"])
def test_punctuation_inside_a_title_does_not_break_the_span(punctuation):
    """⚠️ 跨度体内只排除自己那对定界符，**不排除句末标点**。

    `《你好吗？》` 是合法歌名。把 `？` 挡在跨度外面有两面后果，第二面更严重：

    * `停止正在播放的《你好吗？》` base=True → 被打成名物化（少停一次歌）
    * `我想停止播放《你好吗？》是否合适` base=False → **变成执行取消**：跨度过不去，
      后面的「是否」就到不了，守卫开不了火（Codex P2 第七轮）

    子句切分那一步本来就认引号（`_split_music_request_clauses`），带标点的歌名
    根本不会被切开，所以这里也必须让它整段通过。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    title = f'《你好吗{punctuation}》'
    assert is_explicit_music_cancellation(f'停止正在播放的{title}') is True
    assert is_explicit_music_cancellation(f'我想停止播放{title}是否合适') is False


@pytest.mark.parametrize(
    "phrasing",
    [
        # 不带「的」，直接跟歌名/歌手——点歌功能最主要的用法
        '停止播放晴天',
        '停止播放周杰伦的歌',
        # 「的」后面是引号歌名 / 限定词 / 通用音乐名词 / 省略
        '停止正在播放的《晴天》',
        '停止正在播放的这首歌',
        '停止正在播放的音乐',
        '停止正在播放的',
    ],
)
def test_the_ways_to_stop_a_named_track_that_do_work(phrasing):
    """⚠️ 这条是那个**刻意接受的代价**的配套说明，不是随手加的冒烟用例。

    `停止正在播放的晴天`（「的」+ 不带引号的歌名）判不出来——它和
    `停止播放的代码` 在句子表层完全同构，要分开得知道「晴天」是首歌，那是
    开集，这个文件已经在「枚举播放动词后面能跟什么」上栽过两次。

    代价被限制在很窄的一格：上面这些说法**全都仍然有效**。哪天有人想「修好」
    那一格，先看看这条用例——放开它就等于把 `停止播放的代码` 一起放回去。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(phrasing) is True, phrasing


def test_a_bare_name_after_de_is_deliberately_not_a_command():
    """⚠️ 与上一条成对：这一格**故意**判不出来，而且它和缺陷本体同构。

    ⚠️ 这一格后来**缩小过一次**：`停止播放的夜曲` 现在能判出来了，因为「夜曲」
    以音乐名词「曲」收尾、落在子句末尾（第十轮那条「自由修饰语 + 音乐名词 +
    子句边界」）。剩下的才是真正判不出来的：歌名/歌手名里**没有任何音乐名词**
    的那些（晴天 / 周杰伦）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    # ⚠️ 这一格又缩小了一次：带**进行体**（正在播放的X）时现在判得出来——进行体
    # 本身就说明「正在被播放的东西」是播放对象，X 是开集专名也没关系
    # （Codex P2 第十九轮）。剩下判不出来的只有「不带正在、且没有任何音乐名词」
    # 这一格。
    for text in ('停止播放的晴天', '停止播放的周杰伦'):
        assert is_explicit_music_cancellation(text) is False, text
    # 反过来：以音乐名词收尾、或带进行体的，现在都判得出来。
    assert is_explicit_music_cancellation('停止播放的夜曲') is True
    assert is_explicit_music_cancellation('停止正在播放的晴天') is True
    assert is_explicit_music_cancellation('停止正在播放的周杰伦') is True
    # 同构的缺陷本体——放开上面那一格就等于把这些一起放回去。
    for text in ('停止播放的代码', '停止播放的教程', '停止播放的文档'):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止正在播放的这首", "停止正在播放的這首"),
        ("停止正在播放的这一首", "停止正在播放的這一首"),
        ("停止正在播放的下一首", "停止正在播放的下一首"),
    ],
)
def test_a_measure_phrase_can_end_the_playback_object(simplified, traditional):
    """⚠️ `_ZH_MUSIC_NOUN_MODIFIER` 不能单独站住——它后面永远还要求一个中心语。

    `停止正在播放的这首` / `的下一首` base 都是 True，被打成名物化
    （Codex P2 第八轮）。把「首」收成中心语即可。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("我要停止播放的播放器代码", "我要停止播放的播放器代碼"),
        ("我要停止播放的广播代码", "我要停止播放的廣播代碼"),
        ("我要停止播放的听歌功能", "我要停止播放的聽歌功能"),
    ],
)
def test_the_window_cannot_skip_a_rejected_playback_verb(simplified, traditional):
    """⚠️⚠️ 窗口不能跨过一个**已经被名物化守卫否掉**的播放动词。

    `我要停止播放的播放器代码` 里第一个「播放」被守卫拦下之后，`.{0,6}` 直接
    跳到「播放器」里那个「播放」再匹配一次——那时守卫看到的是「器」而不是
    「的」，缺陷本体（问代码却把歌停掉）就从后门回来了（Codex P2 第八轮）。

    ⚠️ 配对正向断言：同样含两个播放动词、但不是名物化的句子仍然是命令。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('别给我放歌') is True
    assert is_explicit_music_cancellation('停止播放清單裡的紅心歌') is True


def test_the_determiner_table_matches_linearly():
    """⚠️ `_ZH_MUSIC_NOUN_MODIFIER` 的扫描段必须排除方位词本身。

    留着 `[^，,。！!？?]{1,6}?[里裡上中内內裏]的` 的话，`x里的里的里的…` 在每个
    位置都有多种切法（懒扫描 × 外层 `{0,8}`）。这张表本来只在名词尾那一支用，
    我把它嵌进名物化守卫的前视之后会在每个播放动词位置各评估一次、界面控件
    那一支还套了个 `{0,4}` 窗口再乘一遍——实测 93 字输入单次 20ms、基线
    0.02ms（CodeRabbit）。

    ⚠️ 断言**结构 + 耗时**：只测耗时会因机器快而假绿，只测结构不知道代价。
    """  # noqa: DOCSTRING_CJK
    import re
    import time

    from main_logic import music_requests as mr

    # 结构：那一支必须是**原子组**。
    # ⚠️ 不能改成「扫描段排除方位词」——设备名自己就可能带方位字
    # （`樓上音箱裡的` / `車內音響裡的` / `中控台上的`），排除法会把它们打死；
    # 见 test_a_location_char_inside_the_device_name_still_stops。
    assert "(?>" in mr._ZH_MUSIC_NOUN_MODIFIER, (
        "方位结构那一支不再是原子组——多项式回溯会回来"
    )
    assert re.compile(mr._ZH_MUSIC_NOUN_MODIFIER), "限定词表编译不了"

    # 耗时：CodeRabbit 给的形状，顶到 160 字上限。
    worst = ("我要停止播放按钮" + "x里的" + "里的" * 40 + "代码")[:160]
    start = time.perf_counter()
    for _ in range(5):
        mr.is_explicit_music_cancellation(worst)
    assert (time.perf_counter() - start) / 5 < 0.05, "限定词密集输入又开始回溯了"


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止楼上音箱里的音乐", "停止樓上音箱裡的音樂"),
        ("停止车内音响里的音乐", "停止車內音響裡的音樂"),
        ("停止正在播放的家里音箱里的音乐", "停止正在播放的家裡音箱裡的音樂"),
        ("停止中控台上的音乐", "停止中控台上的音樂"),
    ],
)
def test_a_location_char_inside_the_device_name_still_stops(simplified, traditional):
    """⚠️ 设备名自己就可能带方位字（楼**上**音箱 / 车**内**音响 / **中**控台）。

    修上一轮那个 20ms 回溯热点时，第一版把扫描段写成「排除方位词」，这一族当场
    全失配（Codex P2 第九轮，base 全是 True）。原子组既保住了不回溯，也保住了
    「在 1..6 个字里找那个方位词」的原语义——两个性质各取一半。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止正在播放的古典音乐", "停止正在播放的古典音樂"),
        ("停止正在播放的轻柔音乐", "停止正在播放的輕柔音樂"),
        ("停止正在播放的华语歌曲", "停止正在播放的華語歌曲"),
        ("停止播放的民谣歌单", "停止播放的民謠歌單"),
    ],
)
def test_a_free_modifier_before_a_music_head_is_still_a_command(
    simplified, traditional
):
    """⚠️ 曲风/描述性修饰语是**开集**（古典/轻柔/华语/民谣/纯音乐…列不完）。

    所以这一支不枚举修饰语，改为要求**音乐名词落在子句末尾**——跟这个文件里
    名词尾那一支同一招（base 全是 True，Codex P2 第十轮）。

    ⚠️⚠️ 右边界是必须的。只要求「后面某处有个音乐名词」的话，
    `我要停止播放的听歌功能` 里「歌」在中间、中心语是「功能」，第八轮刚修好的
    那一族（问功能却把歌停掉）会立刻回来——所以下面配对断言死钉着它。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的听歌功能') is False
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize(
    "malformed", ["《晴天」", "「晴天》", "『晴天】", "【晴天』"]
)
def test_a_malformed_quote_span_fails_closed(malformed):
    """⚠️ 写坏的引号要 **fail closed**（保住疑问守卫），不能当成「标记在引号里」。

    `我想停止播放《晴天」是否合适` 里 `《` 没有配对的 `》`，跨度过不去、守卫就到
    不了后面的「是否」，一句提问被判成停止命令（Codex P2 第十轮，base 是 False
    ——又是危险方向）。

    判据：开引号如果**不是某个完整跨度的开头**，就当普通字符吃掉。它跟跨度那一支
    天然互斥（一个要求能闭合、一个要求不能），不引入歧义。

    ⚠️ 配对断言：**配对正确**的引号仍然要挡住守卫（那是第三轮修的《好不好》）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{malformed}是否合适'
    ) is False
    assert is_explicit_music_cancellation('帮我停止播放《好不好》') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止正在播放的由周杰伦演唱的歌曲", "停止正在播放的由周杰倫演唱的歌曲"),
        ("停止正在播放的那首特别好听的华语歌曲", "停止正在播放的那首特別好聽的華語歌曲"),
        ("停止播放的我上周收藏的那些歌", "停止播放的我上週收藏的那些歌"),
    ],
)
def test_a_long_relative_clause_before_the_music_head_still_stops(
    simplified, traditional
):
    """⚠️ 修饰语这一段**不设字数上限**，只受子句边界约束。

    `由周杰伦演唱的` 是 7 个字，卡 `{0,6}` 当场失配（Codex P2 第十一轮，base 是
    True）。定关系从句能有多长同样是开集，设几就会被下一个例子顶穿——真正干活
    的是右边界（音乐名词必须落在子句末尾），窗口大小不承担判据。

    ⚠️ 配对断言：右边界仍然拦住第八轮那一族。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的听歌功能') is False
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize(
    "adverb", ["顺便", "順便", "同时", "同時", "一起", "一并", "一併", "也", "再"]
)
def test_a_temporal_clause_with_a_coordinating_adverb_is_a_command(adverb):
    """⚠️ 「的时候」跟「的同时」要**分开看**。

    `請停止播放的時候順便關閉螢幕` 里有「順便」这类并列副词，说明两个动作都在被
    要求，停止是命令（base 是 True，Codex P2 第十一轮）。判据是「后一个动作被
    标记成附加的」——附加意味着前一个动作同样是被要求的。并列副词是封闭词类。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'请停止播放的时候{adverb}关闭屏幕'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止播放的时候通知我", "停止播放的時候通知我"),
        ("停止播放的时候提醒我", "停止播放的時候提醒我"),
        ("停止播放的时候会有提示音吗", "停止播放的時候會有提示音嗎"),
    ],
)
def test_a_bare_temporal_clause_is_deliberately_not_a_command(simplified, traditional):
    """⚠️ 这是一条**刻意不跟随 base** 的判定，不是漏改。

    「的时候」引出的是**时间条件**：用户要的是「到那时提醒我」，不是「现在停」。
    base 在这里是 True，但那是 base 的错——真按它执行会无端把歌停掉，正是这个
    PR 要修的那类破坏（问一件事、歌被停掉）。

    ⚠️ 与上一条成对：只有并列副词出现时才认成命令。哪天有人把「的时候」整个
    放行，上一条仍绿而这一条会红。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止正在播放的周杰伦的《晴天》", "停止正在播放的周杰倫的《晴天》"),
        ("停止正在播放的由周杰伦演唱的《晴天》", "停止正在播放的由周杰倫演唱的《晴天》"),
        ("停止正在播放的古典版《晴天》", "停止正在播放的古典版《晴天》"),
    ],
)
def test_a_free_modifier_before_a_quoted_title_is_still_a_command(
    simplified, traditional
):
    """⚠️ 自由修饰语那一支的收尾**除了音乐名词也可以是引号歌名**。

    括起来的就是歌名，跟显式音乐名词是同一等级的证据（base 全是 True，
    Codex P2 第十二轮）。右边界仍然要求它落在子句末尾。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


def test_a_title_with_inner_punctuation_cannot_hide_a_trailing_question_mark():
    """⚠️ 裸问号那条判据要能**穿过配平的引用跨度**。

    `我想停止播放《你好吗？》？` 里歌名自带问号，字符类在标题内部那个 `？` 上
    断掉，句末真正的裸问号就看不见了，一句提问被判成停止命令
    （Codex P2 第十二轮，base 是 False——危险方向）。

    ⚠️ 配对断言：同一个标题**不带**句末问号时仍然是命令。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation('我想停止播放《你好吗？》？') is False
    assert is_explicit_music_cancellation('我想停止播放《你好吗？》?') is False
    assert is_explicit_music_cancellation('停止正在播放的《你好吗？》') is True


@pytest.mark.parametrize("space", [" ", "\u3000", "  "])
def test_whitespace_before_de_does_not_bypass_the_nominalization_guard(space):
    """⚠️⚠️ 「的」**前面**的空白同样要跳过（Codex P2 第十三轮，危险方向）。

    入口的 normalize 只把连续空白压成一个、不会删掉它，于是
    `我要停止播放 的代码` 里前视看到的是空格而不是「的」，守卫整个失效、
    问代码照样把歌停掉。

    ⚠️ tempered window 也要一起改：`我要停止播放 的播放器代码` 里窗口同样得把
    「播放动词 + 空白 + 的」认成被拒的候选，否则它会跳到后面那个「播放」。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for obj in ("代码", "教程", "播放器代码"):
        text = f'我要停止播放{space}的{obj}'
        assert is_explicit_music_cancellation(text) is False, text
    # 配对正向：空白跳过之后确实是音乐宾语时仍然是命令。
    assert is_explicit_music_cancellation(f'停止正在播放{space}的音乐') is True


@pytest.mark.parametrize(
    "marker", ["是否适合", "能否换成", "可否换成", "是否合适"]
)
def test_a_quoted_title_after_the_marker_does_not_hide_the_guard(marker):
    """⚠️ 疑问标记**之后**的尾巴也要能穿过配平跨度。

    `我想停止播放是否适合《你好吗？》` 里标题自带问号，尾巴在它上面断掉、守卫
    开不了火，一句提问被判成停止命令（base 是 False，危险方向；Codex P2 第十三
    轮）。标记之前那一侧上一轮已经修过，这是对称的另一半。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}《你好吗？》'
    ) is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要放的比刚才大声", "不要放的比剛才大聲"),
        ("不要放的比之前小声", "不要放的比之前小聲"),
        ("不要放的比昨天轻一点", "不要放的比昨天輕一點"),
    ],
)
def test_a_comparative_degree_complement_is_still_a_command(simplified, traditional):
    """⚠️ 比较对象是**开集**（比刚才/比之前/比昨天/比那首…），所以不枚举它，
    改为要求整段以**程度词**收尾（base 全是 True，Codex P2 第十三轮）。

    ⚠️ 配对反向断言：`停止播放的比赛结果` 不以程度词收尾，仍然是名物化。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('停止播放的比赛结果') is False


@pytest.mark.parametrize(
    "queue", ["播放队列", "播放隊列", "播放佇列", "队列", "隊列", "佇列"]
)
def test_a_playback_queue_after_de_is_still_a_command(queue):
    """播放队列跟 `_ZH_PLAYBACK_COMPOUND_NOUN` 是同一族词，base 是 True。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{queue}') is True


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要放的比刚才在客厅听到的大声", "不要放的比剛才在客廳聽到的大聲"),
        ("不要放的比昨天在车里听的时候大声", "不要放的比昨天在車裡聽的時候大聲"),
    ],
)
def test_a_long_comparative_target_is_still_a_command(simplified, traditional):
    """⚠️ 比较对象不设字数上限——跟自由修饰语那一支同一个理由：设几都会被下一个
    例子顶穿，干活的是「以程度词收尾」这个条件（base 是 True，Codex P2 第十四轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('停止播放的比赛结果') is False


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("停止播放功能正在自动循环的歌曲", "停止播放功能正在自動循環的歌曲"),
        ("停止播放按钮所控制的客厅音乐", "停止播放按鈕所控制的客廳音樂"),
        ("停止播放控件触发后出现的声音", "停止播放控件觸發後出現的聲音"),
    ],
)
def test_a_control_noun_heading_a_long_music_object(simplified, traditional):
    """⚠️ 控件名后面找音乐中心语的窗口也不设上限（base 全是 True）。

    ⚠️ 配对反向断言：整句里根本没有音乐名词时仍然判成「在说控件」。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    for text in ('帮我停止播放按钮换个颜色', '停止播放功能吧', '停止播放键'):
        assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "marker", ["应不应该", "應不應該", "要不要", "想不想"]
)
def test_the_remaining_modal_a_not_a_forms(marker):
    """⚠️ 情态重叠式补齐。`我想停止播放应不应该换成《你好吗？》` 是**危险方向**：
    引号跨度正确地把标题里的疑问词藏了起来，而这个真正的情态标记没被认出来，
    于是一句提问执行了取消（Codex P2 第十四轮）。

    ⚠️ 句首那个 A-not-A（`要不要停止播放`）走的是 `_ZH_REQ_PREFIX` 里的
    `(?!不)`，跟这张表互不干扰——两条都断言一下。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}换成《你好吗？》'
    ) is False
    assert is_explicit_music_cancellation(f'{marker}停止播放') is False


@pytest.mark.parametrize("marker", ["有无", "有無"])
def test_the_polar_question_marker_family_is_complete(marker):
    """⚠️ 有无/有無 跟 是否/能否/可否 是同族的极性疑问标记。

    漏了它又是危险方向：引号跨度把标题内的疑问词藏起来之后，真正的标记没被
    认出来就会执行取消（`我想停止播放有无必要换成《你好吗？》`，base 是 False，
    Codex P2 第十五轮）。这已经是同一个耦合第二次咬人（上一轮是 应不应该）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}必要换成《你好吗？》'
    ) is False
    assert is_explicit_music_cancellation(f'我想停止播放{marker}必要') is False


@pytest.mark.parametrize("abbrev", ["OST", "ost", "Ost", "BGM", "bgm"])
def test_a_latin_audio_abbreviation_after_de_is_still_a_command(abbrev):
    """⚠️ OST/BGM 这类拉丁缩写写成字符类放在 `_ZH_MUSIC_OBJECT_NOUN` 里，
    **不放进** `_ZH_SOUNDTRACK_NOUN`——那张表被测试按「扁平 CJK 词表」拆解做
    笛卡尔积，混进字符类会让拆解当场报错（比较式补语那条已经踩过一次）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{abbrev}') is True


@pytest.mark.parametrize(
    "marker",
    ["应该不应该", "應該不應該", "需不需要", "需要不需要", "愿不愿意",
     "有没有", "有沒有", "能不能够"],
)
def test_generated_modal_a_not_a_forms_are_questions(marker):
    """⚠️ 这一族改成**从情态表生成**，不再手抄成品。

    前几轮每补一个成品词，reviewer 就找出下一个（能不能 → 会不会 → 该不该 →
    应不应该 → 应该不应该 → 需不需要…）。封闭的那一维是情态词本身，成品是它的
    两种构式（全叠 / 简叠），所以这里参数化的每一个都不是手工加进表里的。

    ⚠️ 这条一直是危险方向：引号跨度藏住标题内疑问词之后，真标记没被认出来就会
    执行取消。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}换成《你好吗？》'
    ) is False


@pytest.mark.parametrize(
    "audio", ["白噪音", "白噪聲", "白噪声", "噪音", "录音", "錄音", "ASMR", "asmr"]
)
def test_a_non_song_audio_object_after_de_is_still_a_command(audio):
    """⚠️ 非歌曲类音频对象也是播放对象（base 全是 True）。

    ⚠️ 配对反向断言：**不收「广播」**——`我要停止播放的广播代码` 是第八轮修过的
    缺陷本体（问代码却把歌停掉），收了它当场回归。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{audio}') is True
    assert is_explicit_music_cancellation('我要停止播放的广播代码') is False


@pytest.mark.parametrize(
    "ambient", ["环境音", "環境音", "雨声", "雨聲", "海浪声", "提示音", "风声", "白噪音"]
)
def test_an_audio_object_ending_in_a_sound_suffix_is_a_command(ambient):
    """⚠️ 这一族改成**后缀规则**，不再逐个补词。

    环境音/雨声/海浪声/提示音… 是开集，但它们的**构词是闭的**——音频对象几乎都
    以「声/聲/音」收尾（Codex 第十五~十七轮各补一个词，同一个跑步机）。

    ⚠️ 配对反向断言：缺陷本体那一族都不以 声/音 收尾，照旧被挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{ambient}') is True
    for blocked in ('我要停止播放的代码', '我要停止播放的教程',
                    '我要停止播放的听歌功能', '我要停止播放的广播代码'):
        assert is_explicit_music_cancellation(blocked) is False, blocked


@pytest.mark.parametrize(
    ("simplified", "traditional"),
    [
        ("不要放的跟刚才一样大声", "不要放的跟剛才一樣大聲"),
        ("不要放的像刚才那么大声", "不要放的像剛才那麼大聲"),
        ("不要放的没有刚才那么大声", "不要放的沒有剛才那麼大聲"),
        ("不要放的比刚才大声", "不要放的比剛才大聲"),
    ],
)
def test_any_comparison_frame_before_a_degree_word_is_a_command(
    simplified, traditional
):
    """⚠️ 比较框架本身是开集（比… / 跟…一样 / 像…那么 / 没…那么 / 不如…）。

    所以不再要求以「比」开头，干活的从来是**以程度词收尾**这个条件
    （base 全是 True，Codex P2 第十七轮）。

    ⚠️ 配对反向断言：`停止播放的比赛结果` 不以程度词收尾，仍然是名物化。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (simplified, traditional):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('停止播放的比赛结果') is False
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize("station", ["电台", "電台", "网络电台", "網路電台"])
def test_a_radio_station_after_de_is_still_a_command(station):
    """电台不以 声/音 收尾，所以后缀规则盖不到，仍需进对象表（base 是 True）。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放的{station}') is True


@pytest.mark.parametrize(
    "name", ["晴天", "周杰伦", "周杰倫", "Taylor Swift", "夜曲", "并蒂莲"]
)
@pytest.mark.parametrize("progressive", ["正在播放", "正在放", "正播放"])
def test_a_progressive_aspect_makes_any_following_name_a_playback_object(
    progressive, name
):
    """⚠️ 「正在播放的X」里的 X **不需要**是白名单音乐名词。

    进行体本身已经说明「正在被播放的东西」就是播放对象，X 是歌名/歌手名这类
    开集专名也没关系（base 全是 True，Codex P2 第十九轮）。这条把第七轮声明的
    那格「刻意接受的代价」又缩小了一次。

    ⚠️ 配对反向断言：缺陷本体那一族**不带**「正在」——`我要停止播放的代码` 说的
    是「停止播放」这件事的代码，不是「正在播放的代码」，所以照旧被挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{progressive}的{name}') is True
    for blocked in ('我要停止播放的代码', '我想停止播放的教程',
                    '我要停止播放的听歌功能'):
        assert is_explicit_music_cancellation(blocked) is False, blocked


@pytest.mark.parametrize("space", ["", " ", "\u3000", "  "])
@pytest.mark.parametrize("name", ["晴天", "Taylor Swift", "周杰伦"])
def test_the_progressive_exception_survives_whitespace(space, name):
    r"""⚠️ 进行体后视要挂在**动词末尾**，不能挂在「的」后面。

    挂在「的」后面就得写成 `(?<!正在播放的)` 这种定长后视，而 Python 的后视必须
    定长——`停止正在播放 的晴天` 中间多个空格就对不上了（Codex P2 第二十一轮）。
    ⚠️ 空白在这个文件里已经咬过五次（限定词后 / 目标与的之间 / 第二个的后 /
    续接前 / 的前），这是第六次，所以这次直接换挂载点而不是再加一处 `\s*`。

    ⚠️⚠️ 覆盖范围要说清楚（CodeRabbit）：`is_explicit_music_cancellation` 入口会先
    做 `" ".join(text.split())`，而 `str.split()` 把 `　` 和连续空格**一起**
    归一化成一个 ASCII 空格。所以这条参数化验的是**端到端行为**（含归一化），
    并不能单独验证定长后视对全角空格的处理——那一半由下面
    `test_the_lookbehind_mount_point_survives_raw_whitespace` 在**归一化之前**
    直接打正则来验。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止正在播放{space}的{name}') is True
    # 配对反向：不带进行体时照旧被挡，空白也不例外。
    assert is_explicit_music_cancellation(f'我要停止播放{space}的代码') is False


@pytest.mark.parametrize(
    "marker",
    ["允不允许", "允許不允許", "乐不乐意", "樂不樂意", "情不情愿", "情不情願"],
)
def test_permission_and_volition_modals_are_generated(marker):
    """⚠️ 情态表补 允许/乐意/情愿。生成器不动——两种构式自动铺开。

    ⚠️ 这条一直是危险方向：引号跨度正确藏住标题内疑问词之后，真标记不在表里就
    没有任何标记能触发守卫，一句提问直接执行取消。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}换成《你好吗？》'
    ) is False


@pytest.mark.parametrize(
    "aspect", ["正在", "正", "当前", "當前", "目前", "现在", "現在"]
)
@pytest.mark.parametrize("name", ["晴天", "Taylor Swift"])
def test_every_current_playback_aspect_marks_the_object(aspect, name):
    """⚠️ 当下体（当前/目前/现在播放的X）跟进行体（正在播放的X）是同一个构式。

    手写那三条后视只覆盖了「正在」那一族（Codex P2 第二十二轮）。副词和播放动词
    都是封闭词类，改成**笛卡尔积生成**定长后视，一次铺开。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{aspect}播放的{name}') is True
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize(
    "marker",
    ["为什么", "為什麼", "为何", "為何", "怎么", "怎麼", "怎样", "怎樣",
     "如何", "干嘛", "幹嘛"],
)
def test_wh_question_markers_are_recognized(marker):
    """⚠️ 疑问标记到这里已经是**三族**：极性、情态 A-not-A、疑问代词/副词。

    三族的触发方式一模一样：引号跨度正确地把标题里的 `？` 藏起来之后，只要真标记
    不在表里，整句就没有任何标记能触发守卫，一句提问直接执行取消
    （Codex P2 第二十三轮，前两族分别在第十四、十五、二十一轮）。

    ⚠️ 疑问代词/副词是封闭类，可以列干净；只收**只能用于提问**的那些——
    `什么` / `哪` 还能出现在「没什么」「哪怕」里，收了会把普通命令判成提问。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}会换成《你好吗？》'
    ) is False
    # 配对反向：普通命令不受影响。
    for command in ('帮我停止播放红心歌单', '停止播放', '不要放晴天',
                    '停止正在播放的晴天'):
        assert is_explicit_music_cancellation(command) is True, command


@pytest.mark.parametrize(
    "marker",
    ["什么时候", "什麼時候", "何时", "何時", "多久", "几时", "幾時",
     "哪里", "哪裡", "哪儿", "哪兒", "什么地方", "什麼地方", "哪一首", "哪首"],
)
def test_compound_wh_markers_are_recognized(marker):
    """⚠️ 复合疑问词（什么时候 / 何时 / 哪里 / 哪一首）。

    第二十三轮我收 wh 那一族时刻意没收 `什么` / `哪` 的**裸形**——它们在
    「没什么」「哪怕」里不是提问。但**复合形**只能用于提问，所以可以收
    （Codex P2 第二十四轮）。收词边界没变，变的是我对边界的应用漏了复合形。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}会换成《你好吗？》'
    ) is False
    for command in ('帮我停止播放红心歌单', '停止播放', '不要放晴天'):
        assert is_explicit_music_cancellation(command) is True, command


@pytest.mark.parametrize("verb", ["播放", "放", "播", "听", "聽"])
@pytest.mark.parametrize("aspect", ["正在", "当前", "目前"])
def test_the_progressive_exception_covers_every_playback_verb(aspect, verb):
    """⚠️ 进行体那边另抄了一份只有 播放/放/播 的动词表，漏了 听/聽。

    `_ZH_NEGATIVE_MUSIC` / `_ZH_DIRECT_MUSIC_STOP` 都把 听/聽 当播放动词，
    于是 `停止正在听的晴天` 掉了下来（Codex P2 第二十五轮）。「同一张表两处各写
    一份、然后漂开」在这个文件里已经是第 N 次——这次提成共用常量
    `_ZH_PLAYBACK_VERBS`，进行体那边直接引用。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{aspect}{verb}的晴天') is True
    # 配对反向：不带进行体时照旧被挡。
    assert is_explicit_music_cancellation(f'我要停止{verb}的代码') is False


@pytest.mark.parametrize("space", ["", " ", "\u3000", "  ", "\t"])
def test_the_lookbehind_mount_point_survives_raw_whitespace(space):
    """⚠️ 这条在**归一化之前**直接打正则，补上端到端用例验不到的那一半。

    入口的 `" ".join(text.split())` 会把 `\u3000`、Tab、连续空格一起归一化成一个
    ASCII 空格，所以端到端参数化其实只验到了归一化行为（CodeRabbit）。进行体后视
    挂在**动词末尾**这件事，只有绕过归一化才验得到。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    raw = f'停止正在播放{space}的晴天'
    assert mr._ZH_NEGATIVE_MUSIC.search(raw), raw
    # 反向：不带进行体时，同样的原始文本仍然被守卫挡住。
    assert not mr._ZH_NEGATIVE_MUSIC.search(f'我要停止播放{space}的代码')


@pytest.mark.parametrize("marker", ["谁", "誰", "哪个", "哪個", "哪些", "多少"])
def test_indefinite_capable_wh_markers_are_still_treated_as_questions(marker):
    """⚠️ 这几个 wh 词有**非疑问用法**（谁都行 / 哪个都可以 / 多少有点）。

    严格按第二十三轮我自己划的边界（「只收只能用于提问的」）本该不收。但代价
    不对称：不收 = `我想停止播放谁唱的《你好吗？》` 执行取消（危险方向）；
    收 = `帮我停止播放谁的歌都行` 判成提问、少停一次歌（轻）。取轻的那一侧，
    跟这个文件里其它十几处取舍一致（Codex P2 第二十六轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}唱的《你好吗？》'
    ) is False
    for command in ('帮我停止播放红心歌单', '停止播放', '不要放晴天'):
        assert is_explicit_music_cancellation(command) is True, command


@pytest.mark.parametrize("wh", ["什么", "什麼", "哪", "几", "幾"])
@pytest.mark.parametrize("measure", ["", "张", "首", "个", "部"])
@pytest.mark.parametrize("noun", ["歌", "音乐", "音樂", "专辑", "歌单"])
def test_music_specific_wh_compounds_are_questions(wh, measure, noun):
    """⚠️ 「裸形歧义、复合形不歧义」那条规则的另一半。

    `什么` / `哪` / `几` 裸用时可能是「没什么」「哪怕」「几乎」，但后面跟**音乐
    名词**时只能是提问（Codex P2 第二十七轮）。音乐名词直接复用
    `_ZH_MUSIC_OBJECT_NOUN`（为此把那张表的定义挪到了疑问表之前），量词是闭集。

    ⚠️ 配对反向断言：普通命令不受影响。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{wh}{measure}{noun}换成《你好吗？》'
    assert is_explicit_music_cancellation(text) is False, text
    for command in ('帮我停止播放红心歌单', '停止播放', '停止正在播放的晴天'):
        assert is_explicit_music_cancellation(command) is True, command


@pytest.mark.parametrize("aspect", ["还在", "還在", "仍在", "仍", "还"])
@pytest.mark.parametrize("verb", ["播放", "听", "聽"])
def test_continuative_aspect_also_marks_the_object(aspect, verb):
    """「还在/仍在」是同一个当下体，只是带持续义（base 是 True）。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{aspect}{verb}的晴天') is True
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


def test_the_control_noun_lookahead_stays_linear():
    """⚠️ 控件名那条前瞻扫的是「后面还有没有音乐宾语」，**不需要**带限定词的
    完整 `_ZH_MUSIC_HEAD_AFTER_DE`。

    带限定词的版本开头是 `{0,8}` 的限定词组，会在扫描的每个位置重算一遍：
    149 字对抗输入单次 47ms（Codex P2 第二十九轮）。换成便宜的「音乐名词 |
    配平引号跨度」之后 0.13ms，语义不变——限定词只影响宾语从哪里起算，不影响
    「有没有音乐宾语」这个判断。

    ⚠️ 结构 + 耗时双断言：只测耗时会因机器快而假绿。
    """  # noqa: DOCSTRING_CJK
    import time

    from main_logic import music_requests as mr

    after_ui = mr._ZH_PLAYBACK_NOT_NOMINALIZED.split(mr._ZH_PLAYBACK_UI_NOUN)[-1]
    assert mr._ZH_MUSIC_NOUN_MODIFIER not in after_ui, (
        "控件名前瞻又把带 {0,8} 限定词组的表达式放回扫描里了"
    )

    worst = ("我要停止播放按钮" + "这个里的" * 35 + "X")[:160]
    start = time.perf_counter()
    for _ in range(3):
        mr.is_explicit_music_cancellation(worst)
    assert (time.perf_counter() - start) / 3 < 5.0, "控件名前瞻又变回非线性了"
    # 语义没变：句尾有音乐宾语的仍然是命令，没有的仍然当控件。
    assert mr.is_explicit_music_cancellation('停止播放键盘音乐') is True
    assert mr.is_explicit_music_cancellation('帮我停止播放按钮换个颜色') is False


@pytest.mark.parametrize(
    "malformed", ["《晴天」是否合适》", "「晴天』能否更换」", "【晴天》怎么换成】"]
)
def test_a_mismatched_closer_inside_a_span_makes_it_malformed(malformed):
    """⚠️ 跨度体内要排除**所有**定界符，不能只排自己那一对。

    只排自己那对时，`《晴天」是否合适》` 会被当成一个合法跨度（`」` 不在排除集
    里），真正的疑问标记就被藏进了「标题」里，一句提问执行了取消
    （Codex P2 第三十轮，base 是 False——危险方向）。

    ⚠️ 「不是完整跨度的开头」那个条件必须**与跨度定义逐字一致**：改了体定义却
    没同步它，会出现两边都不认、前缀整个过不去的情况（本轮第一版就是这样）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'我想停止播放{malformed}') is False
    # 配对：合法跨度（含一层嵌套）仍然保护标题。
    assert is_explicit_music_cancellation('帮我停止播放《好不好》') is True
    assert is_explicit_music_cancellation('帮我停止播放《“晴天”好不好》') is True


@pytest.mark.parametrize("space", ["", " ", "\u3000"])
@pytest.mark.parametrize("aspect", ["正在", "当前", "还在"])
def test_whitespace_inside_the_aspect_phrase(aspect, space):
    r"""⚠️ 体标记和播放动词之间也可能有空格。

    Python 后视必须定长，`\s*` 塞不进去，所以把「有无空格」做进笛卡尔积——
    入口 normalize 把连续空白压成**一个** ASCII 空格，所以只需要两种
    （Codex P2 第三十轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'停止{aspect}{space}播放的晴天') is True
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize(
    "modifier", ["", "循环", "循環", "后台", "後台", "单曲", "随机", "自动"]
)
@pytest.mark.parametrize("aspect", ["正在", "当前", "还在"])
def test_a_playback_modifier_inside_the_aspect_phrase(aspect, modifier):
    """⚠️ 体标记和播放动词之间还可能夹一个**播放方式修饰语**（循环/后台/单曲…）。

    修饰语是开集，所以用 `.` 占位而不是枚举；但 Python 的后视必须**定长**，
    于是按「总宽度」分组——同宽度的组合塞进一个 `(?<!(?:…|…))`，两百多种组合
    收敛成 6 条后视（Codex P2 第三十一轮）。

    ⚠️ 配对反向断言：缺陷本体那族不带体标记，照旧被挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'停止{aspect}{modifier}播放的晴天'
    assert is_explicit_music_cancellation(text) is True, text
    for blocked in ('我要停止播放的代码', '帮我停止播放按钮换个颜色'):
        assert is_explicit_music_cancellation(blocked) is False, blocked


def test_symmetric_quote_runs_stay_deterministic():
    """⚠️⚠️ P1：**对称定界符**（开=闭，如 ASCII 双引号）不能进「内嵌跨度」那一支。

    进去之后，一串连续的 ASCII 双引号里外层既可以在每个引号处闭合、也可以把下一段当内嵌跨度
    吃掉，分段方式指数级：实测 30 个引号 3.9ms、40 个 **127ms**，60 个要跑几十秒，
    而入口上限是 160 字（Codex P1 第三十二轮）。

    对称定界符本来也谈不上嵌套（连续三个双引号没有唯一读法），所以内嵌跨度只由非对称
    对组成、对称对自己的跨度体不带内嵌支。

    ⚠️ 结构 + 耗时双断言；「不是完整跨度的开头」那个条件也要同步（第三十轮的教训）。
    """  # noqa: DOCSTRING_CJK
    import time

    from main_logic import music_requests as mr

    for opening, closing in mr._QUOTE_PAIRS.items():
        if opening in mr._ZH_AMBIGUOUS_QUOTE_OPENERS or closing != opening:
            continue
        assert f"{opening}(?:" not in mr._ZH_PAIRED_QUOTED_SPAN, (
            f"对称定界符 {opening} 又带上内嵌支了——指数分段会回来"
        )
        # ⚠️ 只看每条内嵌分支的**开头**：定界符也会作为被排除的字符出现在
        # `[^…]` 里，直接用 `in` 判断会误报（第一版就是这么写的）。
        assert not any(
            alt.startswith(opening)
            for alt in mr._ZH_QUOTED_SPAN_INNER.split("|")
        ), f"对称定界符 {opening} 还在开着内嵌跨度"

    worst = ('我想停止播放' + '"' * 80 + '是否')[:160]
    start = time.perf_counter()
    mr.is_explicit_music_cancellation(worst)
    assert time.perf_counter() - start < 1.0, "对称引号又开始指数分段了"
    # 语义没变：非对称跨度（含一层嵌套）照旧保护标题。
    assert mr.is_explicit_music_cancellation('帮我停止播放《“晴天”好不好》') is True
    assert mr.is_explicit_music_cancellation('我想停止播放《晴天」是否合适》') is False


@pytest.mark.parametrize(
    "marker", ["为啥", "為啥", "咋办", "咋辦", "何处", "何處", "几点", "幾點",
               "凭什么", "憑什麼"]
)
def test_colloquial_wh_compounds_are_questions(marker):
    """口语疑问复合式（Codex P2 第三十二轮）。触发方式跟前几次一样：引号跨度
    藏住标题内 `？` 之后，真标记不在表里就没有任何标记能触发守卫。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}换成《你好吗？》'
    ) is False
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


@pytest.mark.parametrize("measure", ["", "张", "首", "个", "部"])
@pytest.mark.parametrize("wh", ["什么", "哪", "几"])
def test_a_numeral_inside_a_music_wh_compound(wh, measure):
    """⚠️ 量词前面还能有「一」（`哪一张专辑` / `哪一首歌`）——这是最常见的写法。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{wh}一{measure}歌才会换成《你好吗？》'
    assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


@pytest.mark.parametrize(
    "coordinator",
    ["顺便", "順便", "顺手", "順手", "顺带", "順帶", "一起", "一块", "一道", "就"],
)
def test_more_coordinators_after_the_temporal_clause(coordinator):
    """并列副词表补齐（base 全是 True，Codex P2 第三十三轮）。

    ⚠️ 判据没变：`的时候` 后面**跟着并列副词**才认成命令；裸的时间从句
    （`停止播放的时候通知我`）仍然刻意判成提问（见第十一轮那条 by-design 用例）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'请停止播放的时候{coordinator}关闭屏幕'
    ) is True
    assert is_explicit_music_cancellation('停止播放的时候通知我') is False


@pytest.mark.parametrize("degree", ["高", "低", "轻", "响", "快", "慢"])
@pytest.mark.parametrize("suffix", ["一点", "一點", "一些", "点"])
def test_high_low_comparative_complements(degree, suffix):
    """程度补语补 高/低（base 是 True）。⚠️ 单字仍要求后跟量度成分，否则易撞名词。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'不要放的比刚才{degree}{suffix}') is True
    assert is_explicit_music_cancellation('停止播放的比赛结果') is False


@pytest.mark.parametrize(
    "modifier", ["单曲循环", "随机循环", "后台自动", "循环", "后台", ""]
)
def test_longer_playback_modifiers_inside_the_aspect_phrase(modifier):
    """⚠️ 修饰语最长按 `_ZH_PROGRESSIVE_MAX_MODIFIER` 生成——「单曲循环」「随机循环」
    「后台自动」都是 4 个字（Codex P2 第三十四轮）。

    ⚠️ 上限是**性能与覆盖的取舍**：后视条数随它线性涨（现在按宽度分组后 8 条），
    再往上收益递减。计时复跑：普通输入 0.008ms、三种对抗形状 0.012~0.13ms。

    ⚠️ 参数直接跟实现里的上限对账：实现把上限调小时这条会先在断言上见红，而不是
    留着一堆 4 字修饰语的用例悄悄变成「测了个更宽的实现」（CodeRabbit nitpick）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import (
        _ZH_PROGRESSIVE_MAX_MODIFIER,
        is_explicit_music_cancellation,
    )

    assert len(modifier) <= _ZH_PROGRESSIVE_MAX_MODIFIER, (
        f'用例的修饰语比实现上限还长: {modifier}'
    )
    text = f'停止正在{modifier}播放的晴天'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize("marker", ["哪位", "哪几位", "哪幾位"])
def test_the_performer_wh_marker(marker):
    """`哪位歌手唱的…` —— 人称疑问词（base 是 False，Codex P2 第三十四轮）。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}歌手唱的《你好吗？》'
    ) is False
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


_MUSIC_WH_CLASSIFIERS = [
    "种", "種", "类", "類", "款", "批", "组", "組", "套", "盘", "盤", "碟",
    "版", "支", "张", "張", "首", "个", "個", "部", "条", "條", "片", "段",
]


@pytest.mark.parametrize("classifier", _MUSIC_WH_CLASSIFIERS)
def test_the_wh_classifier_slot_is_structural(classifier):
    """⚠️ 量词槽是**结构化**的，不是一张枚举表（Codex P2 第三十五轮）。

    上一版写死 `[张張首个個部条條片段]` 并在注释里断言「量词是闭集」，结果
    种/種/类/類/款 一个没有——`我想停止播放哪种唱片…` 当场执行取消（base 是
    False）。批/组/套/盘/碟/版 同样不在。汉语量词是开集，但这里根本不需要认出
    是哪个量词：疑问性由**头**（什么/哪/几）和**尾**（音乐名词）两头钉死。

    ⚠️ 这条用例列的量词只是**样本**，不是白名单——正则里已经没有这张表了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (
        f'我想停止播放哪{classifier}唱片换成《你好吗？》',
        f'我想停止播放哪一{classifier}专辑',
    ):
        assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


def test_nezha_is_a_word_not_a_quantified_wh_phrase():
    """⚠️ `哪吒` 是词，不是「哪 + 量词」。

    量词槽放开成任意单字之后，`我想停止播放哪吒主题曲` 会被误判成提问而不停歌——
    《哪吒》主题曲是真实点播量很大的一类请求，所以单独挡掉。

    ⚠️ 承载这条保护的只有**第一句**：它得同时满足「疑问守卫的前缀（我想/我要/
    帮我）」和「哪吒 后面直接跟音乐名词、中间没有『的』」。带「的」的说法本来
    就够不着音乐名词、没前缀的说法根本进不了疑问守卫，拿它们当断言是空的——
    第一版就是这么写的，撤掉保护照样全绿。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation('我想停止播放哪吒主题曲') is True
    for text in ('我要停止播放哪吒的歌', '帮我停止播放哪吒里的插曲'):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放哪种唱片') is False


@pytest.mark.parametrize("marker", ["何人", "何者", "何故", "何事", "莫非"])
def test_literary_wh_pronouns(marker):
    """书面语的「何 + X」疑问代词（Codex P2 第三十七轮）。

    ⚠️ 这一族**故意不做成结构化**（`何` + 任意汉字），跟量词槽那边相反：
    《何日君再来》是真实点播，`何` 后面接任意字会把它吃进去。文言疑问
    代词本来就是那几个，枚举得干净。下面的反向断言就盯这一点。
    ⚠️ `任何人` / `任何事` 里 何人/何事 只是子串，base 是 True，不加左界当场掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{marker}唱的《你好吗？》'
    ) is False
    for text in (
        '我想停止播放任何人的歌',
        '我要停止播放任何事相关的歌',
        '我想停止播放何日君再来',
    ):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "aspect", ["刚", "剛", "刚刚", "剛剛", "刚开始", "剛開始", "刚才", "剛才"]
)
def test_just_started_playback_aspect(aspect):
    """起始体 `刚/剛`（base 全是 True，Codex P2 第三十七轮）。

    ⚠️ 实现里只加了**单字** 刚/剛：刚刚/刚开始/刚才 都由修饰语槽
    （0~4 个任意字）接住，不单列成品。这条用例列的是成品形式，就是为了钉住
    「一个单字 + 修饰语槽」确实盖得住它们。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (f'停止{aspect}播放的晴天',
                 f'帮我停止{aspect}播放的Taylor Swift'):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的代码') is False


@pytest.mark.parametrize("punct", ["，", ",", "；", ";", "、", ""])
@pytest.mark.parametrize(
    "coordinator", ["也", "再", "就", "顺便", "順便", "顺手", "順手",
                    "一起", "同时", "同時"]
)
def test_coordinator_after_a_punctuated_temporal_clause(coordinator, punct):
    """「的时候」和并列副词之间隔着标点也算并列（base 是 True）。

    ⚠️ 逗号是**子句分隔符**，并列副词落在下一个子句里，单子句正则
    永远看不见它——所以修在**切分之前**把这个位置的逗号抹掉，而不是把
    标点塞进那条正则（第一版就是塞进正则，完全无效）。
    ⚠️ 句末标点不算：`。` 之后是另一句话。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'停止播放的时候{punct}{coordinator}帮我关灯'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'停止播放的时候。{coordinator}帮我关灯'
    ) is False
    assert is_explicit_music_cancellation('停止播放的时候通知我') is False


@pytest.mark.parametrize("apostrophe", ["'", "’", "ʼ"])
def test_english_playback_negation_accepts_every_apostrophe(apostrophe):
    """英文否定分支的擇号——跟 card_assist_router 那边对偶的那一半。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (
        f'don{apostrophe}t play Taylor Swift',
        f'please don{apostrophe}t play music',
    ):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('play Taylor Swift') is False


@pytest.mark.parametrize("head", ["有", "是"])
@pytest.mark.parametrize("pronoun", ["什么", "什麼", "啥"])
def test_have_or_be_plus_interrogative_pronoun(head, pronoun):
    """「有/是 + 什么/啥」是复合疑问式（Codex P2 第三十九轮）。

    裸 `什么` 仍然不收——「没什么」「什么的」「什么都行」里它不是提问。
    这跟这张表「裸形歧义、复合形不歧义」的一贯判据是同一条。

    ⚠️ 左界必须挡住 没/沒/不：`没有什么好听的歌` 里 `有什么` 只是子串，
    base 是 True。跟 任何人 / 哪吒 同一族——这是本 PR 里第三个这样的入口。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{head}{pronoun}影响'
    ) is False
    for text in (
        '帮我停止播放没什么好听的歌',
        '帮我停止播放没有什么好听的歌',
        '帮我停止播放水果什么的',
        '帮我停止播放什么都行',
    ):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "aspect",
    ["在", "正在", "现在", "現在", "还在", "還在", "仍在",
     "依然在", "尚在", "一直在", "持续", "持續", "一直",
     "当前", "目前", "刚"],
)
def test_continuing_state_aspect_markers(aspect):
    """持续/进行体标记（base 全是 True，Codex P2 第三十九轮）。

    ⚠️ 实现里 **以「在」收尾的成品全删了**：「在」本身就是汉语进行体的
    核心构式（在 + V），列它一条就把 正在/现在/还在/仍在/依然在/尚在/
    一直在 全收了。第二十九轮补 还在/仍在、第三十九轮又来 依然在/尚在/
    一直在——同一个跑步机跑了两轮，到此为止。
    ⚠️ 这条用例列的是**成品形式**，就是为了钉住删掉那些成品后它们仍然走得通。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (f'停止{aspect}播放的晴天',
                 f'帮我停止{aspect}播放的Taylor Swift'):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我要停止播放的代码') is False
    assert is_explicit_music_cancellation('帮我停止播放按钮换个颜色') is False


@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
@pytest.mark.parametrize(
    "template",
    ["我想停止播放《你好吗？》{w}时候", "我想停止播放{w}时候合适",
     "我想停止播放{w}地方的歌", "我想停止播放凭{w}",
     "我想停止播放为{w}会卡", "我想停止播放有{w}影响",
     "我想停止播放{w}歌"],
)
def test_colloquial_what_is_an_alias_everywhere(template, what):
    """⚠️ `啥` 就是 `什么` 的口语形，凡是 `什么X` 成立的复合式，`啥X` 一样成立。

    上一版把它们逐个**成品**列在表里，于是 为啥 有、啥时候 没有、
    啥地方 没有、凭啥 没有（Codex P2 第四十轮，base 是 False）。现在三种写法
    收成 `_ZH_WHAT` 一条常量，所有复合式从它拼出来。

    ⚠️ 这条用例是 **7 种复合式 × 3 种写法**的笛卡尔积：只要哪一章又回到手写
    成品，缺的那个格子就会见红。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = template.format(w=what)
    assert is_explicit_music_cancellation(text) is False, text
    for keep in (
        '帮我停止播放没什么好听的歌',
        '帮我停止播放什么都行',
        '帮我停止播放红心歌单',
    ):
        assert is_explicit_music_cancellation(keep) is True, keep


@pytest.mark.parametrize("marker", ["莫非", "难道", "難道"])
def test_rhetorical_question_adverbs(marker):
    """反诘语气副词（Codex P2 第四十一轮，base 是 False）。

    它们后面不一定还有别的疑问标记，而歌名自带的问号又被配平跨度挡住，
    所以整句只剩它一个标记。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (
        f'我想停止播放{marker}会换成《你好吗？》',
        f'我想停止播放{marker}不行',
    ):
        assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


@pytest.mark.parametrize("word", ["哪吒", "哪怕"])
@pytest.mark.parametrize("noun", ["音乐", "歌曲", "歌", "专辑"])
def test_lexicalized_na_compounds_are_not_classifier_phrases(word, noun):
    """⚠️ `哪吒` / `哪怕` 是**词**，不是「哪 + 量词」。

    量词槽放开成任意单字之后，这两个词的第二个字会被当成量词，整句误判成
    提问、歌停不下来（base 都是 True；哪吒 第三十五轮、哪怕 第四十一轮）。

    ⚠️ `哪` 在现代汉语里**只有这两个**非疑问的词化组合，不是开集。
    反向断言钉住真的「哪 + 量词」仍然算提问。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (
        f'我想停止播放{word}{noun}很好听',
        f'帮我停止播放{word}{noun}再好听',
    ):
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放哪种{noun}') is False


@pytest.mark.parametrize(
    "marker", ["任何时候都行", "任何時候都行", "无论何时都行", "無論何時都行",
               "不论何时都行", "不論何時都行", "不管何时都行", "不管何時都行",
               "无论何人唱的都行", "不管何人唱的都行"]
)
def test_free_choice_phrases_are_not_questions(marker):
    """⚠️ 任指/无定构式里的疑问词**不是提问**（base 全是 True，Codex P2 第四十二轮）。

    `任何时候` 里 `何时` 只是子串。这跟 任何人 / 哪吒 / 哪怕 / 没有什么
    是同一族——**白名单词是更长词的子串**，本 PR 里的第四个入口。
    左界这一族是闭集（任/无论/不论/不管/随便），一次列全。
    ⚠️ 反向断言钉住真正的 `何时` / `何人` 仍然算提问。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{marker}'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放何时合适') is False
    assert is_explicit_music_cancellation(
        '我想停止播放何人唱的《你好吗？》才会换歌'
    ) is False


@pytest.mark.parametrize("negator", ["没", "沒", "没有", "沒有"])
@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
@pytest.mark.parametrize("noun", ["音乐", "歌", "歌曲"])
def test_declarative_negated_what_is_not_a_question(negator, what, noun):
    """⚠️ 陈述句里的 `没什么X` / `没有什么X` 是**否定**不是提问。

    `我想停止播放因为没什么音乐好听` base 是 True，音乐复合式把 `什么音乐`
    当成疑问头，一句明确的取消反而停不下来（Codex P2 第四十二轮）。

    ⚠️ `没有什么` 要单独列：中间隔着个 `有`，单字后视挡不住——参数化里
    四个否定词就是为了把这两种宽度都盖到。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{negator}{what}{noun}好听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{what}{noun}') is False


@pytest.mark.parametrize("negator", ["不", "没", "沒"])
@pytest.mark.parametrize("marker", ["怎么", "怎麼", "怎样", "怎樣"])
def test_negated_degree_phrases_are_not_questions(negator, marker):
    """⚠️ `不怎么X` 是**程度否定**（not very），不是提问（base 全是 True）。

    嵌在定语里的程度短语踩了疑问守卫，用户明确不想听的歌反而停不下来
    （Codex P2 第四十三轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放这首{negator}{marker}好听的歌'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{marker}会卡') is False


@pytest.mark.parametrize("prefix", ["无论", "無論", "不论", "不管", "任"])
@pytest.mark.parametrize(
    "what", ["什么时候", "什麼時候", "啥时候", "啥時候"]
)
def test_free_choice_applies_to_the_what_time_compound_too(prefix, what):
    """任指左界要同样盖住 `什么时候` 那一支（Codex P2 第四十三轮）。

    上一轮只把左界挂在 `何时` 上，`无论什么时候都行` 还是掉了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{prefix}{what}都行'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{what}合适') is False


@pytest.mark.parametrize("verb", ["播放", "放", "听", "聽", "播"])
@pytest.mark.parametrize(
    "complement", ["断断续续", "斷斷續續", "结结巴巴", "結結巴巴",
                   "忽快忽慢", "一顿一顿", "一頓一頓", "忽高忽低"],
)
def test_reduplicated_state_complements_after_a_mistyped_de(verb, complement):
    """状态/结果补语里的「的」是补语标记「得」的误打（base 全是 True）。

    ⚠️ **不列成品词表**：这一族的构词是闭的——四字重叠式，AABB 或 ABAC。
    按重叠**结构**判，一次收干净（Codex P2 第四十四轮）。

    ⚠️ 反向断言钉住缺陷三的本体：`的代码` / `的教程` / `的听歌功能`
    都不是四字重叠式，照旧被挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'不要{verb}的{complement}'
    assert is_explicit_music_cancellation(text) is True, text
    # ⚠️ 重叠式要求落到**子句末尾**：后面还跟着名词时它是定语不是补语，
    # `停止播放的断断续续的问题` 问的是问题、不是要停歌。
    for kept in ('我要停止播放的代码', '我想停止播放的教程',
                 '我要停止播放的听歌功能',
                 f'我想停止播放的{complement}的问题'):
        assert is_explicit_music_cancellation(kept) is False, kept


@pytest.mark.parametrize("prefix", ["无论", "無論", "不论", "不管", "任", "随便"])
@pytest.mark.parametrize("wh", ["什么歌", "啥歌", "谁唱的歌", "哪个唱的歌"])
def test_free_choice_covers_the_music_and_pronoun_branches(prefix, wh):
    """任指左界要盖住**每一支** wh 分支（Codex P2 第四十四轮）。

    上一轮只盖了时间和 `何…` 两支，音乐复合式和代词组还漏着。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{prefix}{wh}都不好听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}') is False


@pytest.mark.parametrize("negator", ["不", "没", "沒", "没有", "沒有"])
def test_two_character_negators_before_a_degree_marker(negator):
    """⚠️ `没有怎么` 的否定词是**两个字**，单字后视挡不住。

    跟 `没有什么` 是同一个形状，上一轮只在 `什么` 那边单列了两字后视，
    `怎么` 这边漏了（Codex P2 第四十四轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for marker in ("怎么", "怎麼", "怎样", "怎樣"):
        text = f'我想停止播放这首{negator}{marker}听过的歌'
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放怎么会卡') is False


@pytest.mark.parametrize("stem", ["干", "幹"])
@pytest.mark.parametrize("what", ["嘛", "什么", "什麼", "啥"])
def test_what_are_you_doing_markers(stem, what):
    """`干嘛` 和 `干什么/干啥` 是同一个词的不同写法（Codex P2 第四十五轮）。

    上一版只列了 `干嘛`，`我想停止播放《你好吗？》干什么` 里歌名自带的问号
    又被配平跨度挡住，整句就没有疑问标记了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in (f'我想停止播放《你好吗？》{stem}{what}',
                 f'我想停止播放{stem}{what}'):
        assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


@pytest.mark.parametrize(
    "governor", ["不是", "并非", "並非", "无论是", "無論是",
                 "不论是", "不論是", "不管是"]
)
@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
def test_negated_or_free_choice_copulas_are_declarative(governor, what):
    """⚠️ 否定系词 / 任指系词 下的 `什么X` 是陈述（base 全是 True）。

    单字后视在 `什么` 的位置看到的是 `是`、在 `是` 的位置看到的是 `论`，
    两层都挡不住前面那个否定，所以**两条分支都要挂左界**
    （音乐复合式那一支和 `有/是 + 什么` 那一支，Codex P2 第四十五轮）。
    ⚠️ 第一版只改了前一支，`无论是什么歌` 还是掉着。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for noun in ('歌', '音乐'):
        text = f'我想停止播放因为{governor}{what}{noun}都不好听'
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放有{what}影响') is False


@pytest.mark.parametrize("frame", NON_INTERROGATIVE_FRAMES)
@pytest.mark.parametrize(
    "predicate", ["", "唱", "播放", "听", "问", "换成"]
)
@pytest.mark.parametrize("wh", ["什么歌", "啥歌", "谁", "哪首", "哪个"])
def test_free_choice_frame_with_an_intervening_predicate(frame, predicate, wh):
    """⚠️ 任指框架词和疑问词之间可以隔一个谓语，距离**不定长**。

    定长后视只能挡紧贴的那一种，所以这一族改成在**切分之前**把框架里的
    疑问词换成中性的「某」（base 全是 True，Codex P2 第四十六轮）。

    ⚠️ 相邻那一族仍然靠定长后视：`任何时候` 里的 `任何` 是个**词**、
    不是任指框架词，进不了这条替换。两条机制分工不同，下面两条反向断言
    分别钉住它们。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{frame}{predicate}{wh}都一样'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放任何时候都行') is True
    assert is_explicit_music_cancellation(f'我想停止播放{wh}') is False


@pytest.mark.parametrize("frame", NON_INTERROGATIVE_FRAMES)
@pytest.mark.parametrize(
    "body",
    ["谁唱什么歌", "哪个歌手唱什么", "什么人点啥歌",
     "哪首歌是谁唱的", "何时听什么"],
)
def test_free_choice_frame_neutralizes_every_wh_in_scope(frame, body):
    """⚠️ 框架的辖域里的疑问词可能**不止一个**（base 全是 True）。

    上一版写成「框架词 + 窗口 + 一个疑问词」的单条正则，只换得掉第一个
    （Codex P2 第四十七轮）。辖域本来就是**段落式**的，不该用一条固定形状的
    正则去套，所以拆成「找框架词」+「换辖域内所有疑问词」两步。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{frame}{body}都一样'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放什么歌') is False
    assert is_explicit_music_cancellation('我想停止播放何时合适') is False
    # ⚠️ 框架的辖域到**句读为止**：下一个子句里的疑问词不归它管。
    # 不钉这一条的话，「辖域吃整段」那个变异会照样绿。
    for later_question in (
        '不管怎么样，我想停止播放什么歌',
        '随便吧，我想停止播放谁唱的《你好吗？》',
        '无论如何，我想停止播放何时合适',
    ):
        assert is_explicit_music_cancellation(later_question) is False, later_question


@pytest.mark.parametrize("conditional", NON_INTERROGATIVE_FRAMES)
@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
def test_conditional_frames_make_wh_existential(conditional, what):
    """⚠️ **条件框架**辖域里的疑问词是存在量词，不是提问（base 全是 True）。

    `如果有什么新歌再告诉我` 不是在问哪首歌（Codex P2 第四十八轮）。
    它跟任指框架是同一件事，所以共用同一张表、同一个辖域规则（到句读为止）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{conditional}有{what}新歌再告诉我'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放有{what}影响') is False


@pytest.mark.parametrize("negator", ["不", "没", "沒", "没有", "沒有"])
@pytest.mark.parametrize("stem", ["干", "幹"])
@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
def test_negated_what_are_you_doing_is_an_indefinite(negator, stem, what):
    """⚠️ `没干什么` 里的 `干什么` 是**不定指**，不是提问（base 是 True）。

    上一轮刚把 `干什么` 收成疑问标记，就漏了它的否定形
    （Codex P2 第四十八轮）——跟 `不怎么` / `没什么` 同一个形状，
    复用同一个程度否定左界。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为我{negator}{stem}{what}它却自己响了'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'我想停止播放《你好吗？》{stem}{what}'
    ) is False


@pytest.mark.parametrize("frame", NON_INTERROGATIVE_FRAMES)
@pytest.mark.parametrize("wh", ["有什么新歌", "什么歌", "谁唱的", "哪首歌"])
def test_concessive_and_cognition_frames_neutralize_wh(frame, wh):
    """让步框架（即使/就算/哪怕）和认知谓语（不知道/忘了）辖域里的
    疑问词不是在问我们（base 全是 True，Codex P2 第四十九轮）。

    ⚠️ 它们跟任指/条件框架是同一件事，所以进的是同一张表、同一个
    辖域规则（到句读为止）——不另开机制。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{frame}{wh}也不想听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放什么歌') is False


@pytest.mark.parametrize("verb", ["播放", "放", "听", "聽", "播"])
@pytest.mark.parametrize(
    "complement", ["听不清", "聽不清", "看不见", "看不見", "跟不上",
                   "听不清楚", "聽不清楚", "受不了"]
)
def test_potential_complements_after_a_mistyped_de(verb, complement):
    """可能补语 `V不C`（base 全是 True，Codex P2 第四十九轮）。

    中间那个 `不` 是很强的结构信号，不会跟名词混。

    ⚠️ 反向断言钉住缺陷三的本体：`的代码` / `的教程` / `的听歌功能` /
    `的播放器` 都没有那个 `不`，照旧被挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'不要{verb}的{complement}'
    assert is_explicit_music_cancellation(text) is True, text
    for kept in ('我要停止播放的代码', '我想停止播放的教程',
                 '我要停止播放的听歌功能', '我想停止播放的播放器'):
        assert is_explicit_music_cancellation(kept) is False, kept


@pytest.mark.parametrize("verb", ["播放", "放", "听", "聽"])
@pytest.mark.parametrize(
    "defect", ["卡顿", "卡頓", "延迟", "延遲", "断流", "斷流", "失真", "破音",
               "跳针", "跳針", "卡帧", "卡幀", "回声", "回聲", "雜訊", "杂讯"]
)
def test_playback_defect_words_after_a_mistyped_de(verb, defect):
    """⚠️ 播放缺陷词是**白名单**，不是结构规则（base 全是 True）。

    这正是它能收、而「两个汉字的状态词」那条**结构规则**不能收的原因：
    后者会把 `的代码` / `的教程` / `的播放器` 一起放回去，而那正是本 PR
    缺陷三的本体（第四十九轮拒收 `震耳朵` 就是这个理由）。白名单撞不上它们。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'不要{verb}的{defect}'
    assert is_explicit_music_cancellation(text) is True, text
    for kept in ('我要停止播放的代码', '我想停止播放的教程',
                 '我想停止播放的播放器', '我要停止播放的听歌功能'):
        assert is_explicit_music_cancellation(kept) is False, kept


@pytest.mark.parametrize("negator", ["没", "沒", "没有", "沒有"])
def test_negated_duration_is_declarative(negator):
    """`没多久` 是陈述（not long after），不是提问（base 全是 True）。

    跟 `不怎么` / `没什么` / `没干什么` 同一个形状，复用同一个程度否定左界
    （Codex P2 第五十轮）。四个否定词包含两字的，两种宽度都盖到。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{negator}多久它又自己响了'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放多久合适') is False


@pytest.mark.parametrize("separator", ["，", ",", "。", "！", "!", "？", "?", "；", ";"])
def test_frame_scope_stops_at_every_clause_separator(separator):
    """⚠⚠ 框架辖域的边界表必须跟 `_CLAUSE_SEPARATOR_CHARS` **同源**。

    上一版手写时漏了分号，于是框架词能跨过分号去中和下一个子句里的
    疑问词，一句提问执行了取消（Codex P2 第五十二轮，base 是 False——危险方向）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import (
        _CLAUSE_SEPARATOR_CHARS,
        _ZH_CLAUSE_BOUNDARY_RE,
        is_explicit_music_cancellation,
    )

    assert _ZH_CLAUSE_BOUNDARY_RE.match(separator), separator
    assert set(_CLAUSE_SEPARATOR_CHARS) == set("，,。；;！？!?")
    text = f'如果有问题再说{separator}我想停止播放为什么会换成《你好吗？》'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "title", ["《如果爱》", "「如果爱」", "“如果爱”", "《就算不想》", "《不管你是谁》"]
)
def test_frame_words_inside_quoted_titles_do_not_govern(title):
    """⚠⚠ 落在**配平引用跨度里**的框架词不算（base 是 False——危险方向）。

    《如果爱》里的 `如果` 是歌名的一部分，不应该去中和歌名外那个真疑问词
    （Codex P2 第五十二轮）。引号表跟子句切分用的是同一张。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{title}为什么会换成《你好吗？》'
    assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation(
        f'我想停止播放{title}'
    ) is True


@pytest.mark.parametrize("wh", ["如何", "怎么样", "怎麼樣", "怎么", "多久", "多少", "何处", "哪里"])
def test_every_wh_form_is_neutralized_inside_a_frame(wh):
    """⚠️ 要换掉的是**所有能当疑问标记的词**，不是其中几个。

    漏一个，框架里那句话就仍然被当成提问（base 全是 True，
    Codex P2 第五十二轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为无论{wh}都不想听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}合适') is False


@pytest.mark.parametrize("correlative", ["都", "就"])
@pytest.mark.parametrize("head", ["有", "是"])
@pytest.mark.parametrize("what", ["什么", "什麼", "啥"])
def test_correlative_what_clauses_are_declarative(head, what, correlative):
    """关联构式 `有什么X…都/就…` 是陈述（base 全是 True）。

    ⚠️ 跟框架机制不同：这里的标记（都/就）在疑问词**后面**，所以用前视。
    ⚠️ 反向断言：没有那个 都/就 时它仍然是提问。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放是因为播放器{head}{what}歌{correlative}自动放'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'我想停止播放{head}{what}影响'
    ) is False


@pytest.mark.parametrize("negator", ["不", "没", "沒", "没有", "沒有"])
@pytest.mark.parametrize("wh", ["干嘛", "幹嘛", "干什么", "幹什麼", "干啥"])
def test_negated_what_are_you_doing_branches(negator, wh):
    """⚠️ `干嘛` 和 `干什么` 是同一个词的两种写法，否定左界也得一起挂。

    上一轮只给 `干{什么}` 挂了，旁边的 `干嘛|幹嘛` 漏了
    （base 是 True，Codex P2 第五十三轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为我{negator}{wh}它却自己响了'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'我想停止播放《你好吗？》{wh}'
    ) is False


@pytest.mark.parametrize(
    "negator", ["不是", "并非", "並非", "没有", "沒有", "没", "沒"]
)
@pytest.mark.parametrize(
    # ⚠️ 后两个（哪张专辑 / 几张专辑）走的是**音乐复合式**那一支（量词槽），
    # 跟 `哪首` 这种成品分支不是同一条——不列它们的话，「只给 `什么` 头
    # 挂左界」那个变异会照样绿（变异 SURVIVED 才发现）。
    "wh", ["谁", "誰", "哪个", "哪個", "哪些", "哪首歌", "几首歌", "幾首歌",
           "哪里", "哪裡", "哪首", "哪张专辑", "哪張專輯", "几张专辑", "幾張專輯"]
)
def test_negated_wh_pronoun_branches_are_declarative(negator, wh):
    """⚠️ **每一支** wh 都要挂陈述左界，不只是 `什么` 那几支。

    `不是谁都喜欢` / `没有哪首歌好听` / `没有几首歌好听` 都是陈述的
    取消理由，base 全是 True（Codex P2 第五十三轮）。同一个形状已经
    出现过四轮，这一轮把剩下的分支一次挂完。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{negator}{wh}都好听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}好听') is False


@pytest.mark.parametrize(
    "polarity", ["是不是", "能不能", "可不可以", "行不行", "是否", "能否", "有无", "有無"]
)
def test_frames_neutralize_polarity_markers_too(polarity):
    """⚠️ 框架里要中和的不只是疑问代词，还有**极性标记**。

    A-not-A 那一族直接复用生成器的结果，不另拄一张表。
    ⚠️ 反向断言：没有框架时它们仍然是疑问标记。

    ⚠️⚠️ **这条 docstring 原本写着「base 全是 True」，那是错的**（第六十五轮实测）。
    24 个参数里 A-not-A（是不是/能不能/可不可以/行不行）和 有无/有無 确实 base=True，
    但 `是否`/`能否` 这 6 个 **base=False**——本 PR 在这里是放宽了 base。

    没有跟着改回去，因为按这个 PR 的收敛边界它不属于必修的那一类：这些句子里
    用户明确说了「我想停止播放…因为…」，动作是**请求过的**，放宽不会执行用户
    没要求的事。记在这里是为了别让一个错的 base 声称继续误导后面的判断。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for frame in ("无论", "不管", "不知道"):
        text = f'我想停止播放因为{frame}{polarity}好歌都不想听'
        assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'我想停止播放{polarity}'
    ) is False


@pytest.mark.parametrize(
    ("opener", "closer"), [("《", "》"), ("「", "」"), ("“", "”"), ('"', '"'), ("【", "】")]
)
def test_only_the_title_position_quote_shields_a_marker(opener, closer):
    """⚠⚠ 引用跨度只在**紧跟播放动词**那一个位置算标题。

    上一版把句子里每一对配平引号都当标题整段跳过，于是用户把真正的问题
    放在引号里时整段被跳掉，一句提问执行了取消（Codex P2 第五十三轮，
    base 是 False——危险方向）。

    ⚠️ 下面第一条反向断言钉的是第七轮那个取舍：标题位置的引号仍然整段遮住，
    `停止播放《你好吗？》` 照旧是命令。两边必须同时成立。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{opener}你好吗？{closer}'
    ) is True
    for questioned in (
        f'我想停止播放晴天{opener}是否合适{closer}',
        f'我想停止播放{opener}你好吗？{closer}是否合适',
        f'我想停止播放是否适合{opener}你好吗？{closer}',
    ):
        assert is_explicit_music_cancellation(questioned) is False, questioned


@pytest.mark.parametrize("negator", ["没有", "沒有", "没", "沒"])
@pytest.mark.parametrize(
    "phrase", ["哪张专辑", "哪張專輯", "几张专辑", "幾張專輯", "哪种唱片", "哪種唱片",
               "几首歌", "幾首歌", "哪个歌单", "哪個歌單", "几张唱片", "幾張唱片"]
)
def test_negated_music_compound_heads_are_declarative(negator, phrase):
    """⚠️ 音乐复合式里的 `哪` / `几` 两个头也要挂陈述左界，不只是 `什么`。

    `因为没有哪张专辑好听` 是陈述的取消理由（base 是 True，
    Codex P2 第五十三轮）。

    ⚠️ 这条的句式**不能带 `都`**：带了的话关联构式前视已经把它中和掉了，
    这条左界根本没受力——第一版用了 `都好听`，变异当场 SURVIVED。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{negator}{phrase}好听'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(
        f'我想停止播放{phrase}好听'
    ) is False


@pytest.mark.parametrize("punctuation", ["，", ",", "；", ";"])
def test_frame_scope_skips_punctuation_inside_a_quoted_title(punctuation):
    """⚠️ 找辖域边界时要跳过引用跨度：标题里的逗号/分号不是句读。

    子句切分器本来就把跨度当不透明的，这里不跟上就会在标题内部提前断掉，
    后面的 `有什么` 又成了疑问标记（base 是 True，Codex P2 第五十四轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = (f'我想停止播放因为如果听《晴天{punctuation}雨天》'
            '有什么问题再告诉我')
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放有什么影响') is False


@pytest.mark.parametrize(
    ("first", "second"), [("听", "播放"), ("播放", "听"), ("放", "播放"), ("播放", "播放")]
)
def test_every_playback_target_title_is_shielded(first, second):
    """一句话里可以有多个播放动词，**每个**都可能带自己的标题（base 是 True）。

    ⚠️ 循环里标题跨度是**必需**的，只有最后一个动词可以不带标题：写成「标题可选 +
    循环」时，`我想停止播放哪个好听` 里的 `听`（`好听` 的后半）会被当成另一个播放
    动词，循环一路吃到那里，把疑问词 `哪个` 一并吞进前缀——一句提问又成了命令。
    下面第二条反向断言钉的就是这个。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止{first}《晴天》然后停止{second}《好不好》'
    assert is_explicit_music_cancellation(text) is True, text
    for questioned in ('我想停止播放哪个好听', '我想停止播放哪首好听',
                       '我想停止播放晴天“是否合适”'):
        assert is_explicit_music_cancellation(questioned) is False, questioned


@pytest.mark.parametrize(
    "predicate",
    ["合适", "合適", "方便", "容易", "可能", "清楚", "明显", "明顯",
     "靠谱", "靠譜", "划算", "劃算", "合理", "恰当", "恰當"],
)
def test_evaluative_a_not_a_tails(predicate):
    """评价类谓词的重叠式也是 A-not-A 疑问尾（base 都是 False）。

    ⚠️ 生成器会自动产出简叠式（`合不合适`）和全叠式（`合适不合适`），
    所以相等断言挂在**谓词表**上，不列成品（Codex P2 第五十四轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    short = f'{predicate[0]}不{predicate}'
    for tail in (short, f'{predicate}不{predicate}'):
        text = f'我想停止播放《你好吗？》{tail}'
        assert is_explicit_music_cancellation(text) is False, text
    assert is_explicit_music_cancellation('我想停止播放《你好吗？》') is True


@pytest.mark.parametrize("correlative", ["都", "就"])
# ⚠️ `哪首歌` 原来写了两遍（CodeRabbit）——pytest 会自动加 0/1 后缀，用例照跑但
# 同一个词跑两遍不增加覆盖。补成 `哪些歌`。
@pytest.mark.parametrize("wh", ["谁", "誰", "哪个", "哪個", "哪些", "哪首歌", "哪些歌", "哪里", "哪裡", "多少"])
def test_general_pronouns_in_correlative_clauses(wh, correlative):
    """一般疑问代词在关联构式里也是陈述（base 是 True，Codex P2 第五十五轮）。

    ⚠️ 上一轮只给 `有什么…都/就` 挂了关联前视，代词支和 `哪首/哪里` 支漏了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{wh}听了{correlative}难受'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}唱的《你好吗？》') is False


@pytest.mark.parametrize("verb", ["播放", "放", "听", "聽", "播"])
@pytest.mark.parametrize("gap", ["", " "])
def test_whitespace_between_the_verb_and_its_quoted_title(verb, gap):
    """⚠️ 动词和标题之间可以有一个空格：入口的 normalize 把连续空白压成**一个**
    ASCII 空格而不是删掉它（base 是 True，Codex P2 第五十五轮）。

    这是「空白在这个文件里咬人」的第 N 次，前面几次分别在「的」前后和体标记后。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止{verb}{gap}《好不好》'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放晴天“是否合适”') is False


@pytest.mark.parametrize(
    "wh", ["干嘛", "幹嘛", "咋办", "咋辦", "几点", "幾點", "多会儿", "咋", "如何",
           "怎么样", "怎樣", "多久", "多少", "何人", "何處", "哪里", "哪裡",
           "什么歌", "什麼歌"]
)
def test_the_neutralizer_covers_every_marker_form(wh):
    """⚠️⚠️ 中和用的词表**直接复用疑问标记正则本身**，不再另维护一份。

    「中和表比标记表少几个词」已经以三种面孔出现过（第四十六/五十二/五十五轮：
    先是 如何/怎么样/多久，再是 何人/哪里，这次是 干嘛/咋办/几点/多会儿）——
    只要还是两张手写的表，就一定会再漂开一次。同源之后这一族到此为止。

    ⚠️ 这条用例是**两侧同时断言**：同一个词，单用时必须触发疑问守卫（说明它确实
    是标记），进框架后必须被中和（说明中和表没落下它）。任一侧漏了都见红。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(f'我想停止播放{wh}') is False, wh
    framed = f'我想停止播放因为无论{wh}都不好听'
    assert is_explicit_music_cancellation(framed) is True, framed


@pytest.mark.parametrize("correlative", ["都", "就", "也"])
@pytest.mark.parametrize(
    "wh", ["谁", "誰", "哪个", "哪個", "哪首歌", "哪里", "哪裡", "什么歌", "什麼歌"]
)
def test_all_three_correlative_markers(wh, correlative):
    """关联标记不止 都/就，还有 也（base 都是 True，Codex P2 第五十七轮）。这一族是闭集。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{wh}听了{correlative}难受'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}好听') is False


@pytest.mark.parametrize("verb", ["播放", "放", "听", "聽"])
def test_frame_words_in_unquoted_titles_do_not_govern(verb):
    """⚠️ 紧跟播放动词的框架词是**标题的一部分**，不是框架。

    `停止播放如果爱是否会影响歌单` 里的 `如果` 属于歌名《如果爱》——带引号的标题
    上一轮已经处理，这条补的是**不带引号**的那一半（Codex P2 第五十七轮，
    base 是 False——危险方向）。

    ⚠️ 但不能一刀切：框架词后面紧跟**谓词**（有/是/没/要/能/会…）时它引的是真小句，
    第四十八轮修的 `停止播放如果有什么新歌再告诉我` 必须保住。下面两条断言分列两侧。
    ⚠️ 已知代价：《如果有一天》这类以谓词开头的歌名会被当成条件框架——轻的那一侧。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    titled = f'我想停止{verb}如果爱是否会影响歌单'
    assert is_explicit_music_cancellation(titled) is False, titled
    framed = f'我想停止{verb}如果有什么新歌再告诉我'
    assert is_explicit_music_cancellation(framed) is True, framed


@pytest.mark.parametrize("predicate", ["知道", "晓得", "曉得", "记得", "記得", "清楚", "确定", "確定"])
@pytest.mark.parametrize(
    "wh", ["怎么办", "怎麼辦", "谁唱的", "誰唱的", "哪首歌", "什么歌", "什麼歌"]
)
def test_positive_cognition_predicates_also_govern(predicate, wh):
    """**肯定**的认知谓语一样管着宾语从句（base 都是 True，Codex P2 第五十八轮）。

    上一轮只收了否定形（不知道/不记得），肯定形漏了——「新收一个词就要同时想它的
    否定形」这条规律，反过来也成立。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为我{predicate}{wh}'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation(f'我想停止播放{wh}') is False


@pytest.mark.parametrize("correlative", ["都", "就", "也"])
@pytest.mark.parametrize(
    "polarity",
    ["好不好听", "好不好聽", "能不能联网", "能不能聯網",
     "是不是会员", "是不是會員", "要不要收费", "要不要收費"],
)
def test_a_not_a_in_correlative_clauses(polarity, correlative):
    """A-not-A 在关联构式里是「无论是否…」的意思，不是提问（base 都是 True）。

    ⚠️ 反向断言：没有那个 都/就/也 时它仍然是疑问尾——`我想停止播放可不可以`
    是这个 PR 的缺陷二本体，不能被顺手放开。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为{polarity}它{correlative}自动响'
    assert is_explicit_music_cancellation(text) is True, text
    assert is_explicit_music_cancellation('我想停止播放可不可以') is False


@pytest.mark.parametrize(
    "modifier",
    ["歌曲", "歌", "音乐", "音樂", "专辑", "專輯", "单曲", "單曲",
     "唱片", "铃声", "鈴聲", "曲子", "歌单", "歌單",
     "这首", "這首", "那张", "那張", ""],
)
@pytest.mark.parametrize("title", ["《好不好》", "《是不是》", "「能不能」"])
def test_target_modifiers_before_a_quoted_title(modifier, title):
    """标题前面可以有**目标修饰语**：`播放歌曲《好不好》` / `播放这首《好不好》`
    （base 都是 True，Codex P2 第五十八轮）。

    ⚠️ 修饰语只认**闭集**（音乐名词 + 指示词），**没有**放开成「任意…的」。
    放开的话 `播放谁唱的《你好吗？》` / `播放哪位歌手唱的…` / `播放莫非唱的…`
    里的疑问词会被当成修饰语吞掉、标题又被遮住，一句提问执行取消——危险方向。
    代价是 `播放周杰伦的《好不好》` 这类领属修饰仍然少触发一次，那是轻的一侧。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{modifier}{title}'
    assert is_explicit_music_cancellation(text) is True, text
    for questioned in ('我想停止播放谁唱的《你好吗？》',
                       '我想停止播放哪位歌手唱的《你好吗？》',
                       '我想停止播放晴天“是否合适”'):
        assert is_explicit_music_cancellation(questioned) is False, questioned


@pytest.mark.parametrize(
    "modifier", ["", "周杰伦的", "那位歌手的", "我最喜欢的", "歌曲", "这首"]
)
def test_frame_words_after_unquoted_target_modifiers(modifier):
    """⚠️ 框架词落在「播放动词 + 一段无引号修饰语」之后时，仍然属于**目标位**
    （base 是 False，Codex P2 第五十九轮——危险方向）。

    上一轮只判了「紧贴播放动词」，`播放周杰伦的如果爱是否合适` 就漏了。

    ⚠️ 修饰语在这里可以放宽到「任意…的」，跟标题遮蔽那边**方向相反**：
    这里放宽 = 少中和一个框架 = 疑问守卫更容易开火 = 少停一次歌（轻）；
    那边放宽 = 疑问词被吞掉 = 提问执行取消（重）。同一个语料现象，两处取舍不同。

    ⚠️ 第二条断言钉住第四十八轮的真条件框架没被打回去。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    questioned = f'我想停止播放{modifier}如果爱是否合适'
    assert is_explicit_music_cancellation(questioned) is False, questioned
    framed = '我想停止播放如果有什么新歌再告诉我'
    assert is_explicit_music_cancellation(framed) is True


def test_the_frame_scope_coordinator_table_is_pinned():
    """⚠️ 下面那条笛卡尔积从这张表派生，先把表钉住（相等，不是包含）。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests

    assert music_requests._ZH_FRAME_SCOPE_COORDINATORS == (
        "然后", "然後", "然而", "接着", "接著", "接下来", "接下來",
        "随后", "隨後", "继而", "繼而", "而后", "而後",
        "紧接着", "緊接著", "跟着", "跟著", "于是", "於是",
        "同时", "同時",
        "反而", "反倒", "况且", "況且", "何况", "何況", "再者", "此外",
        "只是", "不过是", "不過是", "无非", "無非",
        "可",
        "因而", "从而", "從而",
        "但是", "但", "不过", "不過", "可是", "却", "卻", "而且", "并且", "並且",
        "另外", "再说", "再說", "所以", "因此",
    )


def _frame_scope_coordinators() -> list[str]:
    from main_logic.music_requests import _ZH_FRAME_SCOPE_COORDINATORS

    table = list(_ZH_FRAME_SCOPE_COORDINATORS)
    assert table, "_ZH_FRAME_SCOPE_COORDINATORS 是空的"
    return table


@pytest.mark.parametrize("coordinator", _frame_scope_coordinators())
def test_a_frame_stops_at_a_coordinator(coordinator):
    """任指/条件框架的辖域**止于并列连词**，不只是止于标点
    （base 是 False——危险方向，Codex P2 第六十一轮）。

    `如果会影响歌单就算了然后这样是否合适` 里那个 `如果` 管不到 `然后` 后面的
    真问题；上一版一路扫到子句尾，把 `是否` 中和掉，一句提问执行了取消。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放如果会影响歌单就算了{coordinator}这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


def test_coordinators_do_not_break_real_frames():
    """⚠️ 反向：连词只截**辖域**，不连词的真框架照旧成立。

    第四十八轮的条件框架、第五十八轮的认知谓语框架都钉在这里。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in ('我想停止播放如果有什么新歌再告诉我',
                 '我想停止播放因为无论唱什么歌都不好听',
                 '我想停止播放因为我知道谁唱的',
                 '停止播放', '帮我停止播放红心歌单'):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize("opener", ["《", "「", "“", "『", "〈"])
@pytest.mark.parametrize("frame", ["如果", "无论", "無論", "不管", "即使"])
def test_an_unmatched_quote_opener_runs_to_the_end_of_the_text(opener, frame):
    """⚠️ **没配平的开引号一直管到文末**（base 是 False——危险方向，
    Codex P2 第六十一轮）。

    `停止播放《如果爱是否合适` 里用户漏了书名号的右半边。上一版直接把这段丢掉，
    于是 `如果` 被当成条件框架、把后面那个真疑问词 `是否` 中和掉，提问执行了取消。

    ⚠️ 这同时是跟 `_ZH_UNPAIRED_QUOTE_OPENER` **对齐**：疑问守卫那边早就把没配平
    的开引号当标题起点了，跨度这边却当它不存在——同一个字符两套读法。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{opener}{frame}爱是否合适'
    assert is_explicit_music_cancellation(text) is False, text


def test_unmatched_openers_report_a_span_to_the_end():
    """⚠️ 直接钉住助手本身，别只从外层行为反推。

    外层还有疑问守卫等好几道，只测外层的话，跨度算错但恰好被别的守卫兜住时
    这条就成了假绿。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests

    text = '我想停止播放《如果爱是否合适'
    assert music_requests._zh_quoted_spans(text) == [(text.index('《'), len(text))]
    paired = '我想停止播放《好不好》有什么影响'
    assert music_requests._zh_quoted_spans(paired) == [
        (paired.index('《'), paired.index('》') + 1)
    ]


def test_a_bare_a_not_a_after_an_unmatched_opener_is_read_as_a_question():
    """⚠️ 这是上面那条的**代价**，写成用例钉住，别当缺陷再修一次。

    `停止播放《好不好` base 是 True（把 `好不好` 当歌名），现在是 False
    （当提问）。它跟 `停止播放《如果爱是否合适` 在结构上**无法区分**——都是
    「没配平的开引号 + 里面有疑问式」。两条只能取一条：

    - 让引号内的疑问式失效 → `《如果爱是否合适` 这类提问执行取消（重）
    - 让引号内的疑问式照旧生效 → `《好不好` 少停一次歌（轻）

    取轻的那一侧。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation('我想停止播放《好不好') is False
    assert is_explicit_music_cancellation('我想停止播放《好不好》') is True


def test_the_cognition_and_inquiry_tables_are_pinned():
    """⚠️ 下面的笛卡尔积从这两张表派生，先钉住（相等，不是包含）。

    ⚠️ 还要钉住 `_ZH_ASSERTED_FRAMES` 确实是**划分**：肯定认知谓语全部移出去了，
    其余一个不少。写成集合运算而不是抄一遍成品，抄的那种改表就漏。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert mr._ZH_POSITIVE_COGNITION_FRAMES == (
        "知道", "曉得", "晓得", "记得", "記得", "清楚", "确定", "確定",
    )
    assert mr._ZH_INQUIRY_VERBS == (
        "想", "要", "需要", "希望", "打算", "准备", "準備",
    )
    assert set(mr._ZH_ASSERTED_FRAMES) | set(mr._ZH_POSITIVE_COGNITION_FRAMES) == set(
        mr._ZH_NON_INTERROGATIVE_FRAMES
    )
    assert set(mr._ZH_ASSERTED_FRAMES) & set(mr._ZH_POSITIVE_COGNITION_FRAMES) == set()


def _positive_cognition_frames() -> list[str]:
    from main_logic.music_requests import _ZH_POSITIVE_COGNITION_FRAMES

    table = list(_ZH_POSITIVE_COGNITION_FRAMES)
    assert table, "_ZH_POSITIVE_COGNITION_FRAMES 是空的"
    return table


def _inquiry_verbs() -> list[str]:
    from main_logic.music_requests import _ZH_INQUIRY_VERBS

    table = list(_ZH_INQUIRY_VERBS)
    assert table, "_ZH_INQUIRY_VERBS 是空的"
    return table


@pytest.mark.parametrize("cognition", _positive_cognition_frames())
@pytest.mark.parametrize("inquiry", _inquiry_verbs())
def test_a_cognition_predicate_under_an_inquiry_verb_is_not_an_assertion(
    inquiry, cognition
):
    """⚠️ `想知道` / `需要确定` 里的认知谓语不是在断言，恰恰是在**提问**
    （base 是 False——危险方向，Codex P2 第六十二轮，同族实测 56 条）。

    第五十八轮把肯定认知谓语收进框架表时没挡左界，于是
    `停止播放前想知道是否合适` 的 `是否` 被中和掉、执行了取消，
    用户还没决定要不要停。

    ⚠️ 这是这个 PR 里第八个「白名单词是更长表达的子串」入口。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前{inquiry}{cognition}是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("cognition", _positive_cognition_frames())
@pytest.mark.parametrize("wh", ["谁唱的", "哪首更好", "什么歌"])
def test_an_asserted_cognition_predicate_still_governs(cognition, wh):
    """⚠️ 反向：**没有**探询助动词时，肯定认知谓语照旧管着宾语从句
    （第五十八轮修的，base 全是 True）。左界不能把这一族一起挡掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为我{cognition}{wh}'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "negated", ["不知道", "不记得", "不記得", "不清楚", "不确定", "不確定"]
)
def test_negated_cognition_predicates_keep_no_left_guard(negated):
    """⚠️ 左界**只加在肯定形上**。否定形不挡——`想不知道` 不是说法，
    而 `不知道` 前面本来就可以接任何主语。

    这条同时是**边界断言**：把否定形也搬进 `_ZH_POSITIVE_COGNITION_FRAMES`
    会当场见红。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert negated not in mr._ZH_POSITIVE_COGNITION_FRAMES
    text = f'我想停止播放因为我{negated}谁唱的'
    assert mr.is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "compound", ["直播", "转播", "轉播", "重播", "广播", "廣播", "主播",
                 "投放", "开放", "開放", "好听", "好聽", "难听", "收听", "收聽"]
)
def test_a_single_char_playback_verb_needs_a_left_boundary(compound):
    """⚠️⚠️ 单字的 放/播/听/聽 同时是 直播/转播/好听 的**尾字**。

    `我想停止播放是否会影响直播《原神》` 里 `直播` 的 `播` 被当成播放动词、
    《原神》被当成它的标题；而标题遮蔽那一段整体是**原子组**，一旦匹配就不回退，
    于是标题之前的整段（含真正的 `是否`）被一并吞进前缀，守卫不开火，
    一句提问执行了取消（base 是 False——危险方向，第六十三轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放是否会影响{compound}《原神》'
    assert is_explicit_music_cancellation(text) is False, text


def test_real_single_char_playback_verbs_still_shield_their_title():
    """⚠️ 反向：左界是**白名单**，命令语境里的单字动词照旧遮蔽标题。

    第五十四轮那条「一句话里多个播放动词各带各的标题」必须还活着。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in ('停止听《晴天》然后停止播放《好不好》',
                 '我想停止播放《你好吗？》',
                 '别放《是不是》了'):
        assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize("polarity", ["是否", "能否", "可否", "是不是", "能不能"])
@pytest.mark.parametrize(
    "cognition", ["不知道", "不清楚", "不确定", "知道", "清楚", "确定"]
)
def test_a_cognition_frame_does_not_neutralize_polarity_by_itself(
    cognition, polarity
):
    """⚠️⚠️ **认知谓语只管 wh 宾语从句，不管极性标记。**

    第五十八轮收认知谓语的理由是 `因为我知道谁唱的` 在说理由——那是个 **wh**
    宾语从句。同一条规则套到极性标记上就反了：`停止播放不知道是否合适` 是用户
    在**犹豫**，犹豫的对象正是「要不要停」本身（base 全是 False——危险方向）。

    ⚠️ 这条同时收掉了「肯定认知谓语左界」那一整族（先确定/弄清楚/问清楚/早知道）。
    第六十二轮用助动词表挡左邻，但左邻是**开集**，补不干净；改成从**补语类型**
    上判就闭合了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{cognition}{polarity}合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("prefix", ["先", "弄", "说", "問", "问", "早", "还", "得"])
@pytest.mark.parametrize("cognition", ["确定", "清楚", "知道", "记得"])
def test_any_left_neighbour_of_a_cognition_predicate_is_covered(prefix, cognition):
    """⚠️ 左邻是开集：先确定 / 弄清楚 / 说清楚 / 问清楚 / 早知道 / 还记得 / 得确定。
    按补语类型判之后，左邻是什么都不影响结论（base 全是 False）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{prefix}{cognition}是否会丢进度'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("polarity", ["是不是", "能不能", "是否", "能否", "有无", "有無"])
@pytest.mark.parametrize("reason", ["因为", "因為", "由于", "由於", "既然"])
def test_a_reason_marker_restores_polarity_neutralization(reason, polarity):
    """⚠️ 但认知谓语 + 极性标记**被理由标记管着时**仍然是断言：
    `因为不知道是否好歌都不想听` 是在说停歌的理由（第五十三轮，base 是 True）。

    有 `因为` 时整段是理由小句；没有时 `不知道是否合适` 就是在犹豫要不要停。
    这两条正反用例一起把判据夹住。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{reason}不知道{polarity}好歌都不想听'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize("wh", ["谁唱的", "誰唱的", "哪首更好", "什么歌"])
@pytest.mark.parametrize("cognition", ["知道", "记得", "不知道", "不记得", "清楚"])
def test_a_cognition_frame_still_neutralizes_wh(cognition, wh):
    """⚠️ 反向：wh 那一半照旧中和（第五十八轮，base 全是 True）。
    「只管 wh 不管极性」这条判据的另一半，缺了它上面那条就成了单边收窄。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为我{cognition}{wh}'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize(
    "compound",
    ["主要是", "只要是", "需要是", "重要是", "也就是", "不就是", "这就是",
     "那就是", "立即使用", "随即使用", "别忘了", "差点忘了"],
)
def test_frame_words_matched_inside_a_longer_word_do_not_open_a_frame(compound):
    """⚠️⚠️ 短框架词会被更长的词从中间命中：要是 ⊂ 主要是，就是 ⊂ 也就是，
    即使 ⊂ 立即使用，忘了 ⊂ 别忘了。这些更长表达意思正相反，却照样中和掉辖域内的
    极性标记，一句提问执行成停止播放（base 全是 False——危险方向，第六十三轮）。

    ⚠️⚠️ 这里用**黑名单**，跟这个文件里其它左界的取舍相反，理由是**方向**：
    每加一条只会让框架少认一次 ＝ 少停一次歌（轻）。漏掉一条等于维持现状，
    不引进新风险。而白名单在这里不可行——实测左邻是开集。
    ⚠️ 也因此这条修**是部分的**：没列进来的组合仍然会误开框架。

    ⚠️ 后视必须贴着被挡词的首字、只回看一个字。写成 `(?<![主只需重]要)要是` 时
    引擎在 `要` 之前回看两个字，句首越界即通过——第一版就是这么写的，等于没挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{compound}想问能否换一首'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["要是", "就是", "即使", "忘了", "不管"])
def test_the_guarded_frames_still_work_on_their_own(frame):
    """⚠️ 反向：挂了左右界的那几个框架词，**在正常语境里**照旧是框架。
    黑名单只挡特定前缀/后缀，不能把整个词废掉。

    ⚠️ 探测句要带上后续小句（`什么歌都不好听`）而不是让框架词悬在句尾——
    第六十七轮给 `要是` 加了「右边必须跟谓词或疑问词」之后，光一个 `要是`
    结尾本来就不该算框架。原来那句是**空断言**：它测的是「框架词还在表里」，
    而不是「框架还成立」。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert mr._ZH_FREE_CHOICE_FRAME_RE.search(
        f'我想停止播放因为{frame}什么歌都不好听'
    ) is not None
    text = f'我想停止播放因为{frame}什么歌都不好听'
    assert mr.is_explicit_music_cancellation(text) is True, text


def test_a_temporal_clause_is_not_joined_across_a_negation():
    """⚠️ 后一子句是**否定**时不能合并：`播放的时候，不要再放音乐了` 里的 `再`
    恰好是关联副词，合并之后 `不要再放` 跟前半句连成一体，整条取消请求丢掉
    （base 是 True，第六十三轮）。

    ⚠️ 反向断言钉住第四十六轮那条合并本身没被废掉。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation('播放的时候，不要再放音乐了') is True
    assert is_explicit_music_cancellation('停止播放的时候，顺便关灯') is True


@pytest.mark.parametrize("inquiry", ["想问", "要问", "想問", "要問", "想说", "要说"])
def test_the_focus_adverb_reading_of_jiushi_is_not_a_frame(inquiry):
    """⚠️ `就是` 作**焦点副词**时（就是想问 / 就是要问）不是让步框架，恰恰在引出
    用户的问题：`我想停止播放就是想问是否合适` 里 `是否` 被中和掉之后直接执行了
    取消，用户只是在问（base 是 False——危险方向，第六十四轮）。

    ⚠️ 右界表这一轮先被我当死代码删过一次——当时确实没有任何失败用例支撑它。
    这条给出了真用例，所以加回来。删和加用的是同一条规则：**没有失败用例的
    防御不留，有的就留**。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放就是{inquiry}是否合适'
    assert is_explicit_music_cancellation(text) is False, text
    # ⚠️ 反向：真让步用法不受影响。
    assert is_explicit_music_cancellation(
        '我想停止播放因为就是什么歌都不好听'
    ) is True


@pytest.mark.parametrize("adversative", ["却", "卻"])
def test_the_adversative_coordinator_ends_the_frame_scope(adversative):
    """转折连词 却/卻 当初漏在连词表外，框架辖域越过它把真疑问词中和掉
    （base 是 False——危险方向，第六十四轮）。

    ⚠️ 第六十一轮那张表自称「连词是封闭词类，一次列全」，结果还是漏了——
    自称列全不等于列全，这条并进那张表之后由简繁配对守卫和这条用例一起兜住。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放如果会影响歌单就算了{adversative}这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("title", ["因为爱情", "原因", "既然青春留不住", "既然琴瑟起", "晴天", "红色高跟鞋"])
def test_the_verdict_does_not_depend_on_the_song_title(title):
    """⚠️ 找理由标记时要**跳过引用跨度**：《因为爱情》是歌名，里面的 `因为`
    不是理由标记（第六十五轮扫描发现）。

    ⚠️ 这条的判据不是「哪个值对」，而是**判定不能取决于歌名内容**——
    同一句话换成《晴天》结论就反过来，那显然是错的。三行之上那个框架词循环
    本来就跳过跨度，这里没跟上，同一段文本两套读法。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'帮我停止播放《{title}》不知道能否再点回来'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("reason", ["因为", "因為", "由于", "由於", "既然"])
def test_a_reason_marker_outside_quotes_still_counts(reason):
    """⚠️ 反向：引号**外面**的理由标记照旧算数，别把这一支一起挡掉。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{reason}不知道是不是好歌都不想听'
    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize("separator", ["，", ",", "；", ";", "。"])
@pytest.mark.parametrize("reason", ["因为", "因為", "由于", "既然"])
def test_a_reason_marker_in_an_earlier_clause_does_not_count(separator, reason):
    """⚠️ 理由标记只在**同一子句内**算数：`因为下雨了，帮我停止播放不知道是否合适`
    里那个 `因为` 管的是「下雨了」，不是后面这句犹豫。

    ⚠️ 这条是第六十五轮变异验证补的——那次 `hit.start() >= boundary` 这道限制
    **存活了**（没有任何用例覆盖）。它不是死代码，是活的但没测，两者要分清：
    死代码该删（右界表那次就删了），活代码没测该补用例。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'{reason}下雨了{separator}帮我停止播放不知道是否合适'
    assert is_explicit_music_cancellation(text) is False, text
    # ⚠️ 反向：同一子句内的理由标记照旧算数。
    assert is_explicit_music_cancellation(
        f'我想停止播放{reason}不知道是不是好歌都不想听'
    ) is True


@pytest.mark.parametrize("marker", ["是否", "能否", "可否", "为什么", "怎么办", "好不好"])
@pytest.mark.parametrize("verb", ["播放", "听", "放"])
def test_title_shielding_does_not_scan_past_a_question_marker(verb, marker):
    """⚠️⚠️ 标题遮蔽的懒扫描**不能跨过疑问标记**（base 是 False——危险方向，第六十六轮）。

    `我想停止播放是否会影响稍后播放《晴天》` 里，扫描为了够到第二个 `播放《晴天》`
    一路吃掉了用户真正的 `是否`；那一段又是**原子组**，吃掉就不回退，
    标记位上什么都不剩、守卫开不了火，一句提问执行了取消。

    ⚠️ 判据是 tempered dot：扫描的每个字符前先确认「这里不是疑问标记的开头」。
    标记表跟守卫用的是**同一份**，不另抄——抄一份就会漂开。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{marker}会影响稍后{verb}《晴天》'
    assert is_explicit_music_cancellation(text) is False, text


def test_quoted_titles_are_still_shielded_after_the_tempering():
    """⚠️ 反向：引号**里面**的标记照旧被遮蔽，别让 tempering 把这一支打掉。

    扫描类本来就不含开引号，遇到 `《` 就停，所以标题自带的 `吗/是不是` 不受影响。
    第五十四轮那条「一句话里多个播放动词各带各的标题」也钉在这里。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in ('我想停止播放《你好吗？》',
                 '停止听《晴天》然后停止播放《好不好》',
                 '我想停止播放《是不是》',
                 '帮我停止播放红心歌单'):
        assert is_explicit_music_cancellation(text) is True, text


def test_the_frame_right_blacklist_is_pinned():
    """⚠️ 这张表到第六十六轮是**第三次登场**，钉住它免得看着像反复横跳：

    * 第六十三轮为 `不管用` 加过 → 变异验证显示删掉行为完全不变（那句被别的判据
      拦住了）→ 按「没有失败用例的防御就是死代码」删掉；
    * 第六十四轮 `就是想问` 给出真用例 → 加回来，只收 `就是`；
    * 第六十六轮 `按钮不管用` 给出真用例 → 才把 `不管` 收进来。

    三次用的是同一条规则，变的是证据。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    # ⚠️ 第七十四轮给 `就是` 补了 `不`：`就是不知道是否合适` 是焦点副词 + 犹豫。
    assert mr._ZH_FRAME_RIGHT_BLACKLIST == {"就是": "想要问問说說不", "不管": "用"}


@pytest.mark.parametrize("tail", ["是否需要修复", "是否要修", "能否修好", "怎么办"])
def test_bu_guan_yong_is_an_adjective_not_a_frame(tail):
    """`不管用` 是「不 + 管用（有效）」，不是任指框架：`停止播放时发现按钮不管用
    是否需要修复` 里用户在报按钮坏了并提问，`是否` 被中和后执行了取消
    （base 是 False——危险方向，第六十六轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放时发现按钮不管用{tail}'
    assert is_explicit_music_cancellation(text) is False, text
    # ⚠️ 反向：`不管` 作真框架时不受影响。
    assert is_explicit_music_cancellation(
        '我想停止播放因为不管什么歌都不好听'
    ) is True


# ⚠️ `简要` 原来写了两遍（CodeRabbit）——本该是简繁成对，补成 `簡要`。
@pytest.mark.parametrize("noun", ["摘要", "纪要", "紀要", "概要", "提要", "纲要", "綱要", "简要", "簡要", "主要"])
def test_a_noun_ending_in_yao_does_not_open_a_conditional_frame(noun):
    """⚠️⚠️ `要是` 单靠左界黑名单收不干净：它前面那个字构成的名词是**开集**——
    摘要 / 纪要 / 概要 / 提要 / 纲要 / 简要 / 主要 / 只要 / 需要 / 重要 / 首要…
    第六十六轮补了 主/只/需/重/首/次，第六十七轮 reviewer 又拿 `摘要是` 来了。

    换个方向就闭合了：看**右边**。`摘要是最新版本` / `纪要是否完整` 里 `要是`
    后面跟的是名词（那个 `是` 其实是下一小句的系词）；真条件框架后面跟的一定是
    谓词或疑问词——`要是有新歌` / `要是不好听` / `要是什么歌都不好听`。
    谓词和疑问词都是**闭集**，而且这两张表本来就在这个文件里，不用另立门户。

    ⚠️ 左界黑名单**保留**：`主要是想问` 右边跟的是 `想`（谓词），右侧规则放它过去，
    靠左界那条挡。两条各管一半，缺一不可——下面第二条断言钉住这一点。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前先检查{noun}是最新版本是否合适'
    assert is_explicit_music_cancellation(text) is False, text


def test_the_left_and_right_guards_each_cover_a_different_half():
    """⚠️ 两条判据各管一半，缺一不可：

    * `摘要是最新版本` —— 右边是名词，**右**侧规则挡下；左界里没有 `摘`。
    * `主要是想问` —— 右边是谓词 `想`，右侧规则放行，**左**界里的 `主` 挡下。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    # ⚠️ 第七十五轮起 `摘` **在**左界里了：右侧必需只挡得住「系词接名词」那半边，
    # `纪要是有用的` 这种「系词接谓词」的照旧开框架（50 个组合里 45 个从没跑过，
    # 因为那条派生测试只喂名词补语）。两条判据的分工因此变了——右侧管名词补语，
    # 左界管这一族名词本身。
    assert "摘" in mr._ZH_FRAME_LEFT_BLACKLIST["要是"]
    assert "主" in mr._ZH_FRAME_LEFT_BLACKLIST["要是"]
    assert mr._ZH_FRAME_RIGHT_REQUIRED.get("要是") is True
    assert mr.is_explicit_music_cancellation(
        '我想停止播放前先检查摘要是最新版本是否合适'
    ) is False
    assert mr.is_explicit_music_cancellation(
        '我想停止播放主要是想问能否换一首'
    ) is False


@pytest.mark.parametrize(
    "coordinator", ["随后", "隨後", "继而", "繼而", "而后", "而後", "紧接着", "緊接著",
                    "跟着", "跟著", "于是", "於是", "因而", "从而", "從而"]
)
def test_the_sequential_coordinators_also_end_the_frame_scope(coordinator):
    """顺承类连词当初只收了 然后/接着，随后/继而/而后/紧接着 同族漏了
    （base 都是 False——危险方向，第六十七轮）。

    ⚠️ 这张表第六十一轮建立时写着「连词是封闭词类，一次列全」，到这里已经是
    **第三次补**（第六十四轮补 却/卻，这一轮补顺承一族）。自称列全不等于列全，
    所以这次按「顺承 / 转折 / 因果 / 并列」四族逐族过了一遍。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放如果会影响歌单就算了{coordinator}这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    ("predicate", "rest"),
    [("有", "新歌再告诉我"), ("不", "好听就算了"), ("是", "新歌就留着"),
     ("能", "联网就继续"), ("会", "卡就算了"), ("要", "收费就算了")],
)
def test_a_conditional_frame_opens_on_a_predicate_too(predicate, rest):
    """⚠️ `要是` 的右侧判据认**两族**：谓词和疑问词。

    ⚠️ 这条是第六十七轮变异验证补的——「小句开头表漏掉谓词那一族」那个变异体
    **存活了**：原来的用例只走 wh 分支（`要是什么歌都不好听`），谓词分支
    （`要是有新歌`）一条都没测到。两族各测一遍，缺哪半都会见红。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    text = f'我想停止播放要是{predicate}{rest}'
    assert mr._ZH_FREE_CHOICE_FRAME_RE.search(text) is not None, text
    assert mr.is_explicit_music_cancellation(text) is True, text


def _music_join_negators() -> list[str]:
    from main_logic.music_requests import _ZH_TEMPORAL_JOIN_NEGATORS

    table = list(_ZH_TEMPORAL_JOIN_NEGATORS)
    assert table, "_ZH_TEMPORAL_JOIN_NEGATORS 是空的"
    return table



@pytest.mark.parametrize("noun", ["基因", "原因", "病因", "死因", "成因"])
def test_a_noun_containing_yin_is_not_a_reason_marker(noun):
    """⚠️⚠️ 单字 `因`/`既` 已经从理由标记表里**删掉**，别再加回来。

    它们是 基因 / 原因 / 病因 / 既有 的子串，一进表就把 `对基因报告不确定是否合适`
    判成「有理由标记 → 是断言」，`是否` 被中和、执行了取消
    （base 是 False——危险方向，第六十八轮）。

    ⚠️ 删而不是加黑名单：含它们的名词是**开集**，黑名单堵不完；多字形
    因为/由于/既然 已经覆盖真实用法。当初加这两个单字时**没有失败用例支撑**。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert "因" not in mr._ZH_REASON_MARKERS
    assert "既" not in mr._ZH_REASON_MARKERS
    text = f'我想停止播放前对{noun}报告不确定是否合适'
    assert mr.is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("coordinator", ["但", "但是", "不过", "不過", "可是", "然而", "却"])
@pytest.mark.parametrize("reason", ["因为", "由于", "既然"])
def test_a_reason_does_not_govern_across_a_coordinator(coordinator, reason):
    """⚠️ 理由标记的辖域边界要跟**框架辖域同源**：`因为音质差但不知道是否合适`
    里那个理由属于 `但` 之前那一小句，管不到后面的犹豫（base 是 False，第六十八轮）。

    上一版这里只认标点，框架那边早就连词也认了——这是这个 PR 里**第三次**栽在
    「同一个概念两处各写一份」上（前两次：子句切分 vs 否定守卫窗口、标记表 vs 守卫）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{reason}音质差{coordinator}不知道是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("quote", ["'", "‘"])
def test_an_ambiguous_quote_still_shields_a_title_from_reason_lookup(quote):
    """⚠️ 理由标记的跨度要连**歧义引号**一起算：`'因为爱情'` 也是歌名。

    主跨度表把 `'`/`‘` 排除在外是为了 Guns N' Roses 那个撇号，但那条取舍属于
    **标题遮蔽**；放到理由标记这里方向正好相反——多算一段跨度 ＝ 少认一个理由标记
    ＝ 少停一次歌（轻），少算一段则是把用户的犹豫执行成取消（重）。

    ⚠️ 不这么改的话，第六十五轮那条修复自己写的理由「判定不该取决于歌名」就只
    兑现了一半：换个引号同一个歌名结论又反过来。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    closer = quote if quote == "'" else "’"
    text = f'帮我停止播放{quote}因为爱情{closer}不知道是否合适'
    assert is_explicit_music_cancellation(text) is False, text
    # ⚠️ 反向：撇号那条取舍没被打掉——标题遮蔽仍然不把 ' 当开引号。
    assert is_explicit_music_cancellation("别放 Guns N' Roses 了") is True


@pytest.mark.parametrize("negator", _music_join_negators())
def test_the_temporal_join_skips_every_cancellation_form(negator):
    """⚠️ 时间小句合并的否定前视要跟「取消播放」那一族**同源**：`无需再放音乐了` /
    `取消再播放音乐` 都是明确的取消请求，合并之后前半句连上来就匹配不到了
    （base 是 True，第六十八轮）。手写那一版只列了 不/别/甭/莫/勿/停。

    ⚠️ 这条直接打**机制**（那个逗号有没有被吃掉），不打端到端结论。
    第一版写成「每个词都要让整句成为取消请求」，结果 `停再放音乐了` /
    `退出再放音乐了` 这些根本不成句的组合全红——**派生笛卡尔积断言了机制并不
    保证的东西**。端到端只留下面那几句真实说法。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    text = f'播放的时候，{negator}再放音乐了'
    assert mr._ZH_TEMPORAL_CLAUSE_JOIN_RE.sub(r"", text) == text, text


def test_the_temporal_join_still_fires_without_a_negation():
    """⚠️ 反向：没有否定时那条合并照旧生效（第四十六轮修的），别把它整条废掉。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    joined = '停止播放的时候，顺便关灯'
    assert mr._ZH_TEMPORAL_CLAUSE_JOIN_RE.sub(r"", joined) != joined
    assert mr.is_explicit_music_cancellation(joined) is True


@pytest.mark.parametrize(
    "sentence",
    ['播放的时候，无需再放音乐了', '播放的時候，無需再放音樂了',
     '播放的时候，取消再播放音乐', '播放的时候，不要再放音乐了'],
)
def test_a_cancellation_after_a_temporal_clause_survives(sentence):
    """端到端：这几句都是真实说法，base 全是 True。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(sentence) is True, sentence


@pytest.mark.parametrize("frame", ["如果", "假如", "若是", "要是", "倘若", "万一", "萬一", "假若"])
@pytest.mark.parametrize(
    "mention",
    ["这个说法", "這個說法", "的用法", "这个词", "這個詞", "这种写法", "這種寫法"],
)
def test_a_metalinguistic_mention_does_not_open_a_conditional_frame(frame, mention):
    """⚠️ 用户在问**措辞**本身合不合适（元语言提及）时，框架不该开：
    `先确认万一这个说法是否合适` base 是 False，`是否` 被中和后执行了取消（第六十九轮）。

    ⚠️ 没有按 reviewer 说的去枚举「万一这个说法 / 万一的用法」——那一侧是开集
    （这个说法 / 的用法 / 这个词 / 这种写法 / 这个表达…）。第六十七轮给 `要是` 挂的
    「右边必须跟小句」本来就把它们排除了，只是当初只挂在一个词上；这一轮推广到整个
    条件族，一条判据覆盖整族。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前先确认{frame}{mention}是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["如果", "假如", "要是", "万一"])
@pytest.mark.parametrize(
    "head",
    ["有新歌", "不好听", "不好聽", "是新歌", "听《晴天》", "聽《晴天》",
     "放不出来", "放不出來"],
)
def test_a_conditional_frame_still_opens_on_a_real_clause(frame, head):
    """⚠️ 反向：真小句照旧开框架。小句开头认三族——谓词、疑问词、**播放动词**。

    播放动词那一支是这一轮补的：推广右侧必需之后
    `因为如果听《晴天,雨天》有什么问题再告诉我` 当场见红——`听` 是动词但不在
    `_ZH_FRAME_PREDICATE_RE` 那个小表里，那张表当初是为别的判据挑的。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    text = f'我想停止播放因为{frame}{head}都不想听'
    assert mr._ZH_FREE_CHOICE_FRAME_RE.search(text) is not None, text


@pytest.mark.parametrize("coordinator", ["同时", "同時"])
def test_simultaneous_coordinators_end_the_frame_scope(coordinator):
    """并行类连词第七十轮补——这张表已经是**第四次**补了（第六十一轮建表时写着
    「连词是封闭词类，一次列全」，六十四轮补 却/卻，六十七轮补顺承一族，现在补并行）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放如果会影响歌单就算了{coordinator}这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("title", ["你好吗？", "你好嗎？", "是不是？", "好不好？"])
def test_a_bare_question_mark_before_a_following_clause_still_counts(title):
    """⚠️ 标题自带的 `吗？` 被正确遮蔽之后，用户那个**裸问号**可能是整句唯一的疑问
    信号；切分把它丢掉，前缀疑问守卫（它要求标记在末尾）就永远看不到它
    （base 是 False——危险方向，第七十轮）。

    ⚠️ 两处都要改才生效：切分时**保留问号**（别的分隔符照旧丢——留逗号会让
    「否定只在自己子句内」那条判据的窗口跨过去），以及守卫里要**消耗**掉那个问号
    而不是零宽前视（尾巴段的字符类不含 `？`，零宽的话整条守卫静默失效）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放《{title}》？我还没决定'
    assert is_explicit_music_cancellation(text) is False, text


def test_question_marked_commands_without_the_inquiry_prefix_still_work():
    """⚠️ 反向：那条裸问号分支只挂在 `(?:我)?(?:想|要)` 那个前缀形状上。

    `停止播放？` / `别放音乐了？` / `帮我停止播放红心歌单？谢谢` base 全是 True，
    不能被一起打掉——base 在「问号结尾」这件事上本来就不一致，不能一刀切。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    for text in ('停止播放？', '别放音乐了？'):
        assert is_explicit_music_cancellation(text) is True, text
    # ⚠️⚠️ **这一条是 by-design 的代价**：`帮我停止播放红心歌单？谢谢`
    # base 是 True、现在是 False。
    #
    # `帮我` 也在那条守卫的前缀集合里，所以保留问号之后第一个子句变成
    # `帮我停止播放红心歌单？`，守卫开火。base 在这件事上本来就**自相矛盾**——
    # 同一句话单独出现（`帮我停止播放红心歌单？`）base 就是 False，只有后面再跟
    # 一个子句时才是 True，因为那个问号被切分丢掉了。这次改动只是让两者一致。
    #
    # 交易：修掉 1 条第 1 类（提问被执行成取消），引进 1 条第 3 类（少停一次歌）。
    # 方向上成立。⚠️ 对照第六十五轮那次**相反**的判断：那次是 1 条第 3 类换来
    # 2 条第 1 类，所以整个退回。同一条判据，方向不同结论就不同。
    assert is_explicit_music_cancellation('帮我停止播放红心歌单？谢谢') is False
    assert is_explicit_music_cancellation('帮我停止播放红心歌单？') is False
    assert is_explicit_music_cancellation('帮我停止播放红心歌单') is True


@pytest.mark.parametrize("compound", ["成就算法", "迁就算了", "遷就算了", "造就算不算"])
def test_a_concessive_frame_inside_a_compound_does_not_open(compound):
    """⚠️ 让步族也挂上「右边必须跟小句」：`成就算法是否正确` 里 `就算` 是
    `成就`+`算法` 的接缝，却开出框架把 `是否` 中和掉（base 是 False，第七十一轮）。

    左界黑名单在这一族上是打地鼠（成就/迁就/造就/将就/俯就…），右侧一条就闭合：
    `就算法` 后面是 `法`，不是小句开头。这跟第六十七轮 `要是` 那次是同一条判据，
    这轮把它从条件族推广到让步族。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前确认{compound}是否正确'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["即使", "即便", "就算", "哪怕", "纵使", "縱使"])
@pytest.mark.parametrize(
    "head",
    ["有新歌", "不好听", "不好聽", "什么歌都一样", "什麼歌都一樣",
     "听《晴天》", "聽《晴天》"],
)
def test_a_concessive_frame_still_opens_on_a_real_clause(frame, head):
    """⚠️ 反向：真让步小句照旧开框架，右侧要求不能把整族废掉。"""  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    text = f'我想停止播放因为{frame}{head}都不想听'
    assert mr._ZH_FREE_CHOICE_FRAME_RE.search(text) is not None, text


@pytest.mark.parametrize(
    "sentence",
    ["Can you play Yellow?", "Could you play Yellow?", "Would you play Yellow?",
     "Could you please play Yellow by Coldplay?"],
)
def test_english_question_form_requests_still_parse(sentence):
    """⚠️⚠️ 切分**两种问号都保留**，剥离由解析入口 `rstrip("？?")` 完成；
    这条验的是**剥掉之后英文点歌仍能匹配**。

    第七十轮「保留问号」的第一版两个都留、不剥，英文疑问式点歌当场全线失效——
    `Can you play Yellow?` 后面挂着 `?` 就匹配不上英文解析器
    （tests/unit/test_proactive_service_boundary.py 6 条红）。第七十二轮
    改成「只留全角」是个**只对一半输入成立**的修法（中文用户也打半角），
    第七十四轮才改成两种都留、剥离下沉到两个入口。

    ⚠️ 那个文件不在我常跑的几个文件里，是**跑全量**才抓到的。这条用例把它挪到
    这里，让改动这条判据的人在常跑的文件里就能见红。

    ⚠️⚠️ 入口必须是 `parse_explicit_user_music_request`，不是 `parse_music_request`——
    后者是宽松兜底，**永远返回一个对象**，拿它写 `is not None` 是空断言。
    第一版就是这么写的，变异（把 ASCII `?` 也留下）当场存活才发现。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    request = parse_explicit_user_music_request(sentence)
    assert request is not None, sentence
    assert "Yellow" in request.keyword, (sentence, request.keyword)
    assert "?" not in request.keyword, (sentence, request.keyword)


def test_both_question_marks_survive_clause_splitting():
    """⚠️ 切分**两种问号都留**，剥离放在两个解析入口里做。

    第七十二轮我是靠「切分不保留 ASCII `?`」来保英文点歌的，那是个**只对一半输入
    成立**的写法：中文用户也会打半角问号（`《你好吗？》?我还没决定`），那样取消
    守卫又看不见了（base 是 False——危险方向，第七十四轮）。
    两边都保留、`_parse_explicit_zh_clause` 和 `_parse_explicit_en_clause` 两个入口
    都剥，才是对称的。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    assert mr._split_music_request_clauses('停止播放？后面还有') == ['停止播放？', '后面还有']
    assert mr._split_music_request_clauses('stop playing? more text') == [
        'stop playing?', 'more text'
    ]


@pytest.mark.parametrize(
    "text", ["播放《晴天》？", "播放晴天？", "播放《晴天》"]
)
def test_a_preserved_question_mark_does_not_leak_into_playback_parsing(text):
    """⚠️⚠️ 保留下来的问号**只活到取消守卫为止**。

    第七十轮为了让取消守卫看见裸问号而保留分隔符，可后面所有点歌判据都是
    **精确匹配**：`播放《晴天》？` 的 keyword 变成 `晴天》？`，
    `播放这个视频？` 更糟——base 正确地拒绝了它（不是音乐），却被当成歌名去搜
    （base=None → now 有值，Codex P2 第七十三轮）。

    ⚠️ 这是**同一个改动第二次漏到别的路径**：上一轮漏的是英文点歌（ASCII `?`，
    跑全量才抓到），这次是中文点歌。保留分隔符这种改动的影响面比它看起来大。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    request = parse_explicit_user_music_request(text)
    assert request is not None, text
    assert request.keyword == "晴天", (text, request.keyword)


def test_a_non_music_target_with_a_question_mark_is_still_rejected():
    """⚠️ `播放这个视频？` base 是 None（不是音乐），保留问号一度让它绕过非音乐
    目标的检查、变成一次音乐搜索。这条钉住它回到 base。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import parse_explicit_user_music_request

    assert parse_explicit_user_music_request('播放这个视频？') is None
    assert parse_explicit_user_music_request('play a video?') is None


@pytest.mark.parametrize(
    "coordinator", ["反而", "反倒", "况且", "況且", "何况", "何況", "再者", "此外"]
)
def test_adversative_and_additive_coordinators_end_the_frame_scope(coordinator):
    """转折/递进类连词第七十三轮补——这张表**第五次**补了。

    ⚠️ 它第六十一轮建立时写着「连词是封闭词类，一次列全」，五轮下来那句话一次都
    没兑现过。记在这里不是为了再保证一次，是为了让下一个人知道这张表历史上一直在漏。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放即使会影响歌单也算了{coordinator}我想问这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("mark", ["？", "?"])
def test_either_question_mark_reaches_the_cancellation_guard(mark):
    """⚠️ 中文用户两种问号都会打。第七十二轮只保留全角是**只对一半输入成立**的写法。"""  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放《你好吗？》{mark}我还没决定'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["即使", "即便", "就算", "哪怕", "纵使", "縱使"])
@pytest.mark.parametrize("mention", ["这个说法", "這個說法", "的用法", "这个词"])
def test_a_concessive_metalinguistic_mention_does_not_open_a_frame(frame, mention):
    """⚠️ 让步族的右侧要求**加回来了**。这一支的来回值得记：

    第七十一轮加 → 变异验证显示多余（`成就算法` 已被左界挡住）→ 按「没有失败用例
    支撑的防御就是死代码」删掉；这一轮 `先确认即使这个说法是否合适` 给出真用例
    （base 是 False——危险方向）→ 加回来。

    同一条规则用了四次，变的不是规则是证据。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前先确认{frame}{mention}是否合适'
    assert is_explicit_music_cancellation(text) is False, text


def test_jiushi_before_an_uncertainty_predicate_is_not_a_frame():
    """`就是不知道是否合适` 是焦点副词 + 犹豫（base 是 False，第七十四轮）。
    原来的右界只挡 想/要/问/说 那一族探询动词，`不` 漏了。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation('我想停止播放就是不知道是否合适') is False
    # ⚠️ 反向：真让步用法不受影响。
    assert is_explicit_music_cancellation('我想停止播放因为就是什么歌都不好听') is True


@pytest.mark.parametrize("title", ["如果有一天", "如果没有你", "假如爱有天意"])
def test_a_predicate_initial_song_title_is_not_a_conditional_frame(title):
    """⚠️⚠️ 第六十三轮那段注释把方向**标反了**，一躺十一轮。

    它写着「《如果有一天》这类以谓词开头的歌名会被当成条件框架，少拦一次提问——
    **轻的那一侧**」。可「少拦一次提问」＝提问没被拦住＝取消照常执行，那是**重**
    的那一侧。`我想停止播放如果有一天是否合适` base 是 False、一度是 True。

    收紧判据：在播放目标位上，只有谓词后面**近处还跟着疑问词**时才算真框架
    （`如果有什么新歌`，第四十八轮那条），否则一律当标题。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放{title}是否合适'
    assert is_explicit_music_cancellation(text) is False, text
    # ⚠️ 反向：第四十八轮那条真条件框架不能被打回去。
    assert is_explicit_music_cancellation(
        '我想停止播放如果有什么新歌再告诉我'
    ) is True


@pytest.mark.parametrize("noun", ["摘要", "纪要", "紀要", "概要", "提要", "纲要", "綱要", "简要", "簡要"])
@pytest.mark.parametrize(
    "complement", ["有用的", "不完整的", "要保留的", "能看的", "没写完的"]
)
def test_a_yao_noun_before_a_predicate_complement_does_not_open_a_frame(noun, complement):
    """⚠️⚠️ 右侧必需只挡得住「系词接**名词**」那半边（`摘要是最新版本`）。

    系词最常见的补语恰恰以 有/不/要/能/没 开头，那五个字都在
    `_ZH_FRAME_PREDICATE_RE` 里、是合法小句开头，于是 `纪要是有用的` 照旧开框架、
    把 `是否` 中和掉（base 是 False——危险方向，第七十五轮）。

    ⚠️ 这条缺口是**派生测试的固有盲区**：原来那条只喂名词补语，
    9 名词 × 5 谓词补语的 45 个组合一个都没跑过。补上左界之后两侧都盖到。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前先检查{noun}是{complement}是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("verb", ["成", "迁", "遷", "造", "将", "將", "俯"])
def test_jiushi_shares_the_same_seam_as_jiusuan(verb):
    """⚠️ `就是` 和 `就算` 坐在**同一个接缝**上（动词+就），两张左界黑名单原来
    互不相交、各缺对方那一半。`成就是有奖励的` 比触发第七十一轮那条修复的
    `造就算不算` 常见得多（base 是 False，第七十五轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    for frame in ("就是", "就算"):
        assert verb in mr._ZH_FRAME_LEFT_BLACKLIST[frame], (verb, frame)
    assert mr.is_explicit_music_cancellation(
        f'我想停止播放这个{verb}就是有奖励的是否合适'
    ) is False, verb


# ⚠️⚠️⚠️ 这一组是**统一左界**的守卫。它替代了「逐个往黑名单补字」那条路。
_FRAME_SEAM_WORDS = [
    # X要（名词/副词）+ 是
    # ⚠️ 繁体那一半必须显式列：实现侧 `_ZH_FRAME_LEFT_BLACKLIST` 明确收了
    # 紀/綱/簡/遷/將，也就是说繁体接缝是**实现支持的分支**，只列简体等于那半边
    # 零覆盖。这个 PR 已经在同一个坑里栽过好几次（挨個 / 遭 / 忘记 / 鈴聲）。
    "摘要是", "纪要是", "紀要是", "概要是", "提要是",
    "纲要是", "綱要是", "简要是", "簡要是",
    "主要是", "次要是", "首要是", "重要是", "需要是", "只要是", "想要是",
    "硬要是", "非要是",
    # X就（动词）+ 是/算
    "成就是", "迁就是", "遷就是", "造就是", "将就是", "將就是",
    "俯就是", "屈就是", "高就是",
    "另就是", "早就是", "也就是", "这就是",
    "迁就算", "遷就算", "成就算", "造就算", "将就算", "將就算",
    # X即 + 使 / X不 + 管 / X忘 + 了
    "立即使", "随即使", "隨即使", "当即使", "當即使", "旋即使",
    "才不管", "就不管", "都不管", "全不管",
    "别忘了", "別忘了", "差忘了", "难忘了", "難忘了", "遗忘了", "遺忘了",
]


@pytest.mark.parametrize("seam", _FRAME_SEAM_WORDS)
def test_a_frame_word_on_a_compound_seam_never_opens_a_frame(seam):
    """⚠️⚠️⚠️ **这条守的是一条判据路线，不是一个缺陷。**

    框架词是 2 字中文词、靠**子串匹配**打在任意文本上，而含
    `就是`/`要是`/`即使`/`不管`/`忘了` 接缝的汉语复合词是**无界**的。
    第六十三轮起我一直在往左界黑名单里补字，七十五轮下来随手列 42 个候选
    **还有 10 个是活的第 1 类**——补词这条路不收敛，因为要枚举的是
    「所有含该接缝的复合词」。

    换成统一左界（框架词只在句首 / 标点后 / 非汉字后 / 一个**闭集**汉字后才算
    框架）之后，这 42 个一次全堵住。反过来看是成立的：真框架的左邻是闭集
    ——句首、标点、播放动词尾字、连词尾字、代词、几个助词。

    ⚠️ 判错方向是轻的那一侧：漏认一个框架 ＝ 少中和 ＝ 疑问守卫更容易开火 ＝
    少停一次歌；误认一个框架 ＝ 提问被执行成取消（重）。

    ⚠️ 逐词的左/右黑名单**保留**，两层各管一半：`也就是`/`这就是` 的左邻
    （`也`/`这`）正好落在闭集里，得靠黑名单挡。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放前确认{seam}有用的是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize(
    "text",
    ["我想停止播放如果有什么新歌再告诉我",
     "我想停止播放因为无论唱什么歌都不好听",
     "我想停止播放因为就是什么歌都不好听",
     "我想停止播放要是有新歌再告诉我",
     "我想停止播放因为万一有新歌就麻烦了",
     "我想停止播放因为不知道是不是好歌都不想听",
     "我想停止播放，如果有新歌再说",
     "我想停止播放因为即使换一首也不好听"],
)
def test_real_frames_survive_the_unified_left_bound(text):
    """⚠️ 统一左界的反向断言：真框架的左邻确实都落在那个闭集里。

    这八句覆盖三类左邻来源——播放动词（放）、连词（为）、标点（，）。
    ⚠️ **不含句首**：句首带条件从句的句子本来就不是直接命令（实测 base 全是
    False），端到端断言不出东西。`^` 那一支改由下面
    `test_the_left_bound_start_anchor_is_live` **直接打正则**。
    原来的 docstring 声称覆盖了句首，会让人以为 `^` 分支有回归保护（CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(text) is True, text


@pytest.mark.parametrize("marker", ["那么", "那麼", "那就", "的话", "的話"])
def test_a_conditional_consequent_marker_ends_the_frame_scope(marker):
    """⚠️ **后件标记跟连词是两回事。**

    `那么/那麼/那就/的话/的話` 标的正是条件句**前件的终点**——而前件恰好就是框架
    的辖域。所以它们不是「又几个连词」，是这条辖域判据在语言学上本来就该有的边界。

    `如果有什么新歌那么这样是否合适` 里 `什么` 在前件内该中和，`是否` 在后件里
    不该被碰（base 是 False——危险方向，第七十七轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放如果有什么新歌{marker}这样是否合适'
    assert is_explicit_music_cancellation(text) is False, text


def test_correlatives_are_not_treated_as_consequent_markers():
    """⚠️ 反向：裸 `就`/`都` **不收**——它们在任指框架里是**关联词**，
    收了会把那一族的辖域提前截断（`无论唱什么歌都不好听` 会当场失效）。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    for bare in ("就", "都"):
        assert bare not in mr._ZH_CONDITIONAL_CONSEQUENT_MARKERS
    assert mr.is_explicit_music_cancellation(
        '我想停止播放因为无论唱什么歌都不好听'
    ) is True
    assert mr.is_explicit_music_cancellation(
        '我想停止播放如果有什么新歌再告诉我'
    ) is True


@pytest.mark.parametrize("transition", ["只是", "不过是", "不過是", "无非", "無非"])
def test_adversative_transitions_end_the_reason_scope(transition):
    """`因为音质差只是不知道是否合适` —— 理由属于 `只是` 之前那一小句，管不到后面的
    犹豫（base 是 False，第七十八轮）。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    text = f'我想停止播放因为音质差{transition}不知道是否合适'
    assert is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["如果", "假如", "要是", "万一", "倘若"])
def test_bare_jiu_ends_a_conditional_scope_but_not_a_free_choice_one(frame):
    """⚠️⚠️ 裸 `就` 只对**条件族**算后件边界。

    `如果有什么新歌就告诉我这样是否合适` 里 `就` 引出后件、前件到此为止；
    可 `无论唱什么歌都不好听` 里的 `就/都` 是任指框架的**关联词**，收了会把那一族
    的辖域提前截断——第七十七轮就是因为这个没敢收裸 `就`。

    **同一个字在两族里语法角色相反**，所以按框架词属于哪一族分流，不能一刀切。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        f'我想停止播放{frame}有什么新歌就告诉我这样是否合适'
    ) is False, frame
    # ⚠️ 反向：任指族的关联词 就/都 不能被当成边界。
    for correlative in ("都", "就"):
        assert is_explicit_music_cancellation(
            f'我想停止播放因为无论唱什么歌{correlative}不好听'
        ) is True, correlative


@pytest.mark.parametrize("verb", ["问", "問", "说", "說"])
@pytest.mark.parametrize("frame", ["不管", "无论", "無論", "如果", "即使"])
def test_a_frame_word_after_an_inquiry_verb_is_a_mention(verb, frame):
    """⚠️ 言说/探询动词后面的框架词是**被讨论的词**，不是框架：
    `问不管这个词是否合适`（base 是 False，第七十八轮）。

    ⚠️ 这一格是我第七十六轮建左邻白名单时**顺手加的**（把 `问/说` 当成了合法左邻），
    没有失败用例支撑，然后它自己造出了缺陷。已从白名单移除。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    for banned in ("问", "問", "说", "說"):
        assert banned not in mr._ZH_FRAME_LEFT_CONTEXT, banned
    text = f'我想停止播放前{verb}{frame}这个词是否合适'
    assert mr.is_explicit_music_cancellation(text) is False, text


@pytest.mark.parametrize("frame", ["如果", "要是", "无论", "無論", "不管", "即使"])
def test_the_left_bound_start_anchor_is_live(frame):
    """⚠️ `_ZH_FRAME_LEFT_BOUND` 的 `^` 分支**直接打正则**。

    端到端断言不出来：句首带条件/任指从句的句子本来就不是直接取消命令
    （`如果不好听就停止播放` 实测 base 就是 False），所以
    `test_real_frames_survive_the_unified_left_bound` 那八句里一句句首的都没有。
    上一版 docstring 却声称覆盖了句首——会让人以为这一支有回归保护（CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    hit = mr._ZH_FREE_CHOICE_FRAME_RE.search(f'{frame}不好听就停止播放')
    assert hit is not None and hit.start() == 0, frame


def test_the_left_blacklist_and_the_left_bound_do_not_overlap():
    """⚠️⚠️ **这条监控的是「统一左界包住逐词黑名单」这个关系本身。**

    第七十六轮加统一左界之后，实测逐词黑名单的 59 个字**行为上已经全部冗余**：
    清空整张表，53 个接缝词加 5 个已知句子照样全部正确处理。

    ⚠️ 那为什么不删？因为它跟这个文件里删过四次的那些「死代码」**性质不同**：
    右界表、单字 `因`/`既`、让步族右侧必需、白名单里的 `问`/`说` —— 那四个是
    **从来没有失败用例支撑**的防御（或放宽）。这 59 个字每一个当初都对应一个真缺陷，
    现在是被更宽的判据**包住**了，不是没根据；而且它零成本、方向安全。

    ⚠️ 真正的风险是**有人往允许左邻集里加字**，悄悄把某个接缝重新打开。所以这里
    钉住那个不变量：黑名单里的字**一个都不能出现在允许左邻集里**。谁往
    `_ZH_FRAME_LEFT_CONTEXT` 里加了个跟黑名单撞车的字，这条当场见红。
    """  # noqa: DOCSTRING_CJK
    from main_logic import music_requests as mr

    allowed = set(mr._ZH_FRAME_LEFT_CONTEXT)
    overlap = {
        frame: sorted(set(chars) & allowed)
        for frame, chars in mr._ZH_FRAME_LEFT_BLACKLIST.items()
        if set(chars) & allowed
    }
    assert overlap == {}, f'黑名单与允许左邻集撞车，接缝会被重新打开: {overlap}'
    # ⚠️ 顺带钉住黑名单非空——它整张被清掉的话上面那条会**空转**。
    assert sum(len(c) for c in mr._ZH_FRAME_LEFT_BLACKLIST.values()) >= 50


def test_bare_ke_is_an_adversative_boundary_but_not_inside_keyi_keneng():
    """裸 `可` 作转折：`因为音质差可我不知道是否合适` 里理由属于 `可` 之前那一小句
    （base 是 False，第八十轮）。

    ⚠️⚠️ 第一版我给它加了 `(?![以能])` 右界，docstring 里写着「**必须**带右界，
    不挡会造成少触发的回归」。**那是没验过的预测，而且是错的。**
    43 条含 `可` 的用例（可以 / 可能 / 可不可以 / 认可 / 许可 / 宁可 / 可是 /
    可+代词）跑下来，带右界、裸 `可`、以及**完全不加 `可`** 三种配置的 base 差分
    **一模一样**——那个右界零作用。已按「没有失败用例支撑的防御就是死代码」删掉。

    ⚠️ 教训：加边界前先**量风险面**，别把预测写成 docstring 里的断言口吻。
    变异当时也提示过——「去掉右界」只打红了钉子、没打红任何行为断言。
    """  # noqa: DOCSTRING_CJK
    from main_logic.music_requests import is_explicit_music_cancellation

    assert is_explicit_music_cancellation(
        '我想停止播放因为音质差可我不知道是否合适'
    ) is False
    # ⚠️ 反向：这两句 base 是 True，加 `可` 之后照旧（实测三种配置都一样）。
    assert is_explicit_music_cancellation(
        '我想停止播放因为不知道可能是不是好歌都不想听'
    ) is True
    assert is_explicit_music_cancellation(
        '我想停止播放因为无论什么歌可以停就停'
    ) is True
