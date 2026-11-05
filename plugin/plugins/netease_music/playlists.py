"""Per-character queue. Only one confirmed terminal event may consume a track."""

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from functools import partial

import httpx

from plugin.sdk.plugin import Err, SdkError, plugin_entry, tr
from utils.file_utils import atomic_write_json

from .models import SongCandidate
from .provider import ProviderError, select_recording


@dataclass
class Queue:
    name: str
    tracks: list[SongCandidate]
    locale: str | None = None
    cursor: int = 0
    ticket: str = ""
    advancing_ticket: str = ""
    advance_epoch: int = 0


class PlaylistMixin:
    def _library_file(self, target):
        return self.data_path() / (
            "playlists-" + hashlib.sha256(target.encode()).hexdigest()[:24] + ".json"
        )

    def _library(self, target):
        path = self._library_file(target)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _write_library(self, target, library):
        path = self._library_file(target)
        atomic_write_json(path, library, indent=None)

    def _store_tracks(self, target, name, rows, *, append_current=False):
        # Serialized read-modify-replace runs off the event loop, including on cancellation.
        with self._library_lock:
            library = self._library(target)
            if append_current:
                current = rows[0]
                saved = library.setdefault(name, [])
                if any(row[0] == current[0] for row in saved):
                    return "already_saved", len(saved)
                saved.append(current)
            else:
                library[name] = rows
            self._write_library(target, library)
            return "added", len(library[name])

    async def _current_track(self, target):
        from config.network import MAIN_SERVER_PORT

        async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
            response = await client.get(
                f"http://127.0.0.1:{MAIN_SERVER_PORT}/api/music/current/netease",
                params={"lanlan_name": target},
            )
            response.raise_for_status()
            return response.json()["track"]

    async def _save_current(self, target, name, t):
        try:
            track = await self._current_track(target)
        except httpx.HTTPError:
            track = None
        if not name or not track:
            message = t("playlist.need_name" if not name else "playlist.no_track")
            status = "not_saved"
        else:
            row = [track["song_id"], track["name"], track["artist"]]
            status, count = await asyncio.to_thread(
                self._store_tracks, target, name, [row], append_current=True
            )
            message = t(
                f"playlist.{status}",
                name=name,
                song=track["name"],
                artist=track["artist"],
                count=count,
            )
        receipt = self._note(target, message, ai_behavior="respond")
        return await self.finish(
            data={
                "status": status,
                "summary": message,
                "confirmation_submitted": self._submitted(receipt),
            },
            delivery="silent",
        )

    def _note(self, target, text, ai_behavior="blind"):
        return self.push_message(
            source="netease_music",
            visibility=["chat"],
            ai_behavior=ai_behavior,
            parts=[{"type": "text", "text": text}],
            target_lanlan=target,
        )

    @plugin_entry(
        id="playlist",
        name=tr("playlist.title", default="网易云歌单连播与收藏"),
        description=tr("entry.playlist.description", default="放点歌、陪伴连播默认 curate：结合聊天和音乐偏好记忆选 6 至 8 首，tracks 为字符串数组，每项「歌名 | 歌手」。搜网易云现成歌单用 search，name 传关键词。revise 按新偏好替换尚未播放的曲目，不打断当前歌；save_current 将此刻播放器确认的一首追加到指定本地歌单，自动去重；仅在明确保存整张播放队列时用 save；replay 重放“我们自己的本地歌单”或已收藏的 name；list 列出收藏；next 下一首；stop 停止。"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "curate",
                        "revise",
                        "search",
                        "save_current",
                        "save",
                        "replay",
                        "list",
                        "next",
                        "stop",
                    ],
                },
                "name": {"type": "string", "maxLength": 100},
                "tracks": {
                    "type": "array",
                    "maxItems": 24,
                    "items": {"type": "string", "maxLength": 150},
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        timeout=90,
    )
    async def playlist(self, action, name="", tracks=None, _ctx=None):
        locale = self._locale(_ctx or {})
        t = partial(self.i18n.t, locale=locale)
        target = self._target_lanlan(_ctx or {})
        if not target:
            return Err(SdkError(t("playlist.target")))
        key = self._session_key(target)
        q = self._queues.get(key)
        name = str(name or "").strip()[:100]
        if action == "list":
            names = list(await asyncio.to_thread(self._library, target))
            self._note(
                target,
                t("playlist.list", names=", ".join(names))
                if names
                else t("playlist.empty"),
            )
            return await self.finish(data={"names": names}, delivery="silent")
        if action == "save_current":
            return await self._save_current(target, name, t)
        if action == "save":
            if not q or not name:
                self._note(target, t("playlist.need_queue"))
                return await self._finish_silent("no_queue_or_name")
            rows = [[track.song_id, track.name, track.artist] for track in q.tracks]
            await asyncio.to_thread(self._store_tracks, target, name, rows)
            self._note(target, t("playlist.saved", name=name, count=len(rows)))
            return await self._finish_silent("saved")
        if action == "stop":
            self._begin_request(key)
            if q:
                q.ticket = ""
                q.advancing_ticket = ""
                q.advance_epoch += 1
                q.cursor = len(q.tracks)
            self.push_message(
                source="netease_music",
                visibility=[],
                ai_behavior="blind",
                target_lanlan=target,
                parts=[{"type": "ui_action", "action": "netease_stop"}],
            )
            return await self._finish_silent("stopped")
        if action in ("next", "revise") and not q:
            self._note(target, t("playlist.no_queue"))
            return await self._finish_silent("no_queue")
        editing = action == "revise"
        generation = (
            self._session_generations.get(key) if editing else self._begin_request(key)
        )
        if q and action == "next":
            q.ticket = ""
        async with self._new_provider() as provider:
            if action == "next":
                return await self._advance(provider, target, key, q)
            if action == "search" and name:
                name, rows = await provider.playlist(name)
            elif action == "replay" and name:
                library = await asyncio.to_thread(self._library, target)
                rows = [SongCandidate(*row) for row in library.get(name, [])]
            elif action in ("curate", "revise"):
                rows = await self._curated_tracks(provider, tracks or [])
                name = name or t("playlist.curated", character=target)
            else:
                return Err(SdkError(t("playlist.need_queue")))
            if not self._is_current(key, generation):
                return await self._finish_silent("superseded")
            if not rows or (action == "curate" and len(rows) < 2):
                self._note(target, t("playlist.no_results"))
                return await self._finish_silent("no_results")
            if editing:
                if not q.ticket:
                    self._note(target, t("playlist.no_queue"))
                    return await self._finish_silent("inactive")
                prefix = q.tracks[: q.cursor]
                seen = {t.song_id for t in prefix}
                tail = [t for t in rows if t.song_id not in seen]
                if not tail:
                    self._note(target, t("playlist.same_tail"))
                    return await self._finish_silent("no_new_tracks")
                q.tracks = prefix + tail
                self._note(target, t("playlist.revised", count=len(tail)))
                return await self._finish_silent("revised")
            q = self._queues[key] = Queue(name, rows, locale)
            self._note(target, t("playlist.playing", name=name, count=len(rows)))
            return await self._advance(provider, target, key, q)

    async def _curated_tracks(self, provider, pairs):
        gate = asyncio.Semaphore(4)

        async def find_recording(pair):
            title, _, artist = pair.partition("|")
            title, artist = title.strip(), artist.strip()
            query = f"{title} {artist}"
            if not title or not artist or len(query) > 100:
                return None
            async with gate:
                try:
                    candidates = await provider.search(query)
                except (ProviderError, httpx.HTTPError):
                    return None
            return select_recording(title, artist, candidates)

        results = await asyncio.gather(*(find_recording(pair) for pair in pairs[:24]))
        return list(
            {track.song_id: track for track in results if track is not None}.values()
        )

    async def _advance(self, provider, target, key, q):
        q.advance_epoch += 1
        epoch = q.advance_epoch

        def still_playing_queue():
            # A failed replacement song request must not cancel this queue.
            return self._queues.get(key) is q and not self._closed and q.advance_epoch == epoch

        skipped = 0
        while q.cursor < len(q.tracks) and still_playing_queue():
            track = q.tracks[q.cursor]
            q.cursor += 1
            next_cursor = q.cursor
            try:
                media = await asyncio.wait_for(
                    provider.resolve_media(track.song_id), timeout=14
                )
            except asyncio.CancelledError:
                if still_playing_queue() and q.cursor == next_cursor:
                    q.cursor -= 1
                raise
            except (TimeoutError, ProviderError, httpx.HTTPError):
                skipped += 1
                if skipped >= 4 and q.cursor < len(q.tracks) and still_playing_queue():
                    self._note(target, self.i18n.t("playlist.paused", locale=q.locale))
                    return await self._finish_silent("network_pause")
                continue
            if not still_playing_queue():
                break
            q.advancing_ticket = ""
            q.ticket = secrets.token_hex(16)
            if self._push_track(target, track, media, q.ticket):
                return await self.finish(
                    data={
                        "status": "submitted",
                        "position": q.cursor,
                        "total": len(q.tracks),
                        "song": self._candidate_data(track),
                    },
                    delivery="silent",
                )
            q.ticket = ""
            return Err(SdkError(self.i18n.t("errors.delivery_failed", locale=q.locale)))
        if still_playing_queue():
            q.ticket = ""
            self._note(
                target, self.i18n.t("playlist.ended", locale=q.locale, name=q.name)
            )
            return await self._finish_silent("ended")
        return await self._finish_silent("superseded")

    @plugin_entry(
        id="playlist_terminal",
        name="播放器队列回执",
        metadata={"agent_hidden": True},
        input_schema={
            "type": "object",
            "properties": {"ticket": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["ticket", "reason"],
        },
        timeout=90,
    )
    async def playlist_terminal(self, ticket, reason, _ctx=None):
        target = self._target_lanlan(_ctx or {})
        key = self._session_key(target)
        q = self._queues.get(key)
        if (
            not q
            or not ticket
            or (ticket != q.ticket and not (reason == "closed" and ticket == q.advancing_ticket))
            or reason not in ("ended", "error", "closed")
        ):
            return await self._finish_silent("stale")
        q.ticket = ""
        if self._session_generations.get(key) is None:
            return await self._finish_silent("stale")
        if reason == "closed":
            self._begin_request(key)
            q.advancing_ticket = ""
            q.advance_epoch += 1
            q.cursor = len(q.tracks)
            return await self._finish_silent("closed")
        q.advancing_ticket = ticket
        async with self._new_provider() as provider:
            return await self._advance(provider, target, key, q)
