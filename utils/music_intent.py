"""Shared intent checks for music catalog scoping and write authorization."""

import re

CLOUD_WRITE_ACTIONS = frozenset({"create", "add", "remove", "like", "unlike"})


def normalize_music_request(text):
    """Normalize script only for matching; never rewrite song/playlist arguments."""
    return (
        str(text or "")
        .casefold()
        .translate(
            str.maketrans(
                "網雲單歡樂這個當聽儲創刪帳號紅裡頁繼續薦稱錄進將從別幫給機",
                "网云单欢乐这个当听储创删账号红里页继续荐称录进将从别帮给机",
            )
        )
    )


def music_write_refused(text):
    text = normalize_music_request(text)
    return bool(
        re.search(
            r"不要|别|不想|不愿|不需要|不用|不必|先不|暂不|不收藏|不保存|不添加|不加入|不创建|不删除|不移除|不能|如何|怎么|怎樣|怎样|是否|要不要|想不想|"
            r"\b(?:don['’]?t|do not|not now|never|no need|without|how (?:do|can|to)|should (?:i|we)|can (?:i|we))\b|\b(?:don't|do not) want\b",
            text,
        )
    )


def music_write_action(text):
    """Recognize an affirmative library mutation, shared by planner and executor."""
    text = normalize_music_request(text)
    if music_write_refused(text):
        return None
    music = re.search(
        r"歌|曲|音乐|红心|这首|那首|网易云|\b(?:song|track|music|playlist|netease|favorites?|favourites?|liked)\b",
        text,
    )
    if not music:
        return None
    if re.search(
        r"取消.*(?:喜欢|红心)|不再喜欢|\b(?:unlike|unfavorite|unfavourite)\b|(?:remove|delete).*(?:liked|favorites?|favourites?)",
        text,
    ):
        return "unlike"
    if re.search(r"创建|新建|建一[张个]|\bcreate\b|\bmake\b.*\bplaylist\b", text):
        return "create"
    if re.search(r"移除|删掉|删除|去掉|\b(?:remove|delete)\b", text):
        return "remove"
    save = re.search(
        r"收藏|保存|存到|加到|加入|添加|放进|\b(?:save|add|favorite|favourite|like)\b",
        text,
    )
    save = save or re.search(r"喜欢(?:一下)?这首|(?:点|加|标记).{0,5}红心", text)
    if not save:
        return None
    if re.search(r"喜欢的音乐|红心|\b(?:liked songs|favorites|favourites)\b", text):
        return "like"
    if re.search(r"歌单|\bplaylist\b", text) and re.search(
        r"这首|那首|歌曲|正在听|正在播放|添加|加到|加入|存到|放进|保存到|收藏到|保存进|收藏进|\b(?:song|track|add)\b",
        text,
    ):
        return "add"
    if re.search(r"这首|那首|歌曲|正在听|正在播放|当前.*歌|\b(?:song|track)\b", text):
        return "like"
    return None
