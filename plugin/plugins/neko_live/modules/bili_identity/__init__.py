"""Resolve the v0.1 Bilibili identity fields."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import mimetypes
import socket
import ssl
import urllib.parse
from pathlib import Path
from typing import Any

from ...core.contracts import ViewerEvent, ViewerIdentity
from .._base import BaseModule


class _ResolvedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, resolved_ip: str, *, port: int, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)


class _ResolvedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, resolved_ip: str, *, port: int, timeout: float) -> None:
        context = ssl.create_default_context()
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._resolved_ip, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


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
        if self.ctx is not None:
            avatar_analysis_enabled = bool(
                getattr(self.ctx.config, "avatar_analysis_enabled", True)
            )
            live_avatar_roast_enabled = bool(
                getattr(self.ctx.config, "avatar_roast_enabled", True)
            ) or event.source not in {"live_danmaku", "manual_live_simulation"}
            if not avatar_analysis_enabled or not live_avatar_roast_enabled:
                return identity
        if not avatar_url or identity.is_default_avatar:
            return identity
        cached = self.ctx.avatar_cache.get(avatar_url) if self.ctx else None
        if cached:
            data, mime = cached
            usable, animated = self._inspect_avatar(data)
            if usable:
                identity.avatar_bytes = data
                identity.avatar_mime = mime
                identity.is_animated_avatar = animated
            return identity
        timeout = self.ctx.config.avatar_fetch_timeout_seconds if self.ctx else 8
        try:
            data, mime = await asyncio.to_thread(self._fetch_avatar, avatar_url, timeout)
            if data:
                usable, animated = self._inspect_avatar(data)
                if not usable:
                    raise ValueError("avatar_decode_failed")
                identity.avatar_bytes = data
                identity.avatar_mime = mime
                identity.is_animated_avatar = animated
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
    def _inspect_avatar(data: bytes | None) -> tuple[bool, bool]:
        """Return (usable_for_vision, animated). Decode failures disable vision."""
        if not data:
            return False, False
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                im.load()
                return True, bool(getattr(im, "is_animated", False))
        except Exception:
            return False, False

    @staticmethod
    def _fetch_avatar(url: str, timeout: float) -> tuple[bytes, str]:
        if url == "neko-live://fixtures/demo-avatar":
            return BiliIdentityModule._load_demo_avatar()
        parsed, resolved_ip, port = BiliIdentityModule._resolve_avatar_endpoint(url)
        connection = BiliIdentityModule._open_avatar_connection(parsed, resolved_ip, port, timeout)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        host_header = parsed.netloc
        connection.request(
            "GET",
            path,
            headers={
                "Host": host_header,
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 NEKO-Roast/0.1",
            },
        )
        try:
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ValueError("avatar_redirect_not_allowed")
            if response.status >= 400:
                raise ValueError("avatar_fetch_failed_status")
            data = response.read(2 * 1024 * 1024)
            content_type = response.getheader("content-type") or ""
        finally:
            connection.close()
        mime = content_type.split(";", 1)[0].strip()
        if not mime:
            mime = mimetypes.guess_type(url)[0] or "image/png"
        return data, mime

    @staticmethod
    def _resolve_avatar_endpoint(url: str) -> tuple[urllib.parse.ParseResult, str, int]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("avatar_url_scheme_not_allowed")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("avatar_url_host_required")
        lowered = hostname.lower()
        if lowered == "localhost" or lowered.endswith(".localhost"):
            raise ValueError("avatar_url_host_not_allowed")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = [item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)]
        except OSError as exc:
            raise ValueError("avatar_url_host_unresolved") from exc
        if not addresses:
            raise ValueError("avatar_url_host_unresolved")
        for address in set(addresses):
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError("avatar_url_host_not_allowed")
        return parsed, addresses[0], port

    @staticmethod
    def _validate_avatar_url(url: str) -> None:
        BiliIdentityModule._resolve_avatar_endpoint(url)

    @staticmethod
    def _open_avatar_connection(
        parsed: urllib.parse.ParseResult, resolved_ip: str, port: int, timeout: float
    ) -> http.client.HTTPConnection:
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("avatar_url_host_required")
        if parsed.scheme == "https":
            return _ResolvedHTTPSConnection(hostname, resolved_ip, port=port, timeout=timeout)
        return _ResolvedHTTPConnection(hostname, resolved_ip, port=port, timeout=timeout)

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
