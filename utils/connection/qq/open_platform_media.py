"""QQ Open Platform rich media: image upload and image sending.

Sending an image over the Open Platform is two requests, not one: upload the bytes
to get a ``file_info``, then send a ``msg_type=7`` message carrying it. Both halves
are QQ-platform-specific, so they live here next to
:mod:`utils.connection.qq.open_platform` rather than in the platform-neutral
``base`` layer.

Two upload protocols coexist on this platform:

1. **Legacy direct upload** -- ``POST /v2/{scope}/{id}/files`` with
   ``file_type`` / ``file_name`` / ``file_size`` / ``mime_type``, answering with an
   ``upload_url`` to ``PUT`` the bytes to. This is what the connection used to do
   for group images; the current platform docs no longer list those request fields,
   and a live run on 2026-09-26 showed the group flow failing on it (the image
   silently degraded to a plain text placeholder).
2. **Documented upload** -- either a **URL upload** (hand the platform an http(s)
   address and let it fetch), or a **chunked upload**
   (``upload_prepare`` -> per-part ``PUT`` + ``upload_part_finish`` -> merge).
   A local file cannot use the URL flow.

So for local files both are attempted, legacy first: that keeps the previously
working deployment working, and whichever succeeds is named in the log, which is how
"which protocol is still alive" gets answered without guessing. A live run on
2026-09-27 reproduced it -- the log recorded the legacy attempt getting no
``file_info`` and the chunked upload then succeeding, so the chunked path is the live
one.

Shape
-----

The actions sit on :class:`QQOpenPlatformMediaMixin`, the same way the
NapCat / go-cqhttp extensions sit on ``NapCatActionsMixin``: the connection class
stays about the protocol, and a platform's extras stay in one place next to it.

Anything that needs these actions only has to hold the connection object, so a
consumer that owns its own connection -- or holds one the host handed it -- can call
them without importing this module. Failure returns ``""`` / ``None`` instead of
raising: the caller decides how to degrade (the group and private message paths in
``open_platform`` fall back to a plain text placeholder).

Dependencies
------------

Connection members only: ``_http``, ``_API_BASE``, ``_ensure_token()``,
``_auth_headers()``, ``logger``, and ``record_sent_message_id()`` for the send half.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Any, Optional

#: Platform media type for an image.
FILE_TYPE_IMAGE = 1

#: Soft image limit. Past this the platform stores the upload as a "file" instead of
#: an image; this module refuses rather than silently changing what it sends.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: ``upload_prepare`` wants ``md5_10m``: the MD5 of the first 10002432 bytes.
_MD5_10M_BYTES = 10_002_432


def _local_path(source: str) -> str:
    """The local path behind ``source`` (a ``file://`` URI is unwrapped)."""
    text = str(source or "").strip()
    if text.startswith("file://"):
        text = text[7:]
    return text


def _read_source(source: str) -> tuple[bytes, str]:
    """Read a local file -> ``(bytes, file_name)``; unreadable is ``(b"", "")``."""
    path = _local_path(source)
    if not path or not os.path.isfile(path):
        return b"", ""
    with open(path, "rb") as handle:
        return handle.read(), os.path.basename(path)


def _digests(payload: bytes) -> dict[str, str]:
    """The three digests ``upload_prepare`` asks for, from one pass over the bytes."""
    return {
        "md5": hashlib.md5(payload).hexdigest(),
        "sha1": hashlib.sha1(payload).hexdigest(),
        "md5_10m": hashlib.md5(payload[:_MD5_10M_BYTES]).hexdigest(),
    }


def _media_error(data: dict[str, Any]) -> str:
    """The platform's error envelope, when the answer carries one.

    Success answers either hold the field the caller wants (``file_info`` / ``id``) or
    nothing at all, so only a **non-zero** ``code`` / ``err_code`` counts as an error:
    a missing code is not one. Returns "" when the answer looks fine.
    """
    for key in ("code", "err_code"):
        if key not in data:
            continue
        value = data.get(key)
        if str(value).strip() not in ("", "0", "None"):
            return str(data.get("message") or data.get("msg") or f"{key}={value}")
    return ""


class QQOpenPlatformMediaMixin:
    """Rich-media actions for ``QQOpenPlatformConnection``.

    Every method is written against ``self`` as the connection object, so the mixin
    also works on any class providing the members listed in the module docstring.
    """

    # ── transport plumbing ─────────────────────────────────────────────

    def _media_log(self, level: str, message: str) -> None:
        """Log through the connection's logger when it has one (never raises)."""
        logger = getattr(self, "logger", None)
        if logger is None:
            return
        try:
            getattr(logger, level, logger.info)(f"[QQOpenPlatform] {message}")
        except Exception:
            pass

    def _media_api_base(self) -> str:
        return str(getattr(self, "_API_BASE", "") or "").rstrip("/")

    async def _media_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Authenticated POST returning parsed JSON; a non-dict answer is ``{}``.

        A non-2xx answer **raises** (``httpx.HTTPStatusError``) rather than looking like an
        empty result. For an upload step that difference is the whole ballgame: "the part
        was accepted" and "the part was rejected" decide whether merging the chunks is
        allowed to run, and a merge over a rejected part stores a truncated file while the
        platform still answers with a normal-looking ``file_info``. Callers that only want
        an optional field keep their own ``try`` around this (``upload_image`` does).
        """
        response = await self._http.post(
            f"{self._media_api_base()}{path}", json=body, headers=self._auth_headers(),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    # ── upload protocols ───────────────────────────────────────────────

    async def _media_upload_by_url(
        self, *, scope: str, owner_id: str, url: str, file_type: int,
    ) -> str:
        """Documented URL upload: the platform fetches and stores the address."""
        data = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {"file_type": file_type, "url": url, "srv_send_msg": False},
        )
        return str(data.get("file_info") or "")

    async def _media_upload_chunked(
        self, *, scope: str, owner_id: str, payload: bytes, file_name: str, file_type: int,
    ) -> str:
        """Documented chunked upload: prepare -> per-part PUT + finish -> merge."""
        digests = _digests(payload)
        prepare = await self._media_post(
            f"/v2/{scope}/{owner_id}/upload_prepare",
            {
                "file_type": file_type,
                "file_size": str(len(payload)),
                "file_name": file_name,
                **digests,
            },
        )
        upload_id = str(prepare.get("upload_id") or "")
        parts = prepare.get("parts")
        if not upload_id or not isinstance(parts, list) or not parts:
            return ""

        mime_type = mimetypes.guess_type(file_name)[0] or "image/png"
        ordered = sorted(
            (p for p in parts if isinstance(p, dict)),
            key=lambda p: int(p.get("index") or 0),
        )
        offset = 0
        for part in ordered:
            try:
                index = int(part.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            try:
                size = int(part.get("block_size") or 0)
            except (TypeError, ValueError):
                size = 0
            chunk = payload[offset:offset + size] if size > 0 else payload[offset:]
            presigned = str(part.get("presigned_url") or "")
            if not chunk or not presigned:
                return ""

            # Every part has to be **confirmed** before the merge is allowed to run: a
            # rejected PUT or a rejected finish would otherwise leave a hole in the file,
            # and the merge below still answers with a normal-looking ``file_info``.
            try:
                response = await self._http.put(
                    presigned, content=chunk, headers={"Content-Type": mime_type},
                )
                response.raise_for_status()
            except Exception as exc:
                self._media_log(
                    "warning", f"分片第 {index} 片上传失败（{len(chunk)} 字节），放弃合并: {exc}",
                )
                return ""
            try:
                finished = await self._media_post(
                    f"/v2/{scope}/{owner_id}/upload_part_finish",
                    {
                        "upload_id": upload_id,
                        "part_index": index,
                        "block_size": str(len(chunk)),
                        "md5": hashlib.md5(chunk).hexdigest(),
                    },
                )
            except Exception as exc:
                self._media_log("warning", f"分片第 {index} 片收尾失败，放弃合并: {exc}")
                return ""
            problem = _media_error(finished)
            if problem:
                self._media_log("warning", f"分片第 {index} 片被平台拒绝，放弃合并: {problem}")
                return ""

            offset += len(chunk)

        if offset != len(payload):
            # The part list did not cover the whole file: merging would store a
            # truncated file and the platform will not flag it. Skipping this send is
            # better than uploading a broken image and reporting success.
            self._media_log("warning", f"分片只覆盖 {offset}/{len(payload)} 字节，放弃合并")
            return ""

        merged = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {"file_type": file_type, "upload_id": upload_id, "srv_send_msg": False, "file_name": file_name},
        )
        problem = _media_error(merged)
        if problem:
            self._media_log("warning", f"合并分片失败: {problem}")
            return ""
        return str(merged.get("file_info") or "")

    async def _media_upload_legacy(
        self, *, scope: str, owner_id: str, payload: bytes, file_name: str, file_type: int,
    ) -> str:
        """Legacy direct upload: apply for an ``upload_url``, then PUT."""
        mime_type = mimetypes.guess_type(file_name)[0] or "image/png"
        data = await self._media_post(
            f"/v2/{scope}/{owner_id}/files",
            {
                "file_type": file_type,
                "file_name": file_name,
                "file_size": len(payload),
                "mime_type": mime_type,
            },
        )
        upload_url = str(data.get("upload_url") or "")
        if not upload_url:
            return ""
        response = await self._http.put(
            upload_url, content=payload, headers={"Content-Type": mime_type},
        )
        response.raise_for_status()
        try:
            file_info = str((response.json() or {}).get("file_info") or "")
        except Exception:
            file_info = ""
        return file_info or str(data.get("file_info") or "")

    # ── public operations ──────────────────────────────────────────────

    async def upload_image(self, *, scope: str, owner_id: str, source: str) -> str:
        """Upload one image into ``scope`` (``"groups"`` / ``"users"``), return ``file_info``.

        ``scope`` is the platform's own isolation: an upload made through the private
        interface can only be sent privately, and the other way round, so callers must
        pass the one matching where the image goes. Failure returns ``""`` and leaves
        the degradation choice to the caller.
        """
        url = str(source or "").strip()
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            try:
                await self._ensure_token()
                file_info = await self._media_upload_by_url(
                    scope=scope, owner_id=owner_id, url=url, file_type=FILE_TYPE_IMAGE,
                )
            except Exception as exc:
                self._media_log("warning", f"图片 URL 上传异常: {exc}")
                return ""
            if file_info:
                self._media_log("info", f"图片上传成功(url): {file_info[:24]}")
            else:
                self._media_log("warning", "图片 URL 上传失败")
            return file_info

        try:
            payload, file_name = _read_source(url)
        except Exception as exc:
            self._media_log("warning", f"图片读取失败: {exc}")
            return ""
        if not payload:
            self._media_log("warning", f"图片文件不存在或为空: {_local_path(url)}")
            return ""
        if len(payload) > MAX_IMAGE_BYTES:
            self._media_log(
                "warning",
                f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: {len(payload)} 字节",
            )
            return ""

        try:
            await self._ensure_token()
        except Exception as exc:
            self._media_log("warning", f"取 token 失败，无法上传图片: {exc}")
            return ""

        # Both protocols, legacy first: never worse than the behaviour that shipped,
        # and the log says which one worked.
        for label, attempt in (
            ("直传", self._media_upload_legacy),
            ("分片", self._media_upload_chunked),
        ):
            try:
                file_info = await attempt(
                    scope=scope, owner_id=owner_id,
                    payload=payload, file_name=file_name, file_type=FILE_TYPE_IMAGE,
                )
            except Exception as exc:
                self._media_log("warning", f"图片{label}上传异常: {exc}")
                continue
            if file_info:
                self._media_log("info", f"图片上传成功({label}): {file_info[:24]}")
                return file_info
            self._media_log("warning", f"图片{label}上传未拿到 file_info")
        return ""

    async def send_private_image(
        self, user_id: str, source: str, *,
        content: str = "", reply_message_id: str = "", record_sent: bool = True,
    ) -> Optional[str]:
        """Send one image to a private chat (``msg_type=7`` + ``media.file_info``).

        Returns the message id, or ``None`` at any failure for the caller to degrade
        (``send_private_message_segments`` turns that into a plain text placeholder).
        """
        target = str(user_id or "").strip()
        if not target:
            return None
        file_info = await self.upload_image(scope="users", owner_id=target, source=source)
        if not file_info:
            return None
        body: dict[str, Any] = {"msg_type": 7, "media": {"file_info": file_info}}
        text = str(content or "").strip()
        if text:
            body["content"] = text
        reply_id = str(reply_message_id or "").strip()
        if reply_id:
            body["msg_id"] = reply_id
        try:
            data = await self._media_post(f"/v2/users/{target}/messages", body)
        except Exception as exc:
            self._media_log("warning", f"发送单聊图片失败: {exc}")
            return None
        message_id = str(data.get("id") or "")
        if message_id and record_sent:
            try:
                self.record_sent_message_id(message_id)
            except Exception:
                pass
        return message_id or None
