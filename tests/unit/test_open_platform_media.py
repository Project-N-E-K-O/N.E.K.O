"""QQ Open Platform rich media: the two-step upload, and the two send paths.

Sending an image on the Open Platform is two requests, not one: upload the bytes to
get a ``file_info``, then send a ``msg_type=7`` message carrying it. Three things are
pinned here, because getting any of them wrong fails the same silent way (the image
degrades into a text placeholder and nothing raises):

1. **The upload protocol order.** The repository's original code implemented only the
   legacy direct upload (``POST /v2/{scope}/{id}/files`` with
   ``file_type``/``file_name``/``file_size``/``mime_type`` -> ``upload_url`` -> PUT),
   which the current platform docs no longer list; a live run on 2026-09-26 showed it
   failing on the group flow, and on 2026-09-27 the same run logged the legacy attempt
   losing to the chunked one ("legacy upload got no file_info" -> "uploaded
   (chunked)"). So both protocols are tried, legacy first, and a request-shape
   assertion here is what keeps "which one is alive" answerable.
2. **The upload scope.** The platform isolates the two interfaces: an upload made
   through ``/v2/users/{id}/...`` can only be sent privately, and the other way round.
   Cross-using them gets a stored file that cannot be sent.
3. **The wiring.** A partial chunk list must not be merged (that stores a truncated
   file and still returns a normal-looking ``file_info``), and both the group and the
   private segment paths must reach this upload -- the private path used to never
   upload at all.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

import utils.connection.onebot as onebot
from utils.connection.base import ConnectionBase
from utils.connection.qq.open_platform import QQOpenPlatformConnection
from utils.connection.qq.open_platform_media import (
    MAX_IMAGE_BYTES,
    QQOpenPlatformMediaMixin,
)

API_BASE = "https://api.sgroup.qq.com"


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeHTTP:
    """Answers through ``responder(method, url, body)`` and records every call."""

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[tuple[str, str, object]] = []

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        return _Response(self._responder("POST", url, json))

    async def put(self, url, content=None, headers=None):
        self.calls.append(("PUT", url, content))
        return _Response(self._responder("PUT", url, content))

    def posts(self):
        return [(url, body) for method, url, body in self.calls if method == "POST"]

    def puts(self):
        return [(url, body) for method, url, body in self.calls if method == "PUT"]


def _make_connection(responder, logger=None):
    """A real connection with a fake HTTP client and an already-valid token."""
    connection = QQOpenPlatformConnection(app_id="a", client_secret="b", logger=logger)
    connection._http = _FakeHTTP(responder)
    connection._access_token = "fake-token"
    connection._token_expires_at = float("inf")  # _ensure_token() is then a no-op
    return connection


def _run(coro):
    return asyncio.run(coro)


def _legacy_ok(**_):
    return {"upload_url": "https://cos.example/put/1", "file_info": "FI-legacy"}


def _legacy_then_nothing(method, url, body):
    """Legacy upload yields no ``upload_url``; the chunked path works (two 8-byte parts)."""
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [
                {"index": 0, "presigned_url": "https://cos.example/part/0", "block_size": "8"},
                {"index": 1, "presigned_url": "https://cos.example/part/1", "block_size": "8"},
            ],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    if url.endswith("/files") and body.get("upload_id"):
        return {"file_info": "FI-chunked"}
    return {}


def _truncated_parts(method, url, body):
    """Same as above, but the part list covers only half of the 16-byte file."""
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [{"index": 0, "presigned_url": "https://cos.example/part/0", "block_size": "8"}],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    return {}


def _chunked_with_ids(method, url, body):
    """Chunked upload plus a message id, so the send half is observable too."""
    if url.endswith("/messages"):
        return {"id": "MID-group" if "/groups/" in url else "MID-private"}
    return _legacy_then_nothing(method, url, body)


# ── URL upload: the platform fetches the address, no bytes leave the host ──────


def test_remote_url_goes_through_the_documented_url_upload():
    def responder(method, url, body):
        return {"id": "MID-1"} if url.endswith("/messages") else {"file_info": "FI-url"}

    connection = _make_connection(responder)

    message_id = _run(connection.send_private_image(
        "USER1", "https://cdn.example/a.png", content="给你看",
    ))

    posts = connection._http.posts()
    assert posts[0] == (
        f"{API_BASE}/v2/users/USER1/files",
        {"file_type": 1, "url": "https://cdn.example/a.png", "srv_send_msg": False},
    ), posts[0]
    assert posts[1] == (
        f"{API_BASE}/v2/users/USER1/messages",
        {"msg_type": 7, "media": {"file_info": "FI-url"}, "content": "给你看"},
    ), posts[1]
    # Nothing to PUT: the count of PUTs is what distinguishes URL from byte upload.
    assert connection._http.puts() == []
    assert message_id == "MID-1"
    # The sent id must be recorded, or reply detection cannot tell the bot's own
    # message from a user's.
    assert list(connection.sent_message_ids) == ["MID-1"]


def test_scope_is_users_for_private_and_groups_for_group_upload():
    private = _make_connection(lambda method, url, body: {"file_info": "FI"})
    _run(private.upload_image(scope="users", owner_id="U1", source="https://cdn.example/a.png"))
    assert private._http.posts()[0][0] == f"{API_BASE}/v2/users/U1/files"

    group = _make_connection(lambda method, url, body: {"file_info": "FI"})
    _run(group.upload_image(scope="groups", owner_id="G1", source="https://cdn.example/a.png"))
    assert group._http.posts()[0][0] == f"{API_BASE}/v2/groups/G1/files"


# ── local files: legacy direct upload first, documented chunked upload second ──


def test_local_file_tries_the_legacy_direct_upload_first(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 32)
    connection = _make_connection(
        lambda method, url, body: _legacy_ok() if method == "POST" else {"file_info": "FI-legacy"},
    )

    file_info = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))

    assert file_info == "FI-legacy"
    urls = [url for url, _ in connection._http.posts()]
    assert urls[0].endswith("/v2/groups/G1/files")
    assert not any(url.endswith("/upload_prepare") for url in urls), urls
    assert connection._http.puts()[0][0] == "https://cos.example/put/1"


def test_local_file_falls_back_to_the_documented_chunked_upload(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    connection = _make_connection(_legacy_then_nothing)

    file_info = _run(connection.upload_image(scope="users", owner_id="U1", source=str(sticker)))

    assert file_info == "FI-chunked"
    urls = [url for url, _ in connection._http.posts()]
    assert urls[0].endswith("/v2/users/U1/files")
    assert urls[1].endswith("/v2/users/U1/upload_prepare")
    assert urls[2].endswith("/v2/users/U1/upload_part_finish")
    assert urls[3].endswith("/v2/users/U1/upload_part_finish")
    assert urls[4].endswith("/v2/users/U1/files")
    prepare = connection._http.posts()[1][1]
    assert set(prepare) == {"file_type", "file_size", "file_name", "md5", "sha1", "md5_10m"}
    assert prepare["file_size"] == "16"
    # Each PUT must carry the number of bytes its part_finish reported: the server
    # validates against that, and two short parts would merge into a broken file.
    put_sizes = [len(body) for _, body in connection._http.puts()]
    finish_sizes = [
        body["block_size"] for url, body in connection._http.posts()
        if url.endswith("/upload_part_finish")
    ]
    assert put_sizes == [8, 8], put_sizes
    assert finish_sizes == ["8", "8"] == [str(size) for size in put_sizes]


def test_partial_part_list_is_refused_instead_of_merging_a_truncated_file(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    connection = _make_connection(_truncated_parts)

    assert _run(connection.upload_image(scope="users", owner_id="U1", source=str(sticker))) == ""
    assert not any(
        url.endswith("/files") and body.get("upload_id")
        for url, body in connection._http.posts()
    ), "a short part list must not reach the merge step"


# ── failures degrade instead of raising ───────────────────────────────────────


def test_upload_failure_returns_empty_and_the_sender_posts_no_message(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 8)
    connection = _make_connection(lambda method, url, body: {})

    assert _run(connection.send_private_image("U1", str(sticker))) is None
    assert not any(url.endswith("/messages") for url, _ in connection._http.posts())


def test_missing_local_file_never_touches_the_network(tmp_path):
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})

    assert _run(connection.send_private_image("U1", str(tmp_path / "nope.png"))) is None
    assert connection._http.calls == []


def test_oversized_image_is_refused_before_uploading(tmp_path, monkeypatch):
    sticker = tmp_path / "big.png"
    sticker.write_bytes(b"b" * 64)
    monkeypatch.setattr(
        "utils.connection.qq.open_platform_media.MAX_IMAGE_BYTES", 32,
    )
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == ""
    assert connection._http.calls == []
    assert MAX_IMAGE_BYTES > 32, "the module constant is the real limit; the test only lowered it"


def test_token_failure_does_not_raise(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"c" * 8)
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})

    async def _boom():
        raise RuntimeError("token service down")

    connection._ensure_token = _boom
    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == ""


# ── the two segment send paths must reach this upload ─────────────────────────


def test_the_group_image_segment_uploads_through_the_media_path(tmp_path):
    """The group path used to inline the legacy protocol only; it must not come back."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    connection = _make_connection(_chunked_with_ids)

    message_id = _run(connection.send_group_message_segments(
        "G-openid", [{"type": "image", "data": {"file": str(sticker)}}], record_sent=False,
    ))

    assert message_id == "MID-group"
    posts = connection._http.posts()
    assert posts[0][0].endswith("/v2/groups/G-openid/files")
    assert any(url.endswith("/upload_prepare") for url, _ in posts), "the chunked fallback never ran"
    sent = [body for url, body in posts if url.endswith("/messages")]
    assert sent == [{"msg_type": 7, "media": {"file_info": "FI-chunked"}}], sent


def test_the_private_image_segment_sends_msg_type_7(tmp_path):
    """A private sticker used to arrive as a literal text placeholder instead."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    connection = _make_connection(_chunked_with_ids)

    message_id = _run(connection.send_private_message_segments(
        "U-openid", [{"type": "image", "data": {"file": str(sticker)}}],
    ))

    assert message_id == "MID-private"
    posts = connection._http.posts()
    assert posts[0][0].endswith("/v2/users/U-openid/files")
    sent = [body for url, body in posts if url.endswith("/messages")]
    assert sent == [{"msg_type": 7, "media": {"file_info": "FI-chunked"}}], sent


def test_private_image_upload_failure_degrades_to_text():
    connection = _make_connection(
        lambda method, url, body: {"id": "MID"} if url.endswith("/messages") else {},
    )

    message_id = _run(connection.send_private_message_segments(
        "U1", [{"type": "image", "data": {"file": "https://cdn.example/gone.png"}}],
    ))

    assert message_id == "MID"
    sent = [body for url, body in connection._http.posts() if url.endswith("/messages")]
    assert sent == [{"content": "[图片]"}], sent


def test_private_image_without_a_usable_id_keeps_the_text_fallback():
    connection = _make_connection(lambda method, url, body: {"id": "MID"})

    _run(connection.send_private_message_segments(
        "", [{"type": "image", "data": {"file": "https://cdn.example/a.png"}}],
    ))

    posts = connection._http.posts()
    assert not any("/files" in url for url, _ in posts), "no upload without an id to upload against"
    sent = [body for url, body in posts if url.endswith("/messages")]
    assert sent == [{"content": "[图片]"}], sent


# ── the mixin is wired in, and stays reachable for consumers ──────────────────


def test_the_connection_mixes_in_the_media_actions():
    assert issubclass(QQOpenPlatformConnection, QQOpenPlatformMediaMixin)
    # Mixin first: ConnectionBase declares send_group_image abstract, and the media
    # upload is what that method has to use.
    mro = QQOpenPlatformConnection.__mro__
    assert mro.index(QQOpenPlatformMediaMixin) < mro.index(ConnectionBase)
    for name in ("upload_image", "send_private_image"):
        assert callable(getattr(QQOpenPlatformConnection, name, None)), name


def test_the_connector_surface_has_the_image_operations():
    """qq_auto_reply resolves ``utils.connection.onebot`` and sends stickers itself.

    The plugin drives the connection object rather than a module of its own, which is
    what lets its vendored copy retire: the upload (Open Platform only -- OneBot takes
    an image path straight through) and the private image send, which both
    implementations have.
    """
    for cls in (onebot.OneBotClient, onebot.QQOpenPlatformConnection):
        assert callable(getattr(cls, "send_private_image", None)), cls
    assert callable(getattr(onebot.QQOpenPlatformConnection, "upload_image", None))


def test_the_sticker_call_is_the_same_on_both_connectors():
    """A sticker is not the bot replying, and saying so must not depend on the connector.

    ``send_private_image`` is not that call: OneBot's twin takes only
    ``(user_id, image_data)``, and the Open Platform's records the sent id by default.
    The uniform call is the **segment** sender, which both implementations accept with
    ``record_sent`` -- and which the plugin already uses on the OneBot path.
    """
    for cls in (onebot.OneBotClient, onebot.QQOpenPlatformConnection):
        params = inspect.signature(cls.send_private_message_segments).parameters
        assert params["record_sent"].kind is inspect.Parameter.KEYWORD_ONLY, cls
        assert params["record_sent"].default is True, cls


def test_the_mixin_works_on_a_class_that_only_has_the_members():
    """Nothing here needs ``ConnectionBase`` or a specific class identity."""
    class _Bare(QQOpenPlatformMediaMixin):
        CHANNEL = "open"

        def __init__(self):
            self._http = _FakeHTTP(lambda method, url, body: {"file_info": "FI"})
            self._API_BASE = API_BASE
            self.logger = None

        async def _ensure_token(self):
            return None

        def _auth_headers(self):
            return {"Authorization": "QQBot fake"}

    bare = _Bare()
    assert _run(bare.upload_image(scope="users", owner_id="U1", source="https://cdn.example/a.png")) == "FI"
    assert bare._http.posts()[0][0] == f"{API_BASE}/v2/users/U1/files"


@pytest.mark.parametrize("source", ["", "   ", None])
def test_an_empty_source_is_not_an_upload(source):
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})
    assert _run(connection.upload_image(scope="users", owner_id="U1", source=source)) == ""
    assert connection._http.calls == []
