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
# ⚠️ `(?:想|要)` 必须带 `(?!不)`：汉语的 A-not-A 疑问句「要不要X / 想不想X」
# 会被这个可选前缀吃掉第一个 要/想，剩下的「不要 / 不想」正好落进否定动词表，
# 于是 `要不要停止播放`（用户在自问）被判成「停止播放」这个命令。
# ⚠️ 这条是本 PR 唯一把**简体侧也弄坏**的回归（base 两侧都是 False）。
_ZH_REQ_PREFIX = rf"{_ZH_POLITE}{_ZH_FOR_ME}(?:我)?(?:(?:想|要)(?!不))?"
_ZH_ONE_TRACK = r"(?:一首|首|点|點)?"
_ZH_SONG_NOUN = r"(?:歌|歌曲|音乐|音樂)"
# ⚠️ 台湾说「播放清單」、大陆也说「播放列表」。缺了它们，
# `聽一下我的健身播放清單` 会去搜歌手「我」的歌「健身播放清單」。
# 这几个词在 _ZH_PLAYBACK_COMPOUND_NOUN 里已经作为「播放不是动词」的证据枚举过一次了。
_ZH_PLAYLIST_NOUN = r"(?:播放清單|播放清单|播放列表|歌单|歌單)"
_ZH_NETEASE = r"(?:网易云|網易雲)"
_ZH_ONCE = r"(?:一下)?"
# 「算了 / 还是算了」这类改主意的引导语。
#
# ⚠️⚠️ _ZH_NEGATIVE_MUSIC 和 _ZH_DIRECT_MUSIC_STOP **必须逐字用同一套前缀**：
# `{_ZH_CHANGED_MIND_PREFACE}{_ZH_REQ_PREFIX}`。它们作用在同一个子句上、判的是
# 同一件事的两个方面（是不是拒绝 / 是不是停播放），任何一格不一致都会让整句
# 静默换类。这个坑连着踩了两次：先是引导语只有前者收（`算了停止播放红心歌单`
# 被判成窄排除），修完又发现后者多一个 `(?:想|要)?`（`我想停止播放紅心歌單`
# 反过来被忽略）——第一次的守卫只断言了引导语常量，没覆盖整个前缀，所以没抓住。
# 现在两者都写成上面那个组合，守卫也改成断言完整前缀。
_ZH_CHANGED_MIND_PREFACE = r"(?:(?:算了|还是算了|還是算了)[，,\s]*)?"

# ⚠️ 礼貌前缀与点歌解析器同一套（_ZH_POLITE + _ZH_FOR_ME）。此前只允许「请/麻烦」，
# 于是 `请帮我停止播放红心歌单` 压根进不了否定分支——两侧对称的既有缺口，不是繁体
# 补齐引入的。只放宽前缀、仍然要求出现否定/停止动词，所以不会把肯定句吃进来。
# ⚠️ 疑问句不是命令。本 PR 把 _ZH_NEGATIVE_MUSIC / _ZH_DIRECT_MUSIC_STOP 的前缀
# 换成了共享的 _ZH_REQ_PREFIX，顺带让它们认得两种以前不认的开头：收件人短语
# （帮我/给我）和意图词（我想/我要）。认得开头之后，`請幫我停止播放嗎？`
# `我要停止播放嗎？` 这类「用户在自问要不要停」就被判成了命令（base 简繁都是
# False，Codex P2）。
#
# ⚠️ 守卫**必须要求那两种前缀真的出现**，不能改成「凡是以疑问语气词结尾就不算
# 命令」。`停止播放吗` / `请停止播放吗` / `我停止播放吗` 在基线上都是 True，
# 一刀切会把简体用户既有的行为改掉——那是另一个方向的回归。
#
# ⚠️⚠️ 跟前缀一样，这道守卫必须**逐字同时**出现在 _ZH_NEGATIVE_MUSIC 和
# _ZH_DIRECT_MUSIC_STOP 上。只加一边会让整句静默换类（见下面那条 lockstep 注释）。
_ZH_PREFIXED_QUESTION_GUARD = (
    rf"(?!{_ZH_POLITE}(?:(?:给我|給我|帮我|幫我)|(?:我)?(?:想|要))"
    r"[^。！？!?]*[吗嗎呢]\s*[？?]?\s*$)"
)
# ⚠️ 逗号也要排除：这条作用在**未切分**的整句上，允许跨子句的话，
# `我想先听一下，别播放音乐了，好吗？` 会被整句否掉——里面那个明确的取消子句
# 就丢了（base 是 True）。限定在单子句内，多子句照常走切分。
# ⚠️ **裸问号**（`我想停止播放？`，没有语气词）挡不进上面那条正则——
# `_split_music_request_clauses` 会先把句末标点剥掉，正则拿到的子句是
# 「我想停止播放」，看不到那个问号。所以这一条只能放在入口判，作用在**未切分**
# 的原文上。两条机制互补：语气词在正则里（语气词不会被切分剥掉），裸问号在这里。
_ZH_BARE_QUESTION_UTTERANCE = re.compile(
    rf"^{_ZH_POLITE}(?:(?:给我|給我|帮我|幫我)|(?:我)?(?:想|要))[^。！？!?，,、；;]*[？?]\s*$"
)
# ⚠️ 停止动词与**音乐名词**之间的闭集窗口。
#
# 名词尾（音乐/音樂/歌）不像动词尾那样自带播放义：`.{0,6}` 里塞进任何一个别的
# 动词，否定词就改嫁给它了——`停止討論音樂` / `不要提音樂了` / `取消音樂節的行程`
# 全被判成取消播放，然后真的把用户正在放的歌停掉。
#
# 「能改嫁的动词」是开集（讨论/研究/搜索/推荐/写/唱/学/分析/翻译/下载…堵不完），
# 所以枚举**补集**：合法出现在「停止 __ 音乐」里的成分只有限定词 / 来源名 /
# 体貌补语 / 副词 / 收件人短语 / 结构助词，这一侧可以列干净——而且这个文件里
# 大部分已经列过一遍了（见 _ZH_DIRECT_MUSIC_STOP 的限定词闭集与 _ZH_STOP_SOURCE_NOUN）。
#
# ⚠️ 动词尾那一支**保持 `.{0,6}` 不动**，爆炸半径就锁在名词尾这一支。
# ⚠️ ⚠️ 闭集里**必须含 `收藏` 和整组来源名**。`取消收藏这首歌` 要先被
# _ZH_NEGATIVE_MUSIC 命中、再由 _is_source_exclusion_preference 判成窄排除；
# 漏了它们，`取消我喜歡的歌` / `停止紅心歌單的音樂` / `停止這個紅心歌單吧` 会
# 当场翻 False，而且是「结果错了但先在前提上错」那种不好查的失败。
# ⚠️ 用有界 `{0,8}` 而不是 `*`：分支间有公共前缀，无界重复失配时会指数回溯。
_ZH_MUSIC_NOUN_MODIFIER = (
    r"(?:"
    r"我的|你的|妳的|您的|他的|她的|它的|牠的|我们的|我們的|你们的|你們的|他们的|他們的|她们的|她們的|这个|這個|那个|那個|这些|這些|那些|当前的|當前的"
    r"|红心|紅心|日推|每日推荐|每日推薦|我喜欢的|我喜歡的|我喜欢|我喜歡|收藏"
    r"|歌单|歌單|歌曲|音乐|音樂|曲"
    # ⚠️ 量词/选择词也要收：`停止每首歌` / `停止整首歌` / `停止下一首歌` /
    # `停止上一首歌` 在基线上都是 True，闭集漏了 每/整/上/下/一 就把它们打成 False。
    r"|每|整|上|下|一|首|下一|上一|這一|这一|那一"
    r"|一下|一會兒|一会儿|一會|一会|掉|了|再|繼續|继续|一直"
    r"|現在|现在|馬上|马上|立刻|剛才|刚才|剛剛|刚刚"
    r"|給我|给我|幫我|帮我"
        # ⚠️ 服务名和时间限定词也要收：`停止网易云音乐` / `停止目前的音乐`
    # 在基线上是 True，闭集漏了就把它们打成 False。
    r"|网易云|網易雲|酷狗|酷我|[Qq][Qq]|蘋果|苹果|目前的|目前|當前|当前"
    r"|背景|所有|任何|全部|这|這|那|个|個|些|首|的"
    r"){0,8}"
)
# ⚠️⚠️ 单字播放动词（放 / 播 / 听 / 聽）后面的**词汇化动词复合第二字**黑名单。
#
# 要枚举的既不是「别 后面能跟什么」（开集，更早的版本栽在这里），也不是
# 「播放动词后面能跟什么」（歌名/歌手，同样开集，上一版栽在这里），而是
# 「以 放/聽/播 为首的**词汇化动词复合**的第二个字」——词汇化复合是词典
# 条目而不是自由组合，这一侧封闭、可以列干净。
#
# 收一个字进黑名单必须同时满足三条，缺一不收：
#   (a) 放X / 聽X / 播X 是词典里的**双字词**。放鹽 / 放糖 / 放油 那种自由动宾
#       不收——宾语是开集，收它等于把黑名单重新打开成打地鼠。
#   (b)「别 + 放X」在**对助手说话**这个场景里是自然的祈使否定。所以不收
#       聽見 / 聽懂 / 聽清 这类结果补语（「别听懂」不成话）。
#   (c) X 不是高频歌名首字。晴 / 歌 / 音 / 生 / 平 / 涼 / 光 / 走 / 倒 / 好 /
#       久 / 風 / 水 / 牛 一律**不收**，宁可漏掉「别放凉了」也要保住
#       《晴天》《涼涼》《平凡之路》《生日快樂》《光年之外》《風箏》《水星記》。
#
# ⚠️ 三张表**按动词分开**，不合并。分开之后撞上冲突的用户换个动词就能绕过去
# ——`别放心雨` 拦，`别听心雨` / `别播心雨` / `别播放心雨` 都放行。
# ⚠️ 双字动词「播放」无歧义，**不挂任何后视**。歧义只出现在单字动词上。
# ⚠️ 简繁必须成对列（棄弃 / 鬆松 / 過过…）。漏一侧就是繁体用户单边失效，
#    这个文件已经因为字符类只有简体「别」栽过一次。
# 播放动词前面能塞的**副词/时间词**闭集。两条否定分支共用，避免漂开。
_ZH_PLAYBACK_ADVERB = (
    r"\s*(?:再|又|還|还|繼續|继续|一直|老是|總是|总是"
    r"|現在|现在|馬上|马上|立刻|立即|待會|待会|等下|等一下|先)*\s*"
)
_ZH_NON_PLAYBACK_AFTER_FANG = (
    # 结果 / 趋向补语：放下 放開 放在 放到 放上 放入 放進 放出 放回 放著
    "下開开在到上入進进出回著着"
    # 评审点名的那批：放棄 放鬆 放心 放手 放過
    "棄弃鬆松心手過过"
    # 其余词汇化双字动词
    "大空慢任縱纵肆屁學学假血逐牧貸贷映射膽胆置低"
    "火電电話话緩缓寬宽軟软"
    # 放鴿子（爽约）——三字惯用语，高频到必须单收
    "鴿鸽"
)
_ZH_NON_PLAYBACK_AFTER_BO = "種种報报出撒弄遷迁映"
# ⚠️ 「一」收进来是为了挡 `別聽一下X`：`別聽一下老師的意見` / `別聽一下他的解釋`
# 不是取消播放。代价是 `別聽一下這首歌` 也不再算取消——它在基线上本来就是 False，
# 而且「聽一下 后面能跟什么」是开集，正向枚举做不到。简体 base 是 True，一起修了。
_ZH_NON_PLAYBACK_AFTER_TING = "信從从話话命憑凭勸劝取任著着膩腻煩烦夠够一"
# ⚠️ 人称宾语（別聽他的）只对 聽 有效，而且**绝不能含「我」**：
# `别听我喜欢的` 必须先命中 _ZH_NEGATIVE_MUSIC、再由 _excluded_personalization_source
# 判成窄范围排除。把「我」收进来，窄排除会在**前提**上就失效。
_ZH_HEARSAY_PRONOUN = r"(?:他|她|它|牠|你|妳|您)(?:们|們)?"

_ZH_NEGATIVE_MUSIC = re.compile(
    rf"^{_ZH_PREFIXED_QUESTION_GUARD}{_ZH_CHANGED_MIND_PREFACE}{_ZH_REQ_PREFIX}"
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
    # ⚠️ 单字播放动词在这一支同样有歧义，三张表要一起挂——只挂在「别」那一支
    # 是不够的：`不要聽信謠言` / `無需聽從他的安排` / `不想聽取意見` 会走这里。
    # （简体侧 base 就是 True，这条顺带把简体一起修了。）
    rf"(?:.{{0,6}}(?:播放"
    rf"|[听聽](?![{_ZH_NON_PLAYBACK_AFTER_TING}])"
    rf"|放(?![{_ZH_NON_PLAYBACK_AFTER_FANG}])"
    rf"|播(?![{_ZH_NON_PLAYBACK_AFTER_BO}]))"
    # ⚠️ 名词尾也要**右边界**。音樂/歌 可以是更长复合词的词头：
    # `取消音樂節的行程` / `停止音樂課` / `不要音樂理論` 全被判成取消播放
    # （简体 base 同样错）。「音樂X 能组成什么词」是开集，所以正向要求边界。
    # ⚠️ 音乐名词自身的后缀（歌单/歌曲/曲目）要先吃完再判边界，否则
    # `停止這個紅心歌單` 里 `歌` 匹配后卡在 `單` 上，整条判据失效。
    rf"|{_ZH_MUSIC_NOUN_MODIFIER}(?:音乐|音樂|歌)(?:单|單|曲|目)*"
    # 吗/嗎 也要收——`关掉音乐吗` 在基线上是 True，不能被这道边界改掉。
    r"(?:了|吧|啊|呀|呢|嘛|喔|哦|吗|嗎)*\s*(?=$|[，,。！!？?]))"
    # ⚠️ 中间允许**收件人短语**（给我/帮我，即 _ZH_FOR_ME）和「再」，但结尾仍然
    # 必须是播放动词。`别给我放歌` / `別幫我播放音樂` 是明确取消，上一版收得太紧
    # 把它们挡掉了（Codex P2）。允许的是一个**闭集**（就那四个词），不是又开一个
    # 通配窗口——这正是它和之前那种黑名单的区别。
    # ⚠️⚠️ 光要求「别」紧邻播放动词是**不够**的，上一版那条注释（说这样能
    # 「一次性覆盖全部情况」）是错的：它只堵住了「別 后面不是播放动词」那一侧，
    # 而 放 / 聽 / 播 本身就是高频非播放义动词的头——別放棄 / 別放心上 / 別放手 /
    # 別放過我 / 別放鬆 / 別聽他的 / 別聽信 / 別播種 全部会被判成取消播放，
    # 而「別放棄」是日常高频词。繁体侧在基线上是**全 False**（base 的字符类里
    # 只有简体「别」），等于我把这一整类假阳性搬了进来。
    #
    # ⚠️⚠️ 上一版的补法（**正向**闭集后视：播放动词后必须紧跟句末 / 语气词 /
    # 标点 / 音乐名词）方向反了。它枚举的是「播放动词后面能跟什么」，而那一侧跟的
    # 就是歌名和歌手——同样是开集，于是把这个功能**最主要的用法**整片打死：
    # `别放晴天` / `别听周杰伦` / `别播七里香` 全判 False（简体侧 base 是 True，
    # Codex P2）。拿一个假阳性换来一整类假阴性，是一笔坏交易。
    #
    # 现在枚举的是真正封闭的那一侧（见下面 _ZH_NON_PLAYBACK_AFTER_*）：
    # 以 放/聽/播 为首的**词汇化动词复合**的第二个字。三张表按动词分开挂。
        # ⚠️ 「别」和播放动词之间不止能塞「再」。`别继续播放` / `别现在播放` /
    # `别马上放晴天` 在基线上都是 True，只允许「再」把它们全打成 False。
    # 用的是与名词尾那一支**同一套**副词闭集（_ZH_PLAYBACK_ADVERB），
    # 两处漂开就会出现「同一句话两条判据给不同答案」。
rf"|(?:别|別){_ZH_PLAYBACK_ADVERB}{_ZH_FOR_ME}{_ZH_PLAYBACK_ADVERB}(?:播放"
    # 「别听他的」不是取消播放，「别听他的歌」是——救援分支必须排在黑名单前面。
    rf"|[听聽](?={_ZH_HEARSAY_PRONOUN}(?:的)?(?:歌|曲|音乐|音樂|歌单|歌單))"
    rf"|[听聽](?![{_ZH_NON_PLAYBACK_AFTER_TING}]|{_ZH_HEARSAY_PRONOUN})"
    # 第二条后视单收「放我/你/他鸽子」——「我」不能进字符类（见上面那条警告）。
    rf"|放(?![{_ZH_NON_PLAYBACK_AFTER_FANG}])(?!(?:我|你|他|她)(?:们|們)?[鸽鴿])"
    rf"|播(?![{_ZH_NON_PLAYBACK_AFTER_BO}]))"
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
    # ⚠️ 人称是封闭词类，简繁都要列全（跟歌手守卫那张表同一个道理）。漏了敬语
    # 「您」，`聽一下您說話的聲音` 会变成搜歌手「您說話」的歌「聲音」。
    r"(?:你們|你们|妳們|妳们|我們|我们|咱們|咱们|他們|他们|她們|她们"
    r"|你|妳|您|我|他|她|它|牠)(?:的)?"
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
# ⚠️ 目标词和音乐名词之间要允许**方位词**。「不要播放影片中的歌」拒的是
# **音乐**（音乐的来源是视频），只认紧邻的「的」会让「影片」仍被当成非音乐目标，
# 把明确取消压掉。
#
# ⚠️ 枚举的是**方位词**，不是「中的 / 裡的」这类连接短语。连接短语是开集
# （裡面的 / 當中的 / 之中的 / 內的 / 上的…列不完，我第一版只收了四个，实测漏
# 八条）；方位词是汉语里的**封闭词类**，简繁字形一起列全就是下面这个字符集，
# 并排两个词素（當中 / 之中 / 裡面 / 裏頭）也仍在集合内，所以用 `[...]*`。
#
# ⚠️ 也不能退成「目标词之后整片搜音乐名词」——`不要播放遊戲了我要聽歌` /
# `不要播放這個影片裡唱歌的人` 会因为后半句出现「歌」而把目标撤销（实测这个
# 方案在对抗组 10/10 全错）。方位词必须**连续**，中间夹一个非方位词立刻失配。
#
# ⚠️ 顺带修掉简体侧的既有错：`不要播放视频中的歌` 在基线上就是 False。
_ZH_MUSIC_NOUN_AFTER_TARGET = re.compile(
    r"[中裡裏里內内上下前後后旁間间邊边外面頭头當当之]*的?"
    r"(?:歌曲|歌|音乐|音樂|曲目|曲)"
)

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
        r"(?:播放|放|听|聽|来点(?![评击赞名菜头])|來點(?![評擊贊讚名菜頭])"
        r"|来一首|來一首|来首|來首)(?:一下)?"
        rf"(?:一首|首)?(.{{1,40}}?)的{_ZH_SONG_NOUN}",
        clause,
    )
    if artist_match:
        artist = _strip_request_payload(artist_match.group(1))
        # ⚠️ 人称是封闭词类，简繁都要列全。漏了繁体的 妳 / 你們 / 妳們 / 您，
        # `來一首妳的歌` 会去搜一个名叫「妳」的歌手（Codex P2，base 返回 None）。
        if artist in {
            "我", "你", "妳", "您", "他", "她", "它", "牠", "咱", "自己",
            "咱们", "咱們", "我们", "我們", "你们", "你們", "妳們",
            "他们", "他們", "她们", "她們",
        }:
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
        # ⚠️ 「来点 / 來點」是这组里最短也最歧义的动作：「点」还能当别的动词的头
        # （点评 / 点击 / 点赞 / 点名 / 点菜）。`來點評一下這張卡` 会被切成
        # 「來點」+「評一下這張卡」拿去搜歌（Codex P2）。加否定 lookahead 挡掉。
        # ⚠️ 这个集合**不敢说穷尽**——「点」的搭配是开放的。但这是两字动作词的
        # 固有歧义、不是繁体补齐带来的：简体 `来点评一下这张卡` 在基线上同样会
        # 去搜歌，这条 lookahead 把两侧一起修。
        # ⚠️ 台湾正字是「點讚」不是「點贊」（讚=称赞，贊=赞助，两字不通用）。
    # 上一版按字形机械转换写成 贊，于是简体 `来点赞吧` 挡住了、繁体 `來點讚吧`
    # 照样去搜歌——这正是「字形对应不是双射」那类错误（对抗扫描）。
    r"|来一首|來一首|来首|來首|来点(?![评击赞名菜头])|來點(?![評擊贊讚名菜頭]))"
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
# 停止对象短语：来源名 / 音乐名词，可以用「的」串起来（紅心歌單的音樂）。
_ZH_STOP_SOURCE_NOUN = r"(?:红心|紅心|日推|每日推荐|每日推薦|我喜欢的|我喜歡的)"
_ZH_STOP_MUSIC_NOUN = r"(?:歌单|歌單|歌曲|音乐|音樂|歌|曲)"
# ⚠️ 收尾必须是**子句边界**，而且要先把整个短语吃完再判。
#
# 上一版在来源名后面挂了个单点后视（「后面得是句末/语气词/标点/音乐名词」）。
# 那样写挡得住 `取消這個歌單的收藏`，却挡不住 `取消紅心歌單的收藏`：`紅心` 先
# 匹配上，紧跟的 `歌單` 正好满足后视，尾巴 `的收藏` 照样被吞。同族还有
# `取消我喜歡的歌的收藏` / `取消日推歌單的收藏` / `取消紅心音樂的收藏`。
#
# 「在某一个点上做后视」和「把整个短语消费完再要求边界」是两件事——短语可以
# 有任意多节，逐点判永远漏掉最后一节后面的东西。
_ZH_STOP_TARGET_PHRASE = (
    rf"(?:{_ZH_STOP_SOURCE_NOUN}|{_ZH_STOP_MUSIC_NOUN})"
    rf"(?:的?(?:{_ZH_STOP_SOURCE_NOUN}|{_ZH_STOP_MUSIC_NOUN}))*"
    r"(?:了|吧|啊|呀|呢|嘛|喔|哦)*\s*(?=$|[，,。！!？?])"
)
# ⚠️ 「播放清單 / 播放列表」是**名词**，里面的「播放」不是动词。停止动词后面
# 直接跟它时，`取消播放` 这个前缀会先吃掉「播放」二字，把真正的中心语「收藏」
# 整段忽略——一次「取消歌单收藏」于是变成停止播放（base 简繁都是 False）。
#
# ⚠️ 守卫**只在两个闭集同时出现时**才开火，所以既没有把播放动词那一支整体
# 收紧（`停止播放<任意歌名>` 照常命中——「播放后面能跟什么」是开集，试图收紧
# 它的方案实测把 `停止播放清單裡的紅心歌` 这类 base=True 的句子打成了 False），
# 也没有一刀切拒绝「…的收藏」后缀（那会误杀 `停止播放我的收藏` 这种真停止）。
#
# 左侧闭集：playlist/queue 在中文产品词汇里就这几种写法，是术语表不是自由文本。
# 右侧闭集：这条规则只在 _excluded_personalization_source 命中时才被咨询，
# 那张词表里只有「收藏」是操作词（红心/日推/我喜欢的 全是纯名词），势为 1。
_ZH_PLAYBACK_COMPOUND_NOUN = r"(?:播放(?:清單|清单|列表|佇列|队列|隊列))"
_ZH_STOP_MANAGEMENT_TAIL = (
    r"(?:的\s*)?收藏(?:[了吧啊呀呢嘛喔哦])*\s*(?=$|[，,。！!？?])"
)
_ZH_DIRECT_MUSIC_STOP = re.compile(
    rf"^{_ZH_PREFIXED_QUESTION_GUARD}{_ZH_CHANGED_MIND_PREFACE}{_ZH_REQ_PREFIX}"
    r"(?:把)?(?:停止|停掉|暂停|暫停|关掉|關掉|关闭|關閉|取消)"
    # 后面接**播放动词**，或直接接一个**来源名词**（`停止紅心歌單`）。
    # ⚠️ 来源名词这一支刻意**不含「收藏」**：它在「取消收藏这首歌」里是动词
    # （把这首歌取消收藏），管的不是播放。红心/歌单/日推/我喜欢的 都是名词，
    # 没有这种歧义（Codex P2）。同样是闭集。
    rf"\s*(?:(?!{_ZH_PLAYBACK_COMPOUND_NOUN}[^。！？!?，,]*{_ZH_STOP_MANAGEMENT_TAIL})"
    r"(?:播放|放|播|听|聽)"
    # 来源名前面可以有所有格「我的」：`停止我的紅心歌單`（Codex P2）。
    # 来源名前可以有限定词。闭集，逐个列出（Codex / 对抗扫描）。
        # ⚠️ 所有格要列全人称。只有 我的/你的 时，`停止他的紅心歌單` /
    # `停止我們的紅心歌單` / `停止您的紅心歌單` 全判 False（繁体 base 是 True）。
    # 人称是**封闭词类**，一次列干净；而且这张表必须与 _ZH_MUSIC_NOUN_MODIFIER
    # 里那份保持一致，否则两条判据会对同一句话给出不同答案。
    r"|(?:我的|你的|妳的|您的|他的|她的|它的|牠的|我们的|我們的|你们的|你們的|他们的|他們的|她们的|她們的|这个|這個|那个|那個|这些|這些|那些|当前的|當前的|一下)?"
    # ⚠️ 来源名词必须是**完整的**停止对象。不加这个后视，`取消這個歌單的收藏`
    # 里的「取消這個歌單」会先匹配上、把尾巴「的收藏」整个忽略，于是一次
    # 「取消收藏」被当成停止播放（Codex P2，简繁在基线上都是 False）。
    # 短语吃完之后必须落在子句边界上。`停止紅心歌單的音樂` 能一路吃到句末所以
    # 取消；`取消這個歌單的收藏` 吃到 `歌單` 就卡住（`收藏` 不在闭集里），
    # 边界判定失败，于是落回窄排除。
    rf"{_ZH_STOP_TARGET_PHRASE})"
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
    # ⚠️ 只看第一个匹配是不够的。`不要播放電影歌曲的影片` 里先撞上的是「電影」，
    # 它确实只是「電影歌曲」这个音乐复合词的一半——但句子后面还有一个真的
    # 非音乐目标「影片」。上一版撞上复合词就直接丢弃、不再往后扫，于是整句
    # 退化成取消音乐，把用户「别放视频」听成了「别放音乐」（Codex P2；简体
    # `不要播放电影歌曲的视频` 在基线上是 False）。
    zh_target = None
    for candidate in _ZH_NON_MUSIC_TARGET.finditer(clause):
        if _ZH_MUSIC_NOUN_AFTER_TARGET.match(clause, candidate.end()):
            # 「這個影片的歌」「電影歌曲」——目标词自己构成了音乐复合词。
            continue
        zh_target = candidate
        break
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
    # ⚠️ 只对**本 PR 新放行的前缀**（帮我/给我/我想/我要）生效，跟正则里那条
    # 语气词守卫同一个口径——`停止播放？` / `请停止播放？` 在基线上就是 True，
    # 一刀切会改简体既有行为。
    if _ZH_BARE_QUESTION_UTTERANCE.match(normalized):
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
