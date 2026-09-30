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

A legacy attempt that comes back without ``file_info`` -- or whose request the platform
rejects with a 4xx that is not about auth or rate limits -- is remembered for the rest of
the connection (``_legacy_upload_unsupported``): on 2026-09-27 the platform answered the
legacy request with nothing at all, so retrying it for every image only buys a
guaranteed-failing request per send. The per-protocol log line still fires the first time,
and ``connect()`` / a successful reconnect clear the flag, so a platform that brings the old
flow back is picked up again.

Sources
-------

``upload_image`` takes an http(s) URL (URL upload), a ``base64://`` payload (the OneBot
``image`` segment convention), or a local path / ``file://`` URI (chunked upload).

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

import asyncio
import base64
import binascii
import hashlib
import mimetypes
import os
import string
from typing import Any, NamedTuple, Optional
from urllib.parse import urlsplit
from urllib.request import url2pathname

#: Platform media type for an image.
FILE_TYPE_IMAGE = 1

#: Soft image limit. Past this the platform stores the upload as a "file" instead of
#: an image; this module refuses rather than silently changing what it sends.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

#: ``upload_prepare`` wants ``md5_10m``: the MD5 of the first 10002432 bytes.
_MD5_10M_BYTES = 10_002_432

_BASE64_PREFIX = "base64://"

#: How much of an image source a log line shows. A ``base64://`` source can be megabytes.
_LOG_SOURCE_CHARS = 80

#: 4xx answers that say nothing about whether the legacy upload protocol still exists:
#: a bad token, a missing permission, a timeout or a rate limit can hit any request.
_TRANSIENT_CLIENT_ERRORS = frozenset({401, 403, 408, 429})

#: Magic numbers -> extension, for naming decoded ``base64://`` bytes.
_IMAGE_MAGIC = (
    (bytes.fromhex("89504e47"), "png"),
    (bytes.fromhex("ffd8ff"), "jpg"),
    (b"GIF8", "gif"),
)


def _brief(source: str) -> str:
    """``source`` cut down for a log line."""
    text = str(source or "")
    if len(text) <= _LOG_SOURCE_CHARS:
        return text
    return f"{text[:_LOG_SOURCE_CHARS]}...({len(text)} chars)"


def _local_path(source: str) -> str:
    """The local path behind ``source`` (a ``file://`` URI is unwrapped).

    Parsed rather than prefix-stripped: ``file:///C:/x.png`` is a three-slash URI whose
    path is ``/C:/x.png``, and naively dropping ``file://`` leaves ``/C:/x.png`` -- not a
    path Windows can open.

    Percent-escapes are decoded **exactly once**, by ``url2pathname``: it already unquotes
    on both platforms (POSIX: it *is* ``unquote``; Windows: ``nturl2path`` decodes too), so
    unquoting here as well would turn ``a%2520b.png`` into ``a b.png`` and open the wrong
    file (or none).

    A drive-letter URI resolves the same way on every platform: Windows' ``url2pathname``
    answers ``C:\\x.png`` on its own, and on POSIX the leading slash is dropped here, so
    both hand the caller the same ``C:/x.png`` shape. Only the separator stays
    platform-native. A UNC-style ``file://host/share/x`` keeps the host.
    """
    text = str(source or "").strip()
    if not text.lower().startswith("file:"):
        return text
    parsed = urlsplit(text)
    path = url2pathname(parsed.path)
    host = parsed.netloc.strip()
    if host and host.lower() != "localhost":
        return f"//{host}{path}"
    if len(path) > 2 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
        # `/C:/...` -- a Windows drive letter behind the URI's leading slash.
        return path[1:]
    return path


class SourceFile(NamedTuple):
    """A local file ready to upload. ``digests`` is empty when nothing could be read."""

    payload: bytes
    file_name: str
    digests: dict[str, str]


def _read_source(source: str) -> SourceFile:
    """Read a local file -> ``SourceFile``; unreadable is an empty payload.

    Blocking on purpose: callers run it off the event loop (``asyncio.to_thread``) after
    checking the size, so a slow or huge file never stalls the loop or gets read just to
    be rejected.

    The digests are computed **here**, in the same worker thread: hashing up to 20MB
    (md5 + sha1 + the 10MB prefix) is tens of milliseconds, and doing it here is what
    keeps it off the event loop -- the caller already paid for moving this call off it.
    """
    path = _local_path(source)
    if not path or not os.path.isfile(path):
        return SourceFile(b"", "", {})
    with open(path, "rb") as handle:
        payload = handle.read()
    return SourceFile(payload, os.path.basename(path), _digests(payload))


def _image_file_name(payload: bytes) -> str:
    """A file name for decoded bytes, with the extension their magic number says."""
    for magic, extension in _IMAGE_MAGIC:
        if payload.startswith(magic):
            return f"image.{extension}"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image.webp"
    return "image.png"


def _base64_decoded_size(encoded: str) -> int:
    """How many bytes ``encoded`` decodes to, without decoding or copying it.

    Whitespace is not data (``_decode_base64_source`` drops it) and trailing ``=``
    padding stands for no bytes, so neither may count against the size limit: an image
    of exactly ``MAX_IMAGE_BYTES`` would otherwise be refused.
    """
    end = len(encoded)
    while end and encoded[end - 1] in string.whitespace:
        end -= 1
    padding = 0
    while end and padding < 2 and encoded[end - 1] == "=":
        end -= 1
        padding += 1
    whitespace = sum(encoded.count(ch) for ch in string.whitespace)
    return (len(encoded) - whitespace) * 3 // 4 - padding


def _decode_base64_source(encoded: str) -> SourceFile:
    """Decode a ``base64://`` payload -> ``SourceFile``; undecodable is an empty payload.

    Blocking for the same reason as ``_read_source`` (decoding and hashing megabytes), so
    callers run it off the event loop.
    """
    try:
        payload = base64.b64decode("".join(encoded.split()), validate=True)
    except (binascii.Error, ValueError):
        return SourceFile(b"", "", {})
    if not payload:
        return SourceFile(b"", "", {})
    return SourceFile(payload, _image_file_name(payload), _digests(payload))


def _digests(payload: bytes) -> dict[str, str]:
    """The three digests ``upload_prepare`` asks for, from one pass over the bytes."""
    return {
        "md5": hashlib.md5(payload).hexdigest(),
        "sha1": hashlib.sha1(payload).hexdigest(),
        "md5_10m": hashlib.md5(payload[:_MD5_10M_BYTES]).hexdigest(),
    }


def _positive_int(value: Any) -> int:
    """``value`` as a positive int, or 0 when it is missing / unparsable.

    The platform sends numbers as strings (``"8"``); a JSON float (``8.0``) is accepted
    too, because ``int("8.0")`` is a ``ValueError``.
    """
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _is_protocol_rejection(exc: BaseException) -> bool:
    """Whether ``exc`` is a 4xx that says the request shape itself is not accepted."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", 0)
    if not isinstance(status, int):
        return False
    return 400 <= status < 500 and status not in _TRANSIENT_CLIENT_ERRORS


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
        digests: Optional[dict[str, str]] = None,
    ) -> str:
        """Documented chunked upload: prepare -> per-part PUT + finish -> merge.

        ``digests`` comes from the caller when the file was read off the event loop
        already (``upload_image`` passes what ``_read_source`` computed); computing them
        here is the fallback for direct callers.
        """
        digests = digests or _digests(payload)
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
        # Sorted with the same parser the loop uses: `int(p.get("index"))` would raise on
        # `"1.0"` and turn a clean refusal into an opaque "upload failed" exception.
        ordered = sorted(
            (p for p in parts if isinstance(p, dict)),
            key=lambda p: _positive_int(p.get("index")),
        )
        if not ordered:
            self._media_log("warning", "分片上传: upload_prepare 返回的 parts 里没有可用分片，放弃上传")
            return ""
        # The part size may only be stated **once, at the top level** of the prepare
        # answer. Falling back to 0 there is not a harmless default: the first part would
        # then slice `payload[0:]` (the whole file), and the second part would slice an
        # empty chunk and abort the merge -- a multi-part upload could never succeed.
        fallback_size = _positive_int(prepare.get("block_size"))
        # Log the part shape the platform handed us: whether indices are 0- or 1-based,
        # and which level carries the part size, can only be confirmed against a real
        # response -- this line is where the next live run answers both.
        self._media_log(
            "info",
            f"分片上传: {len(ordered)} 片，首片 index={ordered[0].get('index')}，"
            f"每片 {_positive_int(ordered[0].get('block_size')) or fallback_size} 字节，"
            f"文件 {len(payload)} 字节",
        )
        offset = 0
        for part in ordered:
            index = _positive_int(part.get("index"))
            # `index` is only used for ordering and for echoing back in
            # `upload_part_finish`, so a 0-based and a 1-based platform both work; the
            # fixture and the real response do not have to agree on the base.
            size = _positive_int(part.get("block_size")) or fallback_size
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
        """Legacy direct upload: apply for an ``upload_url``, then PUT.

        A 4xx on the *apply* request that is not about auth or rate limits returns ``""``
        instead of raising: it means the platform no longer accepts this request shape,
        which is the same protocol-level answer as a reply without ``upload_url`` and has
        to switch the protocol off the same way (``upload_image``). A failing PUT still
        raises -- that is about this file, not about the protocol.
        """
        mime_type = mimetypes.guess_type(file_name)[0] or "image/png"
        try:
            data = await self._media_post(
                f"/v2/{scope}/{owner_id}/files",
                {
                    "file_type": file_type,
                    "file_name": file_name,
                    "file_size": len(payload),
                    "mime_type": mime_type,
                },
            )
        except Exception as exc:
            if not _is_protocol_rejection(exc):
                raise
            self._media_log("warning", f"图片直传申请被平台拒绝: {exc}")
            return ""
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

    async def upload_image(
        self, *, scope: str, owner_id: str, source: str, token_checked: bool = False,
    ) -> str:
        """Upload one image into ``scope`` (``"groups"`` / ``"users"``), return ``file_info``.

        ``scope`` is the platform's own isolation: an upload made through the private
        interface can only be sent privately, and the other way round, so callers must
        pass the one matching where the image goes. Failure returns ``""`` and leaves
        the degradation choice to the caller.

        ``token_checked=True`` says the caller (the connection's own send paths) has just
        ensured a token, so this call does not ask for one again.
        """
        url = str(source or "").strip()
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            try:
                if not token_checked:
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

        if url.startswith(_BASE64_PREFIX):
            # The OneBot `image` segment convention: the bytes travel inline. The
            # decoded size is computed from the text first, so an oversized payload is
            # refused without being decoded. Text over 4x the limit cannot be a valid
            # image under it, and skips even the whitespace count.
            encoded = url[len(_BASE64_PREFIX):]
            shown = "base64 图片"
            if (
                len(encoded) > 4 * MAX_IMAGE_BYTES
                or _base64_decoded_size(encoded) > MAX_IMAGE_BYTES
            ):
                self._media_log(
                    "warning",
                    f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: "
                    f"base64 {len(encoded)} 字符",
                )
                return ""
            reader, reader_arg = _decode_base64_source, encoded
        else:
            # Size first, bytes later: the soft limit has to be able to reject a file
            # *without* pulling it into memory, and the read itself must not run on the
            # event loop (a local image can be arbitrarily large or on a slow volume).
            path = _local_path(url)
            shown = _brief(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if size <= 0:
                self._media_log("warning", f"图片文件不存在或为空: {shown}")
                return ""
            if size > MAX_IMAGE_BYTES:
                self._media_log(
                    "warning",
                    f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: {size} 字节",
                )
                return ""
            reader, reader_arg = _read_source, url

        try:
            source_file = await asyncio.to_thread(reader, reader_arg)
        except Exception as exc:
            self._media_log("warning", f"图片读取失败: {exc}")
            return ""
        payload, file_name, digests = source_file
        if not payload:
            self._media_log("warning", f"图片文件不存在、为空或无法解码: {shown}")
            return ""
        if len(payload) > MAX_IMAGE_BYTES:
            # Second line of defence: the file can grow (or be swapped) between the stat
            # above and the read.
            self._media_log(
                "warning",
                f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 软限制，放弃上传: {len(payload)} 字节",
            )
            return ""

        if not token_checked:
            try:
                await self._ensure_token()
            except Exception as exc:
                self._media_log("warning", f"取 token 失败，无法上传图片: {exc}")
                return ""

        # Both protocols, legacy first: never worse than the behaviour that shipped,
        # and the log says which one worked. A legacy attempt that answered without
        # `file_info` is remembered (see `_legacy_upload_unsupported`): retrying a
        # protocol the platform already stopped answering only spends a request per image.
        #
        # Only the chunked protocol takes `digests` -- it is the one that puts them in
        # `upload_prepare`. The legacy request has its own field set and rejects unknown
        # kwargs, so each attempt carries its own extras.
        attempts: list[tuple[str, Any, dict[str, Any]]] = [
            ("分片", self._media_upload_chunked, {"digests": digests}),
        ]
        if not getattr(self, "_legacy_upload_unsupported", False):
            attempts.insert(0, ("直传", self._media_upload_legacy, {}))
        for label, attempt, extra in attempts:
            try:
                file_info = await attempt(
                    scope=scope, owner_id=owner_id,
                    payload=payload, file_name=file_name, file_type=FILE_TYPE_IMAGE,
                    **extra,
                )
            except Exception as exc:
                self._media_log("warning", f"图片{label}上传异常: {exc}")
                continue
            if file_info:
                self._media_log("info", f"图片上传成功({label}): {file_info[:24]}")
                return file_info
            self._media_log("warning", f"图片{label}上传未拿到 file_info")
            if label == "直传":
                # Protocol-level failure, not a per-file one: the answer carried no
                # `upload_url` at all (09-27 live log), or the apply request was refused
                # outright (see `_media_upload_legacy`). Remember it so the next image
                # does not pay for the same guaranteed-failing request; `connect()` and
                # a successful reconnect clear the flag, so a platform that brings the
                # old flow back is picked up again.
                self._legacy_upload_unsupported = True
                self._media_log("info", "直传协议已不再返回 file_info，本连接后续只用分片上传")
        return ""

    async def send_private_image(
        self, user_id: str, source: str, *,
        content: str = "", reply_message_id: str = "", record_sent: bool = True,
    ) -> Optional[str]:
        """Send one image to a private chat (``msg_type=7`` + ``media.file_info``).

        Returns the message id, or ``None`` at any failure for the caller to degrade
        (``send_private_message_segments`` turns that into a plain text placeholder).

        Consumers should prefer the **segment** API (``send_private_message_segments``):
        this method is not the uniform one. Its OneBot twin takes ``(user_id,
        image_data)`` with no keyword arguments at all, and it records the sent id by
        default where OneBot's does not -- so calling it by name across connectors is a
        signature mismatch waiting to happen (see ``tests/unit/test_open_platform_media``
        and the plugin's ``media_seam``).
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
            # Replies to the same message need distinct `msg_seq`s, or the platform
            # rejects the later ones as duplicates.
            next_seq = getattr(self, "_next_msg_seq", None)
            if callable(next_seq):
                body["msg_seq"] = next_seq(reply_id)
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
