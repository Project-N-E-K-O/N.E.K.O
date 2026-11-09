"""Expose only the cloud-music operation needed by this assessment turn."""

import re
from copy import deepcopy
from utils.music_capability_context import requests_local_music

_DETAILS = {
    "account": ("查询网易云登录账号昵称、ID及会员类型。", []),
    "playlists": (
        "分页列出网易云账号歌单，kind区分created本人创建/subscribed收藏他人/all。",
        ["offset", "limit", "kind"],
    ),
    "tracks": (
        "查看指定云端歌单的曲目；playlist填查询结果的精确名称或ID。",
        ["playlist", "offset", "limit"],
    ),
    "play": (
        "播放指定云端歌单；playlist填精确名称或ID，liked=喜欢的音乐，daily=每日推荐。保留歌单顺序。",
        ["playlist", "offset"],
    ),
    "liked": ("分页读取账号喜欢的音乐。", ["offset", "limit"]),
    "daily": ("分页读取账号每日推荐歌曲。", ["offset", "limit"]),
    "artists": ("分页读取账号收藏的歌手。", ["offset", "limit"]),
    "create": (
        "按用户要求新建云端歌单；playlist是新名称，privacy默认true私人。",
        ["playlist", "privacy"],
    ),
    "add": (
        "将明确歌曲添加到本人创建的云端歌单；playlist精确名称或ID；song填歌名 歌手、ID或current；明确要求整组才用queue。",
        ["playlist", "song"],
    ),
    "remove": (
        "按明确请求从本人云端歌单移除歌曲；playlist精确名称或ID；song填歌名 歌手、ID或current。",
        ["playlist", "song"],
    ),
    "like": (
        "按明确请求给歌曲加红心，保存到网易云喜欢的音乐；song填歌名 歌手、ID或current。",
        ["song"],
    ),
    "unlike": (
        "按明确请求取消网易云歌曲红心；song填歌名 歌手、ID或current。",
        ["song"],
    ),
}


def cloud_music_actions(text, context=""):
    if requests_local_music(text):
        return []
    if context and re.search(r"下一页|上一页|继续列", text):
        for line in reversed(context.splitlines()):
            if line.startswith("user:") and line[5:].strip() != text.strip():
                previous = cloud_music_actions(line[5:].strip())
                if previous and previous[0] in {
                    "playlists",
                    "tracks",
                    "liked",
                    "artists",
                    "daily",
                }:
                    return previous
                # Do not page an older music request across a new unrelated task.
                break
    # Song saves default to the account, even without the word NetEase.
    save = bool(re.search(r"收藏|保存|存到|加到|加入|添加|放进", text))
    music_save = save and bool(
        re.search(r"这首|那首|歌曲|歌单|正在听|正在播放|当前.*歌", text)
    )
    cloud = music_save or bool(
        re.search(
            r"网易云|云端.*(?:歌|音乐)|(?:歌|音乐).*云端|我(?:的|收藏|创建|建).*歌单|收藏的歌单|自建歌单|喜欢的音乐|日推|每日推荐|收藏.*歌手|红心",
            text,
        )
    )
    if not cloud and re.search(
        r"下一页|上一页|继续列|这张歌单|这个歌单|这首.*收藏|收藏.*这首", text
    ):
        cloud = bool(re.search(r"netease_cloud|cloud_music|网易云.*歌单", context))
    if not cloud:
        return []
    if re.search(r"搜|找", text) and not re.search(
        r"我的|我收藏|我创建|云端|账号|自己的|喜欢的音乐|日推", text
    ):
        return []  # Public playlist/song search keeps its existing provider route.
    if re.search(r"取消.*(喜欢|红心)|不再喜欢", text):
        return ["unlike"]
    if re.search(r"创建|新建|建一[张个]", text):
        return ["create"]
    if re.search(r"移除|删掉|删除|去掉", text):
        return ["remove"]
    if "喜欢的音乐" in text and re.search(r"加到|加入|收藏|保存|存到", text):
        return ["like"]
    if (
        save
        and re.search(r"歌单", text)
        and re.search(
            r"添加|加到|加入|存到|放进|保存到|收藏到|保存进|收藏进|这首|那首|正在听|正在播放",
            text,
        )
    ):
        return ["add"]
    if re.search(r"添加|加到|加入|存到|放进|保存到", text):
        return ["add"]
    if re.search(r"红心|(?:收藏|喜欢)(?:一下)?这首", text) or (
        music_save and not re.search(r"歌单", text)
    ):
        return ["like"]
    if re.search(r"账号|昵称|会员|登录|个人资料", text):
        return ["account"]
    if re.search(r"收藏.*歌手", text):
        return ["artists"]
    if re.search(r"歌单|喜欢的音乐|日推|每日推荐", text) and re.search(
        r"播放|放一下|放一放|听听|来听|放我|放这|放.*歌单|放.*日推", text
    ):
        return ["play"]
    if re.search(r"日推|每日推荐", text):
        return ["daily"]
    if re.search(r"喜欢的音乐", text):
        return ["liked"]
    if re.search(
        r"里面|曲目|哪[些几]首|有哪些歌|歌曲列表|这张歌单|这个歌单|歌单里|多少首", text
    ):
        return ["tracks"]
    if re.search(r"歌单|下一页|上一页|继续列", text):
        return ["playlists"]
    return []


def scoped_music_catalog(plugins, text, context=""):
    """Copy metadata; never mutate the cached plugin registry or other turns."""
    actions = cloud_music_actions(text, context)
    result = []
    for plugin in plugins:
        if plugin.get("id") != "netease_music":
            result.append(plugin)
            continue
        plugin = deepcopy(plugin)
        if not actions:
            plugin["entries"] = [
                e for e in plugin.get("entries", []) if e.get("id") != "cloud_music"
            ]
            if not requests_local_music(text):
                for entry in plugin["entries"]:
                    if entry.get("id") == "playlist":
                        action = (
                            (entry.get("input_schema") or {})
                            .get("properties", {})
                            .get("action", {})
                        )
                        if "enum" in action:
                            action["enum"] = [
                                a
                                for a in action["enum"]
                                if a not in {"save", "save_current"}
                            ]
        else:
            plugin["description"] = "网易云账号与云端音乐库（本轮按需提供的能力）。"
            entries = [
                e for e in plugin.get("entries", []) if e.get("id") == "cloud_music"
            ]
            for entry in entries:
                entry["description"] = " ".join(_DETAILS[a][0] for a in actions)
                schema = entry.get("input_schema") or {}
                props = schema.get("properties") or {}
                allowed = {
                    "action",
                    *(field for action in actions for field in _DETAILS[action][1]),
                }
                schema["properties"] = {k: v for k, v in props.items() if k in allowed}
                if "action" in schema["properties"]:
                    schema["properties"]["action"]["enum"] = actions
                entry["input_schema"] = schema
            plugin["entries"] = entries
        result.append(plugin)
    return result
