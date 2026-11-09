"""Authenticated cloud library; private sessions, bounded results, verified writes."""

from __future__ import annotations

import asyncio
import time

from pyncm_async import Session
from pyncm_async.apis.login import GetCurrentLoginStatus
from pyncm_async.apis.user import GetUserPlaylists, GetUserArtistSubs
from pyncm_async.apis.playlist import (
    GetPlaylistInfo,
    SetCreatePlaylist,
    SetManipulatePlaylistTracks,
)
from pyncm_async.apis.track import GetTrackDetail, SetLikeTrack

from .provider import (
    NeteaseMusicProvider,
    ProviderError,
    _parse_candidate,
    _sanitize_display,
)


class CloudError(ProviderError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CloudLibrary:
    def __init__(self, cookies):
        self.cookies = dict(cookies)
        self.session = None
        self._profile = None
        self._last_request_at = 0.0
        self._request_lock = asyncio.Lock()

    async def __aenter__(self):
        if not self.cookies.get("MUSIC_U"):
            raise CloudError("login_required")
        self.session = Session(
            proxy=None, trust_env=False, follow_redirects=False, timeout=15
        )
        NeteaseMusicProvider._sync_session_cookies(self.session, self.cookies)
        return self

    async def __aexit__(self, *args):
        if self.session is not None:
            await self.session.aclose()

    async def call(self, function, *args, **kwargs):
        try:
            async with self._request_lock:
                delay = 0.3 - (time.monotonic() - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
                try:
                    body = await function(*args, session=self.session, **kwargs)
                finally:
                    self._last_request_at = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never propagate upstream exceptions containing cookies or URLs.
            raise CloudError("upstream_unavailable") from None
        if not isinstance(body, dict):
            raise CloudError("invalid_response")
        code = body.get("code", 200)
        if code in (301, 302):
            raise CloudError("login_expired")
        if code != 200:
            raise CloudError("upstream_rejected")
        return body

    async def account(self):
        if self._profile is None:
            body = await self.call(GetCurrentLoginStatus)
            body = body.get("data") or body
            profile, account = body.get("profile") or {}, body.get("account") or {}
            uid = profile.get("userId") or account.get("id")
            if not uid:
                raise CloudError("login_expired")
            self._profile = {
                "user_id": int(uid),
                "nickname": _sanitize_display(profile.get("nickname")),
                "vip_type": int(profile.get("vipType") or 0),
            }
        return dict(self._profile)

    async def playlists(self):
        uid = (await self.account())["user_id"]
        rows, seen = [], set()
        for offset in range(0, 5000, 200):
            body = await self.call(GetUserPlaylists, uid, offset=offset, limit=200)
            items = body.get("playlist") or []
            added = 0
            for item in items:
                pid = item.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                added += 1
                owner = int((item.get("creator") or {}).get("userId") or 0)
                rows.append(
                    {
                        "id": int(pid),
                        "name": _sanitize_display(item.get("name")),
                        "kind": "created" if owner == uid else "subscribed",
                        "track_count": int(item.get("trackCount") or 0),
                        "liked": str(item.get("specialType")) == "5",
                    }
                )
            if not body.get("more") or not added:
                return rows
        raise CloudError("library_too_large")

    async def resolve_playlist(self, selector, *, owned=False):
        selector = str(selector or "").strip()
        if not selector:
            raise CloudError("playlist_required")
        rows = await self.playlists()
        matches = [
            p
            for p in rows
            if str(p["id"]) == selector
            or p["name"].casefold() == selector.casefold()
            or (
                selector in {"liked", "我喜欢的音乐", "喜欢的音乐"}
                and p["liked"]
                and p["kind"] == "created"
            )
        ]
        if not matches and selector.endswith("歌单"):
            # Accept the unquoted conversational suffix only when exact lookup failed.
            matches = [
                p for p in rows if p["name"].casefold() == selector[:-2].casefold()
            ]
        if len(matches) != 1:
            raise CloudError("ambiguous_playlist" if matches else "playlist_not_found")
        selected = matches[0]
        if owned and selected["kind"] != "created":
            raise CloudError("playlist_not_owned")
        return selected

    async def track_ids(self, playlist_id):
        body = await self.call(GetPlaylistInfo, playlist_id)
        playlist = body.get("playlist")
        if not isinstance(playlist, dict) or "trackIds" not in playlist:
            raise CloudError("playlist_unavailable")
        return list(
            dict.fromkeys(int(t["id"]) for t in playlist["trackIds"] if t.get("id"))
        )

    async def tracks(self, playlist_id, offset=0, limit=20):
        ids = await self.track_ids(playlist_id)
        selected = ids[offset : offset + limit]
        by_id = {}
        for start in range(0, len(selected), 100):
            body = await self.call(GetTrackDetail, selected[start : start + 100])
            for item in body.get("songs") or []:
                song = _parse_candidate(item)
                if song:
                    by_id[song.song_id] = song
        # Preserve server playlist order even when song details arrive shuffled.
        return [by_id[i] for i in selected if i in by_id], len(ids)

    async def daily(self):
        from pyncm_async.apis import WeapiCryptoRequest

        @WeapiCryptoRequest
        def GetDailyRecommendedSongs():
            return "/api/v3/discovery/recommend/songs", {}

        await self.account()
        body = await self.call(GetDailyRecommendedSongs)
        items = (
            (body.get("data") or {}).get("dailySongs") or body.get("recommend") or []
        )
        return [song for item in items if (song := _parse_candidate(item))]

    async def artists(self):
        await self.account()
        body = await self.call(GetUserArtistSubs, limit=5000)
        if body.get("hasMore") or body.get("more"):
            raise CloudError("library_too_large")
        return [
            {"id": int(a["id"]), "name": _sanitize_display(a.get("name"))}
            for a in body.get("data") or []
            if a.get("id")
        ]

    async def create(self, name, privacy=True):
        await self.account()
        name = _sanitize_display(name, limit=100)
        if not name:
            raise CloudError("playlist_required")
        # Do not silently create a duplicate on a repeated task.
        existing = [
            p
            for p in await self.playlists()
            if p["name"] == name and p["kind"] == "created"
        ]
        if existing:
            return {"status": "already_exists", "playlist": existing[0]}
        body = await self.call(SetCreatePlaylist, name, privacy=privacy)
        pid = (body.get("playlist") or {}).get("id") or body.get("id")
        if not pid:
            raise CloudError("write_unconfirmed")
        found = [p for p in await self.playlists() if p["id"] == int(pid)]
        if not found:
            raise CloudError("write_unconfirmed")
        return {"status": "created", "playlist": found[0]}

    async def change_tracks(self, selector, song_ids, *, add):
        playlist = await self.resolve_playlist(selector, owned=True)
        before = set(await self.track_ids(playlist["id"]))
        changed = [
            sid for sid in song_ids if (sid not in before if add else sid in before)
        ]
        if not changed:
            return {
                "status": "unchanged",
                "playlist": playlist,
                "changed_count": 0,
                "song_ids": song_ids[:20],
            }
        await self.call(
            SetManipulatePlaylistTracks,
            changed,
            playlist["id"],
            op="add" if add else "del",
        )
        after = set(await self.track_ids(playlist["id"]))
        if any((sid not in after if add else sid in after) for sid in changed):
            raise CloudError("write_unconfirmed")
        return {
            "status": "added" if add else "removed",
            "playlist": playlist,
            "changed_count": len(changed),
            "song_ids": changed[:20],
        }

    async def like(self, song_id, *, liked):
        uid = (await self.account())["user_id"]
        playlist = await self.resolve_playlist("liked", owned=True)
        if (song_id in set(await self.track_ids(playlist["id"]))) == liked:
            return {"status": "unchanged", "song_id": song_id, "liked": liked}
        await self.call(SetLikeTrack, song_id, like=liked, userid=uid)
        if (song_id in set(await self.track_ids(playlist["id"]))) != liked:
            raise CloudError("write_unconfirmed")
        return {
            "status": "liked" if liked else "unliked",
            "song_id": song_id,
            "liked": liked,
        }


def song_summary(song):
    return {"id": song.song_id, "name": song.name, "artist": song.artist}


def page(rows, offset, limit):
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
    }
