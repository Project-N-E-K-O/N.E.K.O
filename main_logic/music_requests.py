# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared parsing and resolution for user and proactive music requests."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

MusicFetcher = Callable[..., Awaitable[dict[str, Any]]]
_RECENT_QUERY_TTL_SECONDS = 300.0
_RECENT_QUERY_LIMIT_PER_SCOPE = 20
_recent_music_queries: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class MusicRequest:
    keyword: str = ""
    song_name: str = ""
    song_artist: str = ""
    playlist_name: str = ""
    personalization_source: str = "auto"

    @property
    def strict(self) -> bool:
        return bool(
            self.song_name
            or self.song_artist
            or self.playlist_name
            or self.personalization_source != "auto"
        )

    @property
    def display_query(self) -> str:
        if self.playlist_name:
            return self.playlist_name
        if self.personalization_source == "liked":
            return "liked songs"
        if self.personalization_source == "daily":
            return "daily recommendations"
        if self.song_artist and not self.song_name and self.keyword == self.song_artist:
            return self.song_artist
        return " ".join(
            part for part in (self.song_name or self.keyword, self.song_artist) if part
        )


def _music_request_query_key(request: MusicRequest) -> str:
    if request.playlist_name:
        value = f"playlist:{request.playlist_name}"
    elif request.personalization_source != "auto":
        value = f"source:{request.personalization_source}"
    elif not request.keyword:
        value = "source:auto"
    else:
        value = request.keyword
    return " ".join(value.casefold().split())


def was_music_request_recent(scope: str, request: MusicRequest) -> bool:
    key = _music_request_query_key(request)
    if not key:
        return False
    now = time.monotonic()
    timestamp = _recent_music_queries.get((scope, key))
    return timestamp is not None and now - timestamp < _RECENT_QUERY_TTL_SECONDS


def mark_music_request_query(scope: str, request: MusicRequest) -> None:
    key = _music_request_query_key(request)
    if not key:
        return
    now = time.monotonic()
    scope_items = [
        (cache_key, timestamp)
        for cache_key, timestamp in _recent_music_queries.items()
        if cache_key[0] == scope
    ]
    for cache_key, timestamp in scope_items:
        if now - timestamp >= _RECENT_QUERY_TTL_SECONDS:
            _recent_music_queries.pop(cache_key, None)
    scope_items = [item for item in scope_items if item[0] in _recent_music_queries]
    if len(scope_items) >= _RECENT_QUERY_LIMIT_PER_SCOPE:
        _recent_music_queries.pop(min(scope_items, key=lambda item: item[1])[0], None)
    _recent_music_queries[(scope, key)] = now


def parse_music_request(value: str) -> MusicRequest:
    """Parse the controlled directives emitted by proactive chat or a tool."""
    normalized = str(value or "").strip()
    for prefix in ("playlist:", "playlist：", "歌单:", "歌单："):
        if normalized.casefold().startswith(prefix.casefold()):
            name = normalized[len(prefix) :].strip(" '\"「」『』《》")
            return MusicRequest(playlist_name=name)

    for prefix in ("song:", "song：", "歌曲:", "歌曲："):
        if normalized.casefold().startswith(prefix.casefold()):
            payload = normalized[len(prefix) :].strip(" '\"「」『』《》")
            song_name, separator, song_artist = payload.partition("|")
            song_name = song_name.strip(" '\"「」『』《》")
            song_artist = song_artist.strip(" '\"「」『』《》") if separator else ""
            keyword = " ".join(part for part in (song_name, song_artist) if part)
            return MusicRequest(
                keyword=keyword,
                song_name=song_name,
                song_artist=song_artist,
            )

    for prefix in ("source:", "source："):
        if normalized.casefold().startswith(prefix.casefold()):
            source = normalized[len(prefix) :].strip().casefold()
            aliases = {
                "liked": "liked",
                "favorites": "liked",
                "我喜欢": "liked",
                "红心": "liked",
                "daily": "daily",
                "daily recommendations": "daily",
                "日推": "daily",
                "每日推荐": "daily",
            }
            normalized_source = aliases.get(source)
            if normalized_source:
                return MusicRequest(personalization_source=normalized_source)
            logger.warning("未知音乐来源指令: %r", source)
            return MusicRequest()

    if normalized.casefold() in {"personalized", "个性化", "按喜好推荐"}:
        return MusicRequest()
    return MusicRequest(keyword=normalized)


_EN_CLAUSE_AFTER_PERIOD = re.compile(
    r"\s+(?:actually\b|never\s+mind\b|please\b|"
    r"i\s+(?:want|would\s+like)\b|can\b|could\b|would\b|play\b|"
    r"listen\b|do\s+not\b|don't\b|dont\b|stop\b|pause\b|cancel\b)",
    re.IGNORECASE,
)
_EN_CLAUSE_CONJUNCTION = re.compile(
    r"\s+(?:and|but)\s+(?="
    r"(?:(?:actually|never\s+mind)[,\s]+)?(?:please\s+)?"
    r"(?:(?:do\s+not|don't|dont)\s+(?:play|listen\s+to)\b"
    r"|(?:play|listen\s+to|stop|pause|cancel)\b))",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR_CHARS = frozenset("，,。；;！？!?")
_QUOTE_PAIRS = {
    "'": "'",
    '"': '"',
    "“": "”",
    "‘": "’",
    "《": "》",
    "〈": "〉",
    "「": "」",
    "『": "』",
    "【": "】",
}
# ── 简繁并列的复用片段 ────────────────────────────────────────────
# ⚠️ 这些正则撞的是**用户实际打出来的字**，不是界面语言。繁简是不同码位，
# 所以简体词条对繁体输入不是「匹配度低」而是一条都不中：点歌整个功能对繁中
# 用户等于不存在，而且还会误解析（「播放我的紅心歌單」曾被当成「搜索歌手
# 『我』的歌曲『紅心歌單』」）。
# 片段提在这里而不是逐条内联，是因为同一个前缀在下面出现九次，散写必漏。
_ZH_POLITE = r"(?:请|請|麻烦|麻煩)?"
_ZH_FOR_ME = r"(?:给我|給我|帮我|幫我)?"
_ZH_REQ_PREFIX = rf"{_ZH_POLITE}{_ZH_FOR_ME}(?:我)?(?:想|要)?"
_ZH_ONE_TRACK = r"(?:一首|首|点|點)?"
_ZH_SONG_NOUN = r"(?:歌|歌曲|音乐|音樂)"
_ZH_PLAYLIST_NOUN = r"(?:歌单|歌單)"
_ZH_NETEASE = r"(?:网易云|網易雲)"
_ZH_ONCE = r"(?:一下)?"
# 「算了 / 还是算了」这类改主意的引导语。⚠️ 提成常量是因为 _ZH_NEGATIVE_MUSIC 和
# _ZH_DIRECT_MUSIC_STOP **必须认同一套前缀**：前者收了它、后者没有的话，
# `算了停止播放红心歌单`（不带逗号，切不出子句）会被判成窄排除而不是取消播放
# ——两个模式各写各的前缀就会这样悄悄漂开（greptile P1）。
_ZH_CHANGED_MIND_PREFACE = r"(?:(?:算了|还是算了|還是算了)[，,\s]*)?"

# ⚠️ 礼貌前缀与点歌解析器同一套（_ZH_POLITE + _ZH_FOR_ME）。此前只允许「请/麻烦」，
# 于是 `请帮我停止播放红心歌单` 压根进不了否定分支——两侧对称的既有缺口，不是繁体
# 补齐引入的。只放宽前缀、仍然要求出现否定/停止动词，所以不会把肯定句吃进来。
_ZH_NEGATIVE_MUSIC = re.compile(
    rf"^{_ZH_CHANGED_MIND_PREFACE}{_ZH_POLITE}{_ZH_FOR_ME}(?:我)?"
    # ⚠️⚠️ 单字「别 / 別」走**单独一条、要求紧邻播放动词**的分支，不跟多字否定词
    # 共用那个 `.{0,6}` 的宽松窗口。
    #
    # 之前用 lookahead 黑名单（`别(?![人的])`）挡「别人 / 别的」，那是打地鼠：
    # 还有 別緻 / 別具一格 / 別有風味……单字后面能接的词是开放集合，黑名单堵不完。
    # 改成正向要求「别」必须直接管着一个播放动词，一次性覆盖全部这些情况：
    #   別放音樂了 / 别听我喜欢的 / 别再放了   → 命中（别 紧跟 放/听）
    #   別緻的音樂 / 別人都在聽音樂 / 別的歌…   → 不命中（别 后面不是播放动词）
    # ⚠️ 简体侧同样受益：`别致的音乐` / `别的歌播放不了吗` 在基线上都会误取消。
    r"(?:"
    r"(?:不要|不想|不听|不聽|无需|無需|停止|暂停|暫停|关掉|關掉|关闭|關閉|停掉|取消)"
    r".{0,6}(?:播放|放|播|听|聽|音乐|音樂|歌)"
    # ⚠️ 中间允许**收件人短语**（给我/帮我，即 _ZH_FOR_ME）和「再」，但结尾仍然
    # 必须是播放动词。`别给我放歌` / `別幫我播放音樂` 是明确取消，上一版收得太紧
    # 把它们挡掉了（Codex P2）。允许的是一个**闭集**（就那四个词），不是又开一个
    # 通配窗口——这正是它和之前那种黑名单的区别。
    rf"|(?:别|別)\s*(?:再)?\s*{_ZH_FOR_ME}\s*(?:再)?\s*(?:播放|放|播|听|聽)"
    r"|把(?:音乐|音樂|歌).{0,4}(?:关了|關了|关掉|關掉|停掉))"
)
_EN_NEGATIVE_MUSIC = re.compile(
    r"^(?:(?:actually|never\s*mind)[,\s]+)?"
    r"(?:(?:can|could|would)\s+you\s+(?:please\s+)?|(?:please\s+)?)"
    r"(?:(?:do\s+not|don't|dont)\s+(?:play|listen\s+to)\b"
    r"|(?:stop|pause|cancel)\b.{0,12}\b(?:music|song|tracks?|tunes?|playback|playing)\b"
    r"|(?:turn|shut)\s+(?:off\s+(?:the\s+)?(?:music|songs?|tracks?|tunes?|playback)"
    r"|(?:the\s+)?(?:music|songs?|tracks?|tunes?|playback)\s+off)\b)",
    re.IGNORECASE,
)
_EN_EXPLICIT_MUSIC_TARGET = re.compile(
    r"\b(?:music|songs?)\b",
    re.IGNORECASE,
)
_EN_DIRECT_MUSIC_STOP = re.compile(
    r"\b(?:stop|pause|cancel|turn|shut)\b",
    re.IGNORECASE,
)
_EN_LIKED_SOURCE_PATTERN = r"(?:liked|favou?rites?)(?:\s+(?:songs?|music))?"
_EN_DAILY_SOURCE_PATTERN = r"daily(?:\s+(?:recommendations?|mix|songs?|music))?"
_EN_NON_MUSIC_TARGET = re.compile(
    r"(?:(?:a|the|this|that|my|your|some)\s+)?"
    r"(?:games?|videos?|movies?|films?|shows?|podcasts?|audiobooks?|"
    r"chess|football|soccer|basketball)"
    r"|\b(?:me|us|him|her|them|it|this|that)\b"
    r"|\bwith\s+(?:me|us|him|her|them)\b",
    re.IGNORECASE,
)
# ⚠️ 复数第二人称此前整片缺失（他們/她們/我們 都在，唯独漏了 你們）——简体侧
# 同样：`帮我放一段你们说话的声音` 在基线上会去搜「歌手 一段你们说话」。
# 顺带收台湾女性用字「妳 / 妳們」。长的排前面，避免「你」先匹配掉。
_ZH_SPEECH_SUBJECT = (
    r"(?:你們|你们|妳們|妳们|我們|我们|咱們|咱们|他們|他们|她們|她们"
    r"|你|妳|我|他|她|它)(?:的)?"
)
_ZH_SPEECH_TARGET = (
    rf"(?:一段\s*)?{_ZH_SPEECH_SUBJECT}(?:说话|說話|讲话|講話)(?:的?(?:声音|聲音))?"
)
# ⚠️ 只收台湾用字「動畫」，**不要**写「动画」的日文形「動画」——日文里那是
# 极常见的普通名词，收了会把日文输入判成「非音乐目标」。
_ZH_NON_MUSIC_TARGET = re.compile(
    r"(?:(?:一个|一個|一段|一些|这个|這個|那个|那個|我的|你的|他的|她的)\s*)?"
    # ⚠️ 台湾把 video 叫「影片」而不是「視頻」——只补字形转换会漏掉最常用的那个词，
    # 于是「聽一下這個影片」落进点歌解析、去搜一首叫「這個影片」的歌。
    r"(?:视频|視頻|影片|游戏|遊戲|电影|電影|电视剧|電視劇|动画|動畫|动漫|動漫"
    r"|播客|有声书|有聲書)"
    rf"|{_ZH_SPEECH_TARGET}"
)
# 与 _EN_EXPLICIT_MUSIC_TARGET 对偶。中文侧此前没有这一条，所以
# `不要播放電影歌曲` / `不要播放這個影片的歌` 里的「電影 / 影片」会把整句判成
# 非音乐目标、吞掉一次明确取消——尽管句子明明点名了「歌」。简体侧同样如此
# （`不要播放电影歌曲` 在 main 上就不取消），两侧一起修（Codex P2）。
# ⚠️ 必须限定在**目标词紧邻其后**，不能在整句里搜。整句搜的话
# `不要播放唱歌的影片` / `不要播放有歌曲的遊戲` 会因为句子别处出现「歌」而把
# 视频/游戏目标撤销，把一次非音乐拒绝变成取消播放（Codex P2）。
# 只认「電影歌曲」「影片的歌」这种目标词自己构成的复合。
_ZH_MUSIC_NOUN_AFTER_TARGET = re.compile(r"的?(?:歌曲|歌|音乐|音樂|曲目|曲)")

_ZH_NON_MUSIC_SPEECH_REQUEST = re.compile(
    rf"{_ZH_POLITE}{_ZH_FOR_ME}(?:我)?(?:想|要)?"
    r"(?:播放|放|听|聽|想听|想聽|要听|要聽)(?:一下)?"
    rf"{_ZH_SPEECH_TARGET}"
)
# 简繁各列一遍（同形的只出现一次）。这张表决定「放輕鬆的歌」里的「輕鬆」
# 被当成曲风还是歌手名——缺繁体时会返回 song_artist="輕鬆" 去搜歌手。
_ZH_MUSIC_MOOD_OR_STYLE = {
    "安静",
    "安靜",
    "悲伤",
    "悲傷",
    "电子",
    "電子",
    "放松",
    "放鬆",
    "古典",
    "欢快",
    "歡快",
    "怀旧",
    "懷舊",
    "爵士",
    "开心",
    "開心",
    "快乐",
    "快樂",
    "浪漫",
    "民谣",
    "民謠",
    "轻松",
    "輕鬆",
    "热血",
    "熱血",
    "伤感",
    "傷感",
    "舒缓",
    "舒緩",
    "温柔",
    "溫柔",
    "摇滚",
    "搖滾",
    "治愈",
    "治癒",
}


# 句末语气词。非音乐目标判定用的是 fullmatch，所以 `這個影片吧` 会因为多一个
# 「吧」而漏判、掉进点歌解析。剥掉再判。⚠️ 这是**两侧对称的既有缺口**
# （`听一下这个视频吧` 在基线上同样会去搜歌），一并修（Codex P2）。
# 只剥纯语气词；`影片內容` 这类目标词延伸仍然漏判，属既有限制，未在本批处理。
_ZH_TRAILING_PARTICLES = re.compile(r"(?:吧|啊|呀|呢|嘛|哦|喔|吗|嗎)+$")


def _strip_request_payload(value: str) -> str:
    return value.strip(" \t\r\n'\"“”‘’《》〈〉「」『』【】")


def _split_music_request_clauses(text: str) -> list[str]:
    clauses: list[str] = []
    start = 0
    quote_end = ""
    for index, char in enumerate(text):
        embedded_apostrophe = (
            char == "'"
            and 0 < index < len(text) - 1
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
        if quote_end:
            if char == quote_end and not embedded_apostrophe:
                quote_end = ""
            continue
        if char in _QUOTE_PAIRS and not embedded_apostrophe:
            quote_end = _QUOTE_PAIRS[char]
            continue
        conjunction = (
            _EN_CLAUSE_CONJUNCTION.match(text, index)
            if char.isspace()
            else None
        )
        if conjunction:
            clause = text[start:index].strip()
            if clause:
                clauses.append(clause)
            start = conjunction.end()
            continue
        is_separator = char in _CLAUSE_SEPARATOR_CHARS
        if char == ".":
            is_separator = bool(_EN_CLAUSE_AFTER_PERIOD.match(text, index + 1))
        if not is_separator:
            continue
        clause = text[start:index].strip()
        if clause:
            clauses.append(clause)
        start = index + 1
    clause = text[start:].strip()
    if clause:
        clauses.append(clause)
    return clauses


def _parse_explicit_zh_clause(clause: str) -> MusicRequest | None:
    if not clause or _ZH_NEGATIVE_MUSIC.search(clause):
        return None
    if _ZH_NON_MUSIC_SPEECH_REQUEST.fullmatch(clause):
        return None

    if re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:只)?"
        rf"(?:来|來|放|播放|听|聽){_ZH_ONCE}{_ZH_ONE_TRACK}(?:我)?(?:的)?"
        rf"(?:红心|紅心|我喜欢|我喜歡|收藏)(?:的)?{_ZH_SONG_NOUN}?{_ZH_PLAYLIST_NOUN}?",
        clause,
    ):
        return MusicRequest(personalization_source="liked")
    if re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:只)?"
        rf"(?:来|來|放|播放|听|聽){_ZH_ONCE}{_ZH_ONE_TRACK}(?:我)?(?:的)?{_ZH_NETEASE}?(?:的)?"
        rf"(?:日推|每日推荐|每日推薦){_ZH_SONG_NOUN}?",
        clause,
    ):
        return MusicRequest(personalization_source="daily")

    playlist_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:从|從|播放|放|听|聽){_ZH_NETEASE}?(?:的)?{_ZH_PLAYLIST_NOUN}?"
        rf"[《「『【]?(.{{1,40}}?)[》」』】]?(?:这个|這個|的)?{_ZH_PLAYLIST_NOUN}?(?:里|裡|中)"
        rf"(?:随机|隨機)?(?:放|播|听|聽|来|來)?{_ZH_ONE_TRACK}(?:歌|音乐|音樂)?",
        clause,
    )
    if playlist_match:
        playlist = _strip_request_payload(playlist_match.group(1))
        if playlist.startswith("我的"):
            playlist = _strip_request_payload(playlist[2:])
        return MusicRequest(playlist_name=playlist) if playlist else MusicRequest()

    direct_playlist_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:播放|放|听|聽){_ZH_ONCE}"
        rf"(.{{1,40}}?){_ZH_PLAYLIST_NOUN}",
        clause,
    )
    if direct_playlist_match:
        playlist = _strip_request_payload(direct_playlist_match.group(1))
        if playlist.startswith("我的"):
            playlist = _strip_request_payload(playlist[2:])
        return MusicRequest(playlist_name=playlist) if playlist else MusicRequest()

    quoted_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:播放|放|听|聽|来|來){_ZH_ONCE}(?:一首|首)?"
        rf"(?:(.{{1,30}}?)的)?[《「『【](.{{1,60}}?)[》」』】]"
        r"(?:这首歌|這首歌|这首|這首|歌曲|歌)?",
        clause,
    )
    if quoted_match:
        artist = _strip_request_payload(quoted_match.group(1) or "")
        song = _strip_request_payload(quoted_match.group(2))
        return MusicRequest(
            keyword=" ".join(part for part in (song, artist) if part),
            song_name=song,
            song_artist=artist,
        )

    switch_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}"
        r"(?:换成|換成|切到|切成|改放)(?:歌曲?|曲目|音乐|音樂)\s*[:：]?\s*(.{1,60})",
        clause,
    )
    if switch_match:
        song = _strip_request_payload(switch_match.group(1))
        return MusicRequest(keyword=song, song_name=song)

    artist_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}"
        r"(?:播放|放|听|聽|来点|來點|来一首|來一首|来首|來首)(?:一下)?"
        rf"(?:一首|首)?(.{{1,40}}?)的{_ZH_SONG_NOUN}",
        clause,
    )
    if artist_match:
        artist = _strip_request_payload(artist_match.group(1))
        if artist in {"我", "你", "他", "她", "它", "咱", "咱们", "咱們", "我们", "我們", "自己"}:
            return MusicRequest()
        if artist in _ZH_MUSIC_MOOD_OR_STYLE:
            return MusicRequest(keyword=artist)
        return MusicRequest(keyword=artist, song_artist=artist)

    artist_song_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}(?:播放|放|听|聽|来一首|來一首)(?:一下)?(?:一首|首)?"
        r"(.{1,30}?)的(.{1,60})",
        clause,
    )
    if artist_song_match:
        artist = _strip_request_payload(artist_song_match.group(1))
        song = _strip_request_payload(artist_song_match.group(2))
        song = re.sub(r"(?:这首歌|這首歌|这首|這首|歌曲)$", "", song).strip()
        return MusicRequest(
            keyword=f"{song} {artist}",
            song_name=song,
            song_artist=artist,
        )

    generic_match = re.fullmatch(
        rf"{_ZH_REQ_PREFIX}"
        r"(播放一首|播放首|播放一下|播放下|播放"
        r"|放一首|放首|放一下"
        r"|听一首|聽一首|听首|聽首|听一下|聽一下"
        r"|想听|想聽|要听|要聽"
        r"|来一首|來一首|来首|來首|来点|來點)"
        r"(.{0,60})",
        clause,
    )
    if not generic_match:
        return None
    _action, payload = generic_match.groups()
    payload = _strip_request_payload(payload)
    if payload in {"", "歌", "歌曲", "音乐", "音樂", "一首歌", "首歌", "点音乐", "點音樂"}:
        return MusicRequest()
    if _ZH_NON_MUSIC_TARGET.fullmatch(_ZH_TRAILING_PARTICLES.sub("", payload)):
        return None
    named_song_match = re.fullmatch(r"(?:歌曲?|曲目)\s*[:：]\s*(.{1,60})", payload)
    if named_song_match:
        song = _strip_request_payload(named_song_match.group(1))
        return MusicRequest(keyword=song, song_name=song)
    # 「放一首 X」把 X 当歌名，「放 X」只当关键词——两组动作的语义不同，所以
    # 繁体形必须同样进这个集合，否则「聽一首晴天」会退化成关键词搜索。
    if _action in {
        "播放一首",
        "播放首",
        "放一首",
        "放首",
        "听一首",
        "聽一首",
        "听首",
        "聽首",
        "来一首",
        "來一首",
        "来首",
        "來首",
    }:
        return MusicRequest(keyword=payload, song_name=payload)
    return MusicRequest(keyword=payload)


def _parse_explicit_en_clause(clause: str) -> MusicRequest | None:
    if not clause or _EN_NEGATIVE_MUSIC.search(clause):
        return None
    normalized = clause.strip()
    request_prefix = (
        r"(?:(?:please\s+)?(?:i\s+(?:want|would like)\s+to\s+)?"
        r"|(?:can|could|would)\s+you\s+(?:please\s+)?)"
    )
    action_prefix = (
        request_prefix
        + r"(?:play|listen\s+to)\s+"
    )
    if re.fullmatch(
        action_prefix
        + rf"(?:some\s+)?(?:my\s+)?{_EN_LIKED_SOURCE_PATTERN}\s+playlist",
        normalized,
        re.IGNORECASE,
    ):
        return MusicRequest(personalization_source="liked")
    playlist_match = re.fullmatch(
        request_prefix
        + r"(?:play|listen\s+to)\s+"
        r"(?:(?:(?:a|any)\s+(?:song|track|tune)|some\s+(?:songs|music|tracks|tunes)|(?:music|songs|tracks|tunes)|(?:some|any)thing)\s+from\s+|from\s+)?"
        r"(?:my\s+)?(.{1,60}?)\s+playlist",
        normalized,
        re.IGNORECASE,
    )
    if playlist_match:
        return MusicRequest(
            playlist_name=_strip_request_payload(playlist_match.group(1))
        )
    if re.fullmatch(
        action_prefix
        +
        rf"(?:(?:(?:a|any)\s+(?:song|track|tune)|some\s+(?:songs|music|tracks|tunes)|(?:music|songs|tracks|tunes)|(?:some|any)thing)\s+from\s+|from\s+)?"
        rf"(?:some\s+)?(?:my\s+)?{_EN_LIKED_SOURCE_PATTERN}",
        normalized,
        re.IGNORECASE,
    ):
        return MusicRequest(personalization_source="liked")
    if re.fullmatch(
        action_prefix
        +
        rf"(?:(?:(?:a|any)\s+(?:song|track|tune)|some\s+(?:songs|music|tracks|tunes)|(?:music|songs|tracks|tunes)|(?:some|any)thing)\s+from\s+|from\s+)?"
        rf"(?:some\s+)?(?:my\s+)?{_EN_DAILY_SOURCE_PATTERN}",
        normalized,
        re.IGNORECASE,
    ):
        return MusicRequest(personalization_source="daily")
    match = re.fullmatch(
        action_prefix
        +
        r"(?:(?:me|us)\s+)?(?:(?:(?:a|some|any)\s+)?"
        r"(?:songs?|music|tracks?|tunes?)|(?:some|any)thing)"
        r"\s+(?:by|from)\s+(.{1,60})",
        normalized,
        re.IGNORECASE,
    )
    if match:
        artist = _strip_request_payload(match.group(1))
        return MusicRequest(keyword=artist, song_artist=artist)
    match = re.fullmatch(
        action_prefix + r"(?:(?:me|us)\s+)?(.{1,60}?)\s+by\s+(.{1,60})",
        normalized,
        re.IGNORECASE,
    )
    if match:
        song = _strip_request_payload(match.group(1))
        song_without_article = re.sub(
            r"^(?:the|a)\s+song\s+",
            "",
            song,
            count=1,
            flags=re.IGNORECASE,
        )
        if song_without_article != song:
            song = song_without_article
        elif song.startswith("song "):
            song = song[5:]
        artist = _strip_request_payload(match.group(2))
        return MusicRequest(
            keyword=f"{song} {artist}",
            song_name=song,
            song_artist=artist,
        )
    match = re.fullmatch(
        action_prefix + r"(.{1,80})",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    payload = _strip_request_payload(match.group(1))
    wrapper_match = re.fullmatch(
        r"(?:(?:me|us)\s+)?(?:"
        r"(?:a|any)\s+(?:song|track|tune)"
        r"|(?:some|any)\s+(?:songs|music|tracks|tunes)"
        r"|songs|music|tracks|tunes|something|anything)"
        r"(?:\s+for\s+(?:me|us))?",
        payload,
        re.IGNORECASE,
    )
    if wrapper_match:
        return MusicRequest()
    if _EN_NON_MUSIC_TARGET.fullmatch(payload):
        return None
    return MusicRequest(keyword=payload)


def _excluded_personalization_source(clause: str) -> str:
    """Which personalization source a negative clause is excluding, if any.

    ⚠️ Must list the same scripts as ``_ZH_NEGATIVE_MUSIC``. The two work as a
    pair: the negative pattern decides "this clause is a refusal", and this one
    decides "…but only of one source, not of playback". Leaving this side
    Simplified-only while the negative pattern accepts Traditional turns
    「別放紅心歌單，播放每日推薦」 from a narrow exclusion into a full stop.
    (收藏 and 日推 are spelled the same in both scripts.)
    """  # noqa: DOCSTRING_CJK
    folded = clause.casefold()
    if any(
        token in folded
        for token in ("红心", "紅心", "我喜欢", "我喜歡", "收藏")
    ) or re.search(
        rf"\b{_EN_LIKED_SOURCE_PATTERN}\b",
        folded,
    ):
        return "liked"
    if any(
        token in folded
        for token in ("日推", "每日推荐", "每日推薦")
    ) or re.search(
        rf"\b{_EN_DAILY_SOURCE_PATTERN}\b",
        folded,
    ):
        return "daily"
    return ""



# 中文侧的「明确停止」判据，与 _EN_DIRECT_MUSIC_STOP 对偶。
#
# ⚠️ 之前只有英文那一张，中文没有对偶物，于是**任何提到来源的中文句子都被当成
# 窄排除**——`停止播放红心歌单` 明明是「停止播放」，却被读成「以后别用红心这个
# 来源」而不取消当前播放。简体侧长期如此；繁体侧此前是因为来源词表不认繁体才
# 侥幸落到取消分支，补齐词表后就暴露出来了。
#
# 只收无歧义的停止动词，与英文那张的收词标准对齐（stop/pause/cancel/turn/shut）。
# **不收**「不要再 / 別再」：`别放红心歌单，播放每日推荐` 是真正的窄排除——用户
# 要换个来源接着听，不是要停。
# ⚠️ 必须**锚在句首**（只允许礼貌前缀），不能裸搜停止动词。
# 裸搜的话 `不要取消紅心歌單` / `别取消我喜欢的歌` 里的「取消」会被当成明确停止，
# 于是「别取消」被读反成「取消」——上一版就是这么写的，两侧都坏。
# 锚定之后：`停止播放紅心歌單` 命中（停止在句首），`不要取消紅心歌單` 不命中
# （句首是「不要」），落回窄排除，与用户实际意思一致。
# 前缀直接复用 _ZH_REQ_PREFIX，与点歌解析器同一套：只手写「请/麻烦」会漏掉
# 「帮我/给我」，于是 `请帮我停止播放红心歌单` 匹配不上、被当成窄排除而不取消
# （greptile P1）。
# ⚠️ 停止动词后面必须紧跟**播放动词**。只要求动词在句首是不够的：
# `取消收藏这首歌` 里的「取消」管的是「收藏」这个来源操作，不是播放，却会让
# _is_source_exclusion_preference 判否、把一次「取消收藏」变成停止播放
# （Codex P2）。简体侧在基线上是 False，本来就不该取消。
_ZH_DIRECT_MUSIC_STOP = re.compile(
    rf"^{_ZH_CHANGED_MIND_PREFACE}{_ZH_REQ_PREFIX}"
    r"(?:把)?(?:停止|停掉|暂停|暫停|关掉|關掉|关闭|關閉|取消)"
    # 后面接**播放动词**，或直接接一个**来源名词**（`停止紅心歌單`）。
    # ⚠️ 来源名词这一支刻意**不含「收藏」**：它在「取消收藏这首歌」里是动词
    # （把这首歌取消收藏），管的不是播放。红心/歌单/日推/我喜欢的 都是名词，
    # 没有这种歧义（Codex P2）。同样是闭集。
    r"\s*(?:(?:播放|放|播|听|聽)"
    r"|(?:红心|紅心|歌单|歌單|日推|每日推荐|每日推薦|我喜欢的|我喜歡的))"
)


def _is_source_exclusion_preference(clause: str) -> bool:
    return bool(
        _excluded_personalization_source(clause)
        and not _EN_DIRECT_MUSIC_STOP.search(clause)
        and not _ZH_DIRECT_MUSIC_STOP.search(clause)
    )


def _has_explicit_non_music_target(clause: str) -> bool:
    en_target = _EN_NON_MUSIC_TARGET.search(clause)
    bare_pronoun = (
        en_target
        and en_target.group(0).casefold()
        in {"me", "us", "him", "her", "them", "it", "this", "that"}
    )
    if en_target and (
        _EN_EXPLICIT_MUSIC_TARGET.search(clause)
        or (
            bare_pronoun
            and re.search(
                r"\b(?:tracks?|tunes?|playback)\b",
                clause,
                re.IGNORECASE,
            )
        )
    ):
        en_target = None
    zh_target = _ZH_NON_MUSIC_TARGET.search(clause)
    if zh_target and _ZH_MUSIC_NOUN_AFTER_TARGET.match(clause, zh_target.end()):
        # 「這個影片的歌」「電影歌曲」——目标词自己构成了音乐复合，不是非音乐目标。
        zh_target = None
    return bool(zh_target or en_target)


def parse_explicit_user_music_request(text: str) -> MusicRequest | None:
    """Return only high-confidence, user-initiated playback requests."""
    normalized = " ".join(str(text or "").strip().split())
    if not normalized or len(normalized) > 160:
        return None
    excluded_sources: set[str] = set()
    for clause in reversed(_split_music_request_clauses(normalized)):
        clause = clause.strip()
        if not clause:
            continue
        if _ZH_NEGATIVE_MUSIC.search(clause) or _EN_NEGATIVE_MUSIC.search(clause):
            excluded_source = _excluded_personalization_source(clause)
            if excluded_source:
                excluded_sources.add(excluded_source)
                continue
            if _has_explicit_non_music_target(clause):
                continue
            return None
        request = _parse_explicit_zh_clause(clause) or _parse_explicit_en_clause(clause)
        if request is not None:
            if request.personalization_source in excluded_sources:
                continue
            if (
                excluded_sources
                and request.personalization_source == "auto"
                and not (
                    request.keyword
                    or request.playlist_name
                    or request.song_name
                    or request.song_artist
                )
            ):
                continue
            return request
    return None


def is_explicit_music_cancellation(text: str) -> bool:
    """Return whether the utterance contains a direct music cancellation."""
    normalized = " ".join(str(text or "").strip().split())
    if not normalized or len(normalized) > 160:
        return False
    return any(
        (
            _ZH_NEGATIVE_MUSIC.search(clause.strip())
            or _EN_NEGATIVE_MUSIC.search(clause.strip())
        )
        and not _is_source_exclusion_preference(clause.strip())
        and not _has_explicit_non_music_target(clause.strip())
        for clause in _split_music_request_clauses(normalized)
        if clause.strip()
    )


async def fetch_music_request(
    request: MusicRequest,
    *,
    limit: int = 5,
    source_locale: str | None = None,
    fetcher: MusicFetcher | None = None,
    allow_keyword_fallback: bool = False,
    include_failure: bool = False,
    bypass_recommendation_dedupe: bool = False,
) -> dict[str, Any] | None:
    """Resolve a request, falling back only for non-strict keyword searches."""
    if fetcher is None:
        from utils.music_crawlers import fetch_music_content

        fetcher = fetch_music_content

    async def fetch(keyword: str) -> dict[str, Any]:
        try:
            return await fetcher(
                keyword=keyword,
                limit=limit,
                source_locale=source_locale,
                personalized=True,
                playlist_name=request.playlist_name,
                personalization_source=request.personalization_source,
                requested_song=request.song_name,
                requested_artist=request.song_artist,
                bypass_recommendation_dedupe=bypass_recommendation_dedupe,
            )
        except Exception as exc:
            logger.warning("音乐请求获取失败: %s", exc)
            return {
                "success": False,
                "error_code": "upstream_error",
                "error": "Music provider request failed",
                "data": [],
            }

    result = await fetch(request.keyword)
    if result and result.get("success"):
        return result
    if request.strict or not request.keyword or not allow_keyword_fallback:
        return result if include_failure else None

    fallback = await fetch("")
    if fallback and fallback.get("success"):
        return fallback
    return fallback if include_failure else None
