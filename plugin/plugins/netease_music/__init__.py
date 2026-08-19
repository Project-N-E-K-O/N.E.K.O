"""Anonymous NetEase Cloud Music playback plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    tr,
)

from .credential_ui import CredentialUiMixin
from .models import PlayRequest, ResolvedMedia, SongCandidate
from .provider import (
    MediaUnavailableError,
    NeteaseMusicProvider,
    select_first_exact_match,
)

_SOURCE = "netease_music"


class _ProviderLike(Protocol):
    async def search(self, query: str) -> list[SongCandidate]: ...

    async def resolve_media(self, song_id: int) -> ResolvedMedia: ...


class _ProviderContext(Protocol):
    async def __aenter__(self) -> _ProviderLike: ...

    async def __aexit__(self, *exc: object) -> None: ...


@neko_plugin
class NeteaseMusicPlugin(CredentialUiMixin, NekoPluginBase):
    """Search NetEase anonymously and submit validated playback actions."""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self._init_credential_store()
        self._provider_factory = lambda: NeteaseMusicProvider(
            cookies=self._netease_cookies
        )
        # The host calls startup before entries become runnable.  Defaulting
        # closed keeps direct/pre-start invocations fail-safe as well.
        self._closed = True
        self._generation_counter = 0
        self._session_generations: dict[str, int] = {}
        self._active_play_tasks: set[asyncio.Task[Any]] = set()

    @lifecycle(id="startup")
    async def startup(self, **_: object):
        await self._load_netease_cookies()
        self._closed = False
        self._session_generations.clear()
        return Ok(
            {
                "status": "ready",
                "cookie_configured": bool(self._netease_cookies.get("MUSIC_U")),
            }
        )

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: object):
        self._closed = True
        self._generation_counter += 1
        self._session_generations.clear()
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="play_netease_music",
        name=tr(
            "entry.play.name",
            default="播放网易云歌曲",
        ),
        description=tr(
            "entry.play.description",
            default=(
                "仅在用户明确要求播放或点播某首网易云歌曲时调用。将 query 整理为“歌名 歌手”，"
                "保留现场版、翻唱等版本限定。不要用于歌词或歌曲知识查询、泛推荐、歌单、暂停、"
                "停止、切歌或下一首。"
            ),
        ),
        params=PlayRequest,
        timeout=20.0,
    )
    async def play_netease_music(
        self,
        params: PlayRequest,
        _ctx: dict[str, Any] | None = None,
    ):
        active_tasks = getattr(self, "_active_play_tasks", None)
        if active_tasks is None:
            active_tasks = set()
            self._active_play_tasks = active_tasks
        current_task = asyncio.current_task()
        if current_task is not None:
            active_tasks.add(current_task)
        try:
            return await self._play_netease_music(params, _ctx)
        finally:
            if current_task is not None:
                active_tasks.discard(current_task)

    async def _play_netease_music(
        self,
        params: PlayRequest,
        _ctx: dict[str, Any] | None = None,
    ):
        ctx = _ctx if isinstance(_ctx, dict) else {}
        target_lanlan = self._target_lanlan(ctx)
        if not target_lanlan:
            return self._error(
                "errors.missing_target",
                "无法确定当前播放会话，已取消点歌。",
                code="missing_session_target",
                ctx=ctx,
            )

        session_key = self._session_key(target_lanlan)
        generation = self._begin_request(session_key)
        if self._closed:
            return await self._finish_silent("superseded")

        try:
            async with self._new_provider() as provider:
                return await self._play_with_provider(
                    provider=provider,
                    params=params,
                    ctx=ctx,
                    target_lanlan=target_lanlan,
                    session_key=session_key,
                    generation=generation,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._is_current(session_key, generation):
                return await self._finish_silent("superseded")
            return self._error(
                "errors.search_failed",
                "网易云搜索暂时不可用，请稍后重试。",
                code="search_failed",
                ctx=ctx,
            )

    async def _invalidate_credential_requests(self) -> None:
        self._generation_counter += 1
        self._session_generations.clear()
        current_task = asyncio.current_task()
        pending = [
            task
            for task in getattr(self, "_active_play_tasks", set())
            if task is not current_task and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _play_with_provider(
        self,
        *,
        provider: _ProviderLike,
        params: PlayRequest,
        ctx: dict[str, Any],
        target_lanlan: str,
        session_key: str,
        generation: int,
    ):
        candidates = await provider.search(params.query)

        if not self._is_current(session_key, generation):
            return await self._finish_silent("superseded")

        if not candidates:
            return await self._push_expected_outcome(
                session_key=session_key,
                generation=generation,
                target_lanlan=target_lanlan,
                ctx=ctx,
                status="no_results",
                text=self._t(
                    "messages.no_results",
                    "没有找到匹配的网易云歌曲，请换一个更具体的歌名或歌手。",
                    ctx,
                ),
            )

        selected = candidates[0]
        used_fallback = select_first_exact_match(params.query, [selected]) is None

        try:
            media = await provider.resolve_media(selected.song_id)
        except asyncio.CancelledError:
            raise
        except MediaUnavailableError:
            if not self._is_current(session_key, generation):
                return await self._finish_silent("superseded")
            return await self._push_expected_outcome(
                session_key=session_key,
                generation=generation,
                target_lanlan=target_lanlan,
                ctx=ctx,
                status="unavailable",
                text=self._t(
                    "messages.unavailable",
                    "这首歌目前没有可匿名播放的公开音源，可能需要登录或会员权限。",
                    ctx,
                ),
                data={"song": self._candidate_data(selected)},
            )
        except Exception:
            if not self._is_current(session_key, generation):
                return await self._finish_silent("superseded")
            return self._error(
                "errors.media_failed",
                "网易云音源验证失败，未提交播放。",
                code="media_validation_failed",
                ctx=ctx,
            )

        if not self._is_current(session_key, generation):
            return await self._finish_silent("superseded")

        allowlist_receipt = self.push_message(
            source=_SOURCE,
            visibility=[],
            ai_behavior="blind",
            parts=[
                {
                    "type": "ui_action",
                    "action": "media_allowlist_add",
                    "domains": [media.hostname],
                }
            ],
            target_lanlan=target_lanlan,
        )
        if not self._submitted(allowlist_receipt):
            return self._error(
                "errors.delivery_failed",
                "播放动作提交失败，未开始播放。",
                code="delivery_rejected",
                ctx=ctx,
            )

        if not self._is_current(session_key, generation):
            return await self._finish_silent("superseded")

        play_receipt = self.push_message(
            source=_SOURCE,
            visibility=["chat"],
            ai_behavior="blind",
            parts=[
                {
                    "type": "ui_action",
                    "action": "media_play_url",
                    "url": media.url,
                    "media_type": "audio",
                    "name": selected.name,
                    "artist": selected.artist,
                }
            ],
            target_lanlan=target_lanlan,
        )
        if not self._submitted(play_receipt):
            return self._error(
                "errors.delivery_failed",
                "播放动作提交失败，未开始播放。",
                code="delivery_rejected",
                ctx=ctx,
            )

        notice_submitted = True
        if used_fallback:
            notice_receipt = self.push_message(
                source=_SOURCE,
                visibility=["chat"],
                ai_behavior="blind",
                parts=[
                    {
                        "type": "text",
                        "text": self._t(
                            "messages.fallback",
                            "我不太确定这是不是你想听的版本，先按搜索结果给你放第一首啦。",
                            ctx,
                        ),
                    }
                ],
                target_lanlan=target_lanlan,
            )
            notice_submitted = self._submitted(notice_receipt)

        return await self.finish(
            data={
                "status": "submitted",
                "summary": self._t(
                    "messages.submitted",
                    "播放动作已提交。",
                    ctx,
                ),
                "song": self._candidate_data(selected),
                "used_fallback": used_fallback,
                "notice_submitted": notice_submitted,
            },
            delivery="silent",
        )

    def _new_provider(self) -> _ProviderContext:
        return self._provider_factory()

    def _begin_request(self, session_key: str) -> int:
        self._generation_counter += 1
        generation = self._generation_counter
        self._session_generations[session_key] = generation
        return generation

    def _is_current(self, session_key: str, generation: int) -> bool:
        return (
            not self._closed
            and self._session_generations.get(session_key) == generation
        )

    @staticmethod
    def _target_lanlan(ctx: Mapping[str, object]) -> str:
        value = ctx.get("lanlan_name")
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _session_key(target_lanlan: str) -> str:
        # Playback state is owned by a lanlan/player target.  The host creates
        # a fresh conversation_id for each analyzed turn, so using that value
        # would let an older request from the same player arrive late and
        # overwrite the newer song.
        return f"lanlan:{target_lanlan}"

    @staticmethod
    def _locale(ctx: Mapping[str, object]) -> str | None:
        for key in ("language", "locale", "session_language"):
            value = ctx.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _t(
        self,
        key: str,
        default: str,
        ctx: Mapping[str, object],
        **params: object,
    ) -> str:
        return self.i18n.t(
            key,
            locale=self._locale(ctx),
            default=default,
            **params,
        )

    async def _push_expected_outcome(
        self,
        *,
        session_key: str,
        generation: int,
        target_lanlan: str,
        ctx: Mapping[str, object],
        status: str,
        text: str,
        data: dict[str, object] | None = None,
    ):
        if not self._is_current(session_key, generation):
            return await self._finish_silent("superseded")

        receipt = self.push_message(
            source=_SOURCE,
            visibility=["chat"],
            ai_behavior="blind",
            parts=[{"type": "text", "text": text}],
            target_lanlan=target_lanlan,
        )
        if not self._submitted(receipt):
            return self._error(
                "errors.message_delivery_failed",
                "结果消息提交失败。",
                code="delivery_rejected",
                ctx=ctx,
            )

        payload: dict[str, object] = {"status": status}
        if data:
            payload.update(data)
        return await self.finish(data=payload, delivery="silent")

    async def _finish_silent(self, status: str):
        return await self.finish(data={"status": status}, delivery="silent")

    def _error(
        self,
        key: str,
        default: str,
        *,
        code: str,
        ctx: Mapping[str, object],
    ) -> Err[SdkError]:
        return Err(SdkError(self._t(key, default, ctx), code=code))

    @staticmethod
    def _submitted(receipt: object) -> bool:
        return isinstance(receipt, Mapping) and receipt.get("submitted") is True

    @staticmethod
    def _candidate_data(candidate: SongCandidate) -> dict[str, object]:
        return {
            "song_id": candidate.song_id,
            "name": candidate.name,
            "artist": candidate.artist,
            "album": candidate.album,
        }


__all__ = ["NeteaseMusicPlugin"]
