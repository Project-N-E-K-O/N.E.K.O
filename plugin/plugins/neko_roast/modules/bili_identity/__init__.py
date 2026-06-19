"""Resolve the v0.1 Bilibili identity fields."""

from __future__ import annotations

import asyncio
import mimetypes
import urllib.request
from pathlib import Path
from typing import Any

from ...core.contracts import ViewerEvent, ViewerIdentity
from .._base import BaseModule


class BiliIdentityModule(BaseModule):
    id = "bili_identity"
    title = "B站身份解析"

    async def resolve(self, event: ViewerEvent) -> ViewerIdentity:
        uid = str(event.uid or "").strip()
        nickname = str(event.nickname or "").strip()
        avatar_url = str(event.avatar_url or "").strip()
        display_name = nickname
        email = ""
        pendant = ""
        errors: list[str] = []

        if uid and uid.isdigit() and (not nickname or not avatar_url):
            try:
                profile = await self._fetch_profile_by_uid(uid)
                display_name = str(profile.get("name") or nickname or uid).strip()
                email = str(profile.get("email") or profile.get("mail") or "").strip()
                pendant = str(profile.get("pendant") or "").strip()
                nickname = nickname or display_name or uid
                avatar_url = avatar_url or str(profile.get("face") or "").strip()
                if self.ctx:
                    self.ctx.audit.record("bili_identity_fetched", "bili identity fetched", detail={"uid": uid})
            except Exception as exc:
                errors.append(f"profile_fetch_failed: {type(exc).__name__}")
                if self.ctx:
                    self.ctx.audit.record(
                        "bili_identity_fetch_failed",
                        f"profile fetch failed: {type(exc).__name__}",
                        level="warning",
                        detail={"uid": uid},
                    )

        nickname = nickname or uid
        display_name = display_name or nickname
        identity = ViewerIdentity(
            uid=uid,
            nickname=nickname,
            name=display_name,
            email=email,
            avatar_url=avatar_url,
            source_url=f"https://space.bilibili.com/{uid}" if uid else "",
            fetched=not errors,
            error="; ".join(errors),
            is_default_avatar=bool(avatar_url) and "noface" in avatar_url.lower(),
            pendant=pendant,
        )
        if not avatar_url:
            return identity
        cached = self.ctx.avatar_cache.get(avatar_url) if self.ctx else None
        if cached:
            identity.avatar_bytes, identity.avatar_mime = cached
            identity.is_animated_avatar = self._detect_animated(identity.avatar_bytes)
            return identity
        timeout = self.ctx.config.avatar_fetch_timeout_seconds if self.ctx else 8
        try:
            data, mime = await asyncio.to_thread(self._fetch_avatar, avatar_url, timeout)
            if data:
                identity.avatar_bytes = data
                identity.avatar_mime = mime
                identity.is_animated_avatar = self._detect_animated(data)
                ctx = self.ctx
                if ctx is not None:
                    ctx.avatar_cache.put(avatar_url, data, mime)
        except Exception as exc:
            identity.fetched = False
            avatar_error = f"avatar_fetch_failed: {type(exc).__name__}"
            identity.error = "; ".join([item for item in [identity.error, avatar_error] if item])
            ctx = self.ctx
            if ctx is not None:
                ctx.audit.record("avatar_fetch_failed", identity.error, level="warning", detail={"uid": uid})
        return identity

    async def _fetch_profile_by_uid(self, uid: str) -> dict[str, Any]:
        from bilibili_api import user

        # 登录态（若有）让 get_user_info 走登录会话，绕过 -352 风控、恢复头像抓取；未登录=匿名（同现状）。
        credential = getattr(self.ctx, "bili_credential", None) if self.ctx else None
        target = user.User(uid=int(uid), credential=credential)
        info = await target.get_user_info()
        pendant = info.get("pendant") if isinstance(info.get("pendant"), dict) else {}
        return {
            "uid": str(info.get("mid") or uid),
            "name": str(info.get("name") or ""),
            "email": str(info.get("email") or info.get("mail") or ""),
            "face": str(info.get("face") or ""),
            # 挂件/装扮（出框头像的来源）；无装扮时 name 为空字符串。
            "pendant": str(pendant.get("name") or "").strip(),
        }

    @staticmethod
    def _detect_animated(data: bytes | None) -> bool:
        """Best-effort：判断头像是否为动图（大会员动态头像），失败按静态处理。"""
        if not data:
            return False
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                return bool(getattr(im, "is_animated", False))
        except Exception:
            return False

    @staticmethod
    def _fetch_avatar(url: str, timeout: float) -> tuple[bytes, str]:
        if url == "neko-roast://fixtures/demo-avatar":
            return BiliIdentityModule._load_demo_avatar()
        request = urllib.request.Request(
            url,
            headers={
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 NEKO-Roast/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("content-type") or ""
        mime = content_type.split(";", 1)[0].strip()
        if not mime:
            mime = mimetypes.guess_type(url)[0] or "image/png"
        return data, mime

    @staticmethod
    def _load_demo_avatar() -> tuple[bytes, str]:
        plugin_root = Path(__file__).resolve().parents[2]
        png_path = plugin_root / "fixtures" / "demo_avatar.png"
        if png_path.is_file():
            return png_path.read_bytes(), "image/png"
        svg_path = plugin_root / "fixtures" / "demo_avatar.svg"
        return svg_path.read_bytes(), "image/svg+xml"

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "avatar_cache": self.ctx.avatar_cache.status() if self.ctx else {}}
