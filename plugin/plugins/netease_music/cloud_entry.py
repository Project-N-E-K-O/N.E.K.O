"""One cloud-library entry; detailed action schema is shown only on demand."""

import asyncio
import re
import json

from plugin.sdk.plugin import plugin_entry, tr
from .cloud import CloudLibrary, CloudError, page, song_summary
from .playlists import Queue
from .provider import select_first_exact_match
from utils.music_intent import CLOUD_WRITE_ACTIONS, music_write_action

CLOUD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "account",
                "playlists",
                "tracks",
                "play",
                "liked",
                "daily",
                "artists",
                "create",
                "add",
                "remove",
                "like",
                "unlike",
            ],
        },
        "playlist": {
            "type": "string",
            "maxLength": 100,
            "description": "云端歌单精确名称或ID；liked=喜欢的音乐；daily=日推",
        },
        "song": {
            "type": "string",
            "maxLength": 100,
            "description": "歌名 歌手或歌曲ID；current=正在听；queue=整组当前队列（仅add）",
        },
        "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        "kind": {
            "type": "string",
            "enum": ["all", "created", "subscribed"],
            "default": "all",
        },
        "privacy": {"type": "boolean", "default": True},
    },
    "required": ["action"],
}

_ERRORS = {
    "login_required": "网易云插件还没有保存登录凭据。",
    "login_expired": "网易云登录已失效，请更新插件中的登录凭据。",
    "playlist_required": "需要明确的云端歌单名称或ID。",
    "playlist_not_found": "账号可见歌单中没有这个名称或ID，请先列出歌单核对。",
    "ambiguous_playlist": "有重名歌单，请用查询结果中的歌单ID指定。",
    "playlist_not_owned": "这张歌单不是你创建的，不能修改其中的歌曲。",
    "song_required": "需要具体歌曲，或明确选择正在听的歌曲。",
    "song_not_found": "没有找到精确匹配的歌曲，未更改收藏。",
    "no_current_track": "播放器没有确认正在听的网易云歌曲。",
    "write_unconfirmed": "请求已经提交，但尚未核实云端结果，请查询后再决定是否重试。",
    "upstream_rejected": "网易云没有接受这次操作，未确认成功。",
    "upstream_unavailable": "网易云暂时无法连接，未确认操作结果。",
}


def cloud_result_summary(data):
    """Keep a complete bounded page, including its cursor, in the LLM result."""
    from utils.tokenize import count_tokens

    rows = data.get("items")
    if not isinstance(rows, list):
        return json.dumps(data, ensure_ascii=False)
    lines = []
    used = 0
    for row in rows:
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        used += count_tokens(line)
        if used > 2000:
            break
        lines.append(line)
    if len(lines) < len(rows):
        data["items"] = rows[: len(lines)]
        data["next_offset"] = data.get("offset", 0) + len(lines)
    header = {k: v for k, v in data.items() if k != "items"}
    return (
        "网易云云端查询结果（只包含本页）\n"
        + json.dumps(header, ensure_ascii=False)
        + "\n"
        + "\n".join(lines)
    )


class CloudMusicMixin:
    def _new_cloud_library(self):
        return CloudLibrary(self._netease_cookies)

    @plugin_entry(
        id="cloud_music",
        name=tr("cloud.title", default="NetEase account and cloud library"),
        description=tr(
            "cloud.description",
            default="Read and maintain the NetEase cloud library on explicit request. Return only the requested page.",
        ),
        input_schema=CLOUD_SCHEMA,
        llm_result_fields=["summary"],
        timeout=120,
    )
    async def cloud_music(
        self,
        action,
        playlist="",
        song="",
        offset=0,
        limit=20,
        kind="all",
        privacy=True,
        _ctx=None,
    ):
        target = self._target_lanlan(_ctx or {})
        if not target:
            return await self.finish(
                data={"status": "error", "summary": "缺少目标角色会话。"},
                delivery="proactive",
            )
        if action not in CLOUD_SCHEMA["properties"]["action"]["enum"] or kind not in {
            "all",
            "created",
            "subscribed",
        }:
            return await self.finish(
                data={"status": "error", "summary": "未知的云端音乐操作。"},
                delivery="proactive",
            )
        if (
            type(offset) is not int
            or type(limit) is not int
            or not 0 <= offset <= 100000
            or not 1 <= limit <= 50
        ):
            return await self.finish(
                data={"status": "error", "summary": "分页参数无效。"},
                delivery="proactive",
            )
        request = str((_ctx or {}).get("latest_user_request") or "")
        if action in CLOUD_WRITE_ACTIONS and (
            (_ctx or {}).get("request_source") != "user"
            or music_write_action(request) != action
        ):
            return await self.finish(
                data={
                    "status": "blocked",
                    "summary": "本轮没有明确要求这项云端修改，未执行。",
                },
                delivery="proactive",
            )
        if song == "queue" and not re.search(r"整组|整张|全部|整个|所有", request):
            return await self.finish(
                data={
                    "status": "blocked",
                    "summary": "未明确要求保存整组曲目，未执行。",
                },
                delivery="proactive",
            )
        if not hasattr(self, "_cloud_lock"):
            self._cloud_lock = asyncio.Lock()
        key = self._session_key(target)
        generation = self._begin_request(key) if action == "play" else None
        task = asyncio.current_task()
        if not hasattr(self, "_active_play_tasks"):
            self._active_play_tasks = set()
        self._active_play_tasks.add(task)
        try:
            async with self._cloud_lock:
                if action == "play" and not self._is_current(key, generation):
                    return await self._finish_silent("superseded")
                async with self._new_cloud_library() as cloud:
                    result = await self._cloud_operation(
                        cloud,
                        target,
                        action,
                        playlist,
                        song,
                        offset,
                        limit,
                        kind,
                        privacy,
                        str((_ctx or {}).get("music_request_id") or ""),
                        generation=generation,
                        proactive=(_ctx or {}).get("request_source") == "proactive",
                    )
            if action == "play":  # _advance already returned a plugin finish envelope.
                return result
            data = {"scope": "netease_cloud", "action": action, **result}
            data["summary"] = cloud_result_summary(data)
            return await self.finish(data=data, delivery="proactive")
        except CloudError as exc:
            return await self.finish(
                data={
                    "status": "error",
                    "code": exc.code,
                    "summary": _ERRORS.get(
                        exc.code, "未能完成云端音乐操作，请稍后查询。"
                    ),
                },
                delivery="proactive",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self.finish(
                data={"status": "error", "summary": "云端音乐操作失败，未确认成功。"},
                delivery="proactive",
            )
        finally:
            self._active_play_tasks.discard(task)

    async def _cloud_song_ids(self, target, song, *, allow_queue=False):
        song = str(song or "").strip()
        if song == "queue" and allow_queue:
            queue = self._queues.get(self._session_key(target))
            if not queue or not queue.tracks:
                raise CloudError("song_required")
            return list(dict.fromkeys(t.song_id for t in queue.tracks))
        if song in {"current", "这首", "正在听的歌"}:
            current = await self._current_track(target)
            if not current or not current.get("song_id"):
                raise CloudError("no_current_track")
            return [int(current["song_id"])]
        if song.isdecimal() and 0 < int(song) < 10**16:
            return [int(song)]
        if not song:
            raise CloudError("song_required")
        async with self._new_provider() as provider:
            match = select_first_exact_match(song, await provider.search(song))
        if not match:
            raise CloudError("song_not_found")
        return [match.song_id]

    async def _cloud_operation(
        self,
        cloud,
        target,
        action,
        playlist,
        song,
        offset,
        limit,
        kind,
        privacy,
        request_id="",
        *,
        generation=None,
        proactive=False,
    ):
        if action == "play":
            key = self._session_key(target)
            if generation is None:
                generation = self._begin_request(key)
        if action == "account":
            return {"account": await cloud.account()}
        if action == "playlists":
            rows = await cloud.playlists()
            rows = [p for p in rows if kind == "all" or p["kind"] == kind]
            return {"kind": kind, **page(rows, offset, limit)}
        if action == "artists":
            return page(await cloud.artists(), offset, limit)
        if action == "daily":
            return page([song_summary(t) for t in await cloud.daily()], offset, limit)
        if action in {"tracks", "liked", "play"}:
            if action == "play" and playlist == "daily":
                tracks, name = await cloud.daily(), "网易云每日推荐"
                total = len(tracks)
            else:
                selected = await cloud.resolve_playlist(
                    "liked" if action == "liked" else playlist
                )
                name = selected["name"]
                tracks, total = await cloud.tracks(
                    selected["id"], offset, 1000 if action == "play" else limit
                )
            if action != "play":
                return {
                    "playlist": selected,
                    "items": [song_summary(t) for t in tracks],
                    "total": total,
                    "offset": offset,
                    "next_offset": offset + limit if offset + limit < total else None,
                }
            if not tracks:
                raise CloudError("playlist_unavailable")
            if not self._is_current(key, generation):
                return await self._finish_silent("superseded")
            queue = self._queues[key] = Queue(name, tracks, proactive_start=proactive)
            self._note(
                target,
                f"从网易云歌单《{name}》准备了 {len(tracks)} 首，开始尝试播放。"
                + (
                    f"本次最多载入1000首，歌单共{total}首。"
                    if total > len(tracks)
                    else ""
                ),
            )
            async with self._new_provider() as provider:
                return await self._advance(provider, target, key, queue)
        if action == "create":
            return await cloud.create(playlist, privacy)
        ids = await self._cloud_song_ids(target, song, allow_queue=action == "add")
        if action in {"like", "unlike"}:
            return await cloud.like(ids[0], liked=action == "like")
        return await cloud.change_tracks(playlist, ids, add=action == "add")
