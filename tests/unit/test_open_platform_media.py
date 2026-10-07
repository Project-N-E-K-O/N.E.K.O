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
import threading

import httpx
import pytest

import utils.connection.onebot as onebot
from utils.connection.base import ConnectionBase
from utils.connection.qq import open_platform_media as media_module
from utils.connection.qq.open_platform import QQOpenPlatformConnection
from utils.connection.qq.open_platform_media import (
    MAX_IMAGE_BYTES,
    QQOpenPlatformMediaMixin,
)

API_BASE = "https://api.sgroup.qq.com"


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        """Mimic httpx: 4xx/5xx raise instead of being silently usable."""
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://fake.invalid"),
                response=self,
            )


class _FakeHTTP:
    """Answers through ``responder(method, url, body)`` and records every call.

    The responder may return a payload dict, or a ``_Response`` when the test needs a
    non-2xx status (a rejected part PUT, a failing ``upload_part_finish``).
    """

    def __init__(self, responder):
        self._responder = responder
        self.calls: list[tuple[str, str, object]] = []

    def _answer(self, method, url, body) -> _Response:
        raw = self._responder(method, url, body)
        return raw if isinstance(raw, _Response) else _Response(raw)

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        return self._answer("POST", url, json)

    async def put(self, url, content=None, headers=None):
        self.calls.append(("PUT", url, content))
        return self._answer("PUT", url, content)

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


# ── a rejected part must stop the upload, not be merged anyway ────────────────
#
# httpx does not raise on 4xx/5xx by itself, so a rejected part PUT used to be
# indistinguishable from an accepted one: the loop kept going, the coverage check
# passed, and the merge answered with a normal-looking ``file_info`` -- a truncated
# image reported as sent.

def _no_merge(connection) -> bool:
    return not any(
        url.endswith("/files") and body.get("upload_id")
        for url, body in connection._http.posts()
    )


def test_a_rejected_part_upload_is_refused_before_merging(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "PUT":
            return _Response({}, status_code=403)      # presigned URL expired / rejected
        return _legacy_then_nothing(method, url, body)

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(connection), "a rejected part PUT must not be merged"


def test_a_failing_part_finish_is_refused_before_merging(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "POST" and url.endswith("/upload_part_finish"):
            return _Response({}, status_code=500)
        return _legacy_then_nothing(method, url, body)

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(connection), "a failing upload_part_finish must not be merged"


def test_a_part_finish_error_envelope_is_refused_before_merging(tmp_path):
    """A 200 answer carrying the platform's error envelope counts as a failure too."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    def responder(method, url, body):
        if method == "POST" and url.endswith("/upload_part_finish"):
            return {"code": 500, "message": "part rejected"}
        return _legacy_then_nothing(method, url, body)

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="users", owner_id="U1", source=str(sticker))) == ""
    assert _no_merge(connection), "an error envelope must not be merged"


def test_a_rejected_legacy_put_is_not_reported_as_success(tmp_path):
    """The legacy path must not fall back to the *apply* answer's ``file_info`` when the PUT failed."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 32)

    def responder(method, url, body):
        if method == "PUT":
            return _Response({}, status_code=403)
        return {"upload_url": "https://cos.example/put/1", "file_info": "FI-upfront"}

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == ""


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


def _spy_on_read(monkeypatch, record):
    """Replace the module's reader, keeping a note of every call."""
    real = media_module._read_source

    def spy(source):
        record.append(source)
        return real(source)

    monkeypatch.setattr(media_module, "_read_source", spy)
    return spy


def test_an_oversized_file_is_refused_without_reading_it(tmp_path, monkeypatch):
    """The size limit has to reject a file *before* its bytes are pulled into memory."""
    sticker = tmp_path / "huge.png"
    sticker.write_bytes(b"b" * 4096)
    monkeypatch.setattr(media_module, "MAX_IMAGE_BYTES", 64)
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == ""
    assert read == [], "an oversized file must not be read at all"
    assert connection._http.calls == []


def test_a_missing_file_is_refused_without_reading_it(tmp_path, monkeypatch):
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})

    assert _run(connection.upload_image(scope="users", owner_id="U1", source=str(tmp_path / "nope.png"))) == ""
    assert read == [], "a missing file must not be read"
    assert connection._http.calls == []


def test_a_local_file_is_read_off_the_event_loop(tmp_path, monkeypatch):
    """Blocking file I/O must not run on the loop: a big file (or a slow volume) would stall it."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 8)
    threads: list[bool] = []
    real = media_module._read_source

    def spy(source):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(source)

    monkeypatch.setattr(media_module, "_read_source", spy)
    connection = _make_connection(
        lambda method, url, body: _legacy_ok() if method == "POST" else {"file_info": "FI-legacy"},
    )

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == "FI-legacy"
    assert threads == [False], "the read must happen in a worker thread, not on the event loop"


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
    # Mixin first, by the same convention as OneBotClient(NapCatActionsMixin, ...).
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


# ── review (2026-09-29): block_size fallback, index base, no upload for CQ images ──


def _top_level_block_size_only(method, url, body):
    """The prepare answer carries the part size **only at the top level** -- one of the
    shapes the live platform hands back.

    What a missing fallback costs: with size 0 the first part slices ``payload[0:]`` (the
    whole file) and PUTs it, then the second part slices an empty chunk and returns "",
    so a multi-part upload can never succeed.
    """
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "block_size": "8",
            "parts": [
                {"index": 0, "presigned_url": "https://cos.example/part/0"},
                {"index": 1, "presigned_url": "https://cos.example/part/1"},
            ],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    if url.endswith("/files") and body.get("upload_id"):
        return {"file_info": "FI-chunked"}
    return {}


def _one_based_indices(method, url, body):
    """A platform that numbers parts from 1: the implementation only sorts by `index`
    and echoes it back, so both bases have to work."""
    if method == "PUT":
        return {"file_info": "FI-legacy"}
    if url.endswith("/upload_prepare"):
        return {
            "upload_id": "upload_1",
            "parts": [
                {"index": 1, "presigned_url": "https://cos.example/part/1", "block_size": "8"},
                {"index": 2, "presigned_url": "https://cos.example/part/2", "block_size": "8"},
            ],
        }
    if url.endswith("/upload_part_finish"):
        return {}
    if url.endswith("/files") and body.get("upload_id"):
        return {"file_info": "FI-chunked"}
    return {}


def test_a_part_without_its_own_block_size_falls_back_to_the_top_level(tmp_path):
    """**Review must-fix**: a part without its own block_size uses the prepare-level one."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)
    connection = _make_connection(_top_level_block_size_only)

    file_info = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))

    assert file_info == "FI-chunked", "no fallback to the top-level block_size"
    assert [len(body) for _url, body in connection._http.puts()] == [8, 8], (
        "each part should be 8 bytes (a 16-byte first part means no fallback)"
    )


def test_one_based_part_indices_work_too(tmp_path):
    """The index base (0- or 1-based) does not matter: `index` is only used for ordering
    and is echoed back verbatim."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)
    connection = _make_connection(_one_based_indices)

    file_info = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))

    assert file_info == "FI-chunked"
    assert [len(body) for _url, body in connection._http.puts()] == [8, 8]
    finished = [
        body["part_index"] for url, body in connection._http.posts()
        if url.endswith("/upload_part_finish")
    ]
    assert finished == [1, 2], f"the echoed part_index must match the platform's: {finished}"


def test_a_cq_image_in_the_llm_text_is_never_uploaded(tmp_path, monkeypatch):
    """**Review must-fix (security)**: a `[CQ:image,file=<local path>]` in the text is
    never uploaded.

    That text is an LLM reply, so prompting it to emit `[CQ:image,file=<local path>]` was
    enough to make this layer read any file the process can read and send it out. Only an
    image segment the caller passed **explicitly** is uploaded.
    """
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOP-SECRET" * 4)
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    connection = _make_connection(lambda method, url, body: {"id": "MID"} if url.endswith("/messages") else {})

    message_id = _run(connection.send_private_message_segments(
        "U1", [{"type": "text", "data": {"text": f"看看这个[CQ:image,file={secret}]"}}],
    ))

    assert message_id == "MID"
    assert read == [], "the CQ image was treated as an upload source and read from disk"
    assert not any("/files" in url for url, _body in connection._http.posts())
    sent = [body for url, body in connection._http.posts() if url.endswith("/messages")]
    assert sent == [{"content": "看看这个[图片]"}], sent


def test_a_cq_image_in_the_group_text_is_never_uploaded(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"TOP-SECRET" * 4)
    read: list[str] = []
    _spy_on_read(monkeypatch, read)
    connection = _make_connection(lambda method, url, body: {"id": "MID"} if url.endswith("/messages") else {})

    _run(connection.send_group_message_segments(
        "G1", [{"type": "text", "data": {"text": f"看看这个[CQ:image,file={secret}]"}}],
        record_sent=False,
    ))

    assert read == []
    assert not any("/files" in url for url, _body in connection._http.posts())


def test_an_explicit_image_segment_still_uploads(tmp_path):
    """The other side of it: an image segment the caller passed explicitly still uploads
    (that is how the plugin sends pictures)."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)
    connection = _make_connection(_chunked_with_ids)

    message_id = _run(connection.send_private_message_segments(
        "U1", [{"type": "image", "data": {"file": str(sticker)}}], record_sent=False,
    ))

    assert message_id == "MID-private"
    assert any(url.endswith("/upload_prepare") for url, _body in connection._http.posts())


def test_digests_are_computed_off_the_event_loop(tmp_path, monkeypatch):
    """The digests (md5 + sha1 + the 10MB prefix) are computed in the same worker
    thread as the read, so they never occupy the event loop."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"x" * 8)
    threads: list[bool] = []
    real = media_module._digests

    def spy(payload):
        threads.append(threading.current_thread() is threading.main_thread())
        return real(payload)

    monkeypatch.setattr(media_module, "_digests", spy)
    connection = _make_connection(_legacy_ok_for_put)

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == "FI-legacy"
    assert threads == [False], "the digests were computed on the event loop"


def _legacy_ok_for_put(method, url, body):
    return _legacy_ok() if method == "POST" else {"file_info": "FI-legacy"}


def test_the_token_is_ensured_once_per_send(tmp_path):
    """The image send path no longer asks for a token twice (once outside, once inside
    the upload)."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"y" * 16)

    for send in ("group", "private"):
        connection = _make_connection(_chunked_with_ids)
        calls: list[int] = []

        async def _counting():
            calls.append(1)

        connection._ensure_token = _counting
        if send == "group":
            _run(connection.send_group_message_segments(
                "G1", [{"type": "image", "data": {"file": str(sticker)}}], record_sent=False,
            ))
        else:
            _run(connection.send_private_message_segments(
                "U1", [{"type": "image", "data": {"file": str(sticker)}}], record_sent=False,
            ))
        assert len(calls) == 1, f"the {send} path ensured the token {len(calls)} times"


def test_a_dead_legacy_protocol_is_not_retried_for_every_image(tmp_path):
    """Once the legacy protocol answers without a file_info, this connection stops
    retrying it per image (one guaranteed-failing request less)."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)
    connection = _make_connection(_legacy_then_nothing)

    first = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))
    second = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))

    assert (first, second) == ("FI-chunked", "FI-chunked")
    legacy_attempts = [
        url for url, body in connection._http.posts()
        if url.endswith("/files") and "file_size" in body
    ]
    assert len(legacy_attempts) == 1, f"the legacy protocol was retried {len(legacy_attempts)} times"


def test_local_path_handles_real_file_uris():
    """`file:///C:/x.png` and percent-escapes are not handled by stripping a prefix.

    Written so it holds on **any** platform (CI runs on ubuntu-latest): the drive letter
    comes out as `C:/...` everywhere -- Windows' `url2pathname` answers it directly,
    POSIX's leaves `/C:/...` and `_local_path` drops the URI slash. Only the separator
    stays platform-native, so comparisons go through `_slash`.
    """
    def _slash(text: str) -> str:
        return text.replace("\\", "/")

    assert _slash(media_module._local_path("file:///tmp/a%20b.png")).endswith("tmp/a b.png")
    assert not media_module._local_path("file:///tmp/a.png").lower().startswith("file:")
    assert _slash(media_module._local_path("file:///C:/x.png")) == "C:/x.png"
    # UNC keeps the host name (url2pathname switches separators to backslashes on
    # Windows).
    unc = media_module._local_path("file://server/share/a.png")
    assert _slash(unc).startswith("//server/share"), unc
    # A plain path goes through untouched
    assert media_module._local_path("C:/plain/a.png") == "C:/plain/a.png"


def test_a_percent_escape_is_decoded_exactly_once():
    """`%2520` means a literal `%20` -- decoding twice would open the wrong file.

    `url2pathname` already unquotes on both platforms (POSIX: it *is* `unquote`;
    Windows: `nturl2path` decodes too), so an extra `unquote` in `_local_path` turns
    `a%2520b.png` into `a b.png`: the wrong path, silently.
    """
    decoded = media_module._local_path("file:///tmp/a%2520b.png")

    assert decoded.replace("\\", "/").endswith("tmp/a%20b.png"), decoded


def test_the_drive_letter_is_normalized_whatever_url2pathname_answers(monkeypatch):
    """Both `url2pathname` flavours land on the same drive-letter path.

    POSIX's is `unquote` (it leaves ``/C:/x.png`` alone), Windows' `nturl2path` converts
    it -- a Windows-made URI must not resolve differently when the host is Linux.
    """
    monkeypatch.setattr(media_module, "url2pathname", lambda path: path)      # posixpath
    assert media_module._local_path("file:///C:/x.png").replace("\\", "/") == "C:/x.png"

    monkeypatch.setattr(
        media_module, "url2pathname",
        lambda path: path.lstrip("/").replace("/", "\\"),                    # nturl2path
    )
    assert media_module._local_path("file:///C:/x.png").replace("\\", "/") == "C:/x.png"


def test_the_connection_docstring_survives_the_channel_assignment():
    """With `CHANNEL = "open"` before the class docstring, that text is not `__doc__`
    (review nit)."""
    assert QQOpenPlatformConnection.CHANNEL == "open"
    doc = QQOpenPlatformConnection.__doc__ or ""
    assert "QQOpenPlatformMediaMixin" in doc, "the class docstring is a discarded expression again"


# ── re-review (2026-09-30) ────────────────────────────────────────────────────


def _b64(payload: bytes) -> str:
    import base64
    return "base64://" + base64.b64encode(payload).decode("ascii")


class _ListLogger:
    def __init__(self):
        self.lines: list[str] = []

    def warning(self, message):
        self.lines.append(message)

    info = warning


def test_a_base64_source_is_decoded_and_uploaded():
    """The OneBot ``image`` segment convention (``base64://``) used to be taken for a
    local path: ``getsize`` failed and the image degraded to text."""
    png = bytes.fromhex("89504e470d0a1a0a") + b"p" * 8
    connection = _make_connection(_legacy_then_nothing)

    file_info = _run(connection.upload_image(scope="groups", owner_id="G1", source=_b64(png)))

    assert file_info == "FI-chunked"
    assert b"".join(body for _url, body in connection._http.puts()[-2:]) == png
    prepare = [body for url, body in connection._http.posts() if url.endswith("/upload_prepare")]
    assert prepare[0]["file_name"] == "image.png"


def test_an_oversized_base64_source_is_refused_without_decoding(monkeypatch):
    decoded: list[str] = []
    real = media_module._decode_base64_source
    monkeypatch.setattr(
        media_module, "_decode_base64_source", lambda text: decoded.append(text) or real(text),
    )
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})
    huge = "base64://" + "A" * (MAX_IMAGE_BYTES * 4 // 3 + 8)

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=huge)) == ""
    assert decoded == []
    assert connection._http.calls == []


def test_a_base64_image_of_exactly_the_limit_is_not_refused(monkeypatch):
    """Padding and line breaks are not data: counting them made an image of exactly the
    limit look one byte too big, so it was refused before decoding."""
    import base64

    monkeypatch.setattr(media_module, "MAX_IMAGE_BYTES", 32)
    payload = bytes.fromhex("89504e47") + b"q" * 28          # 32 bytes -> one "=" of padding
    encoded = base64.b64encode(payload).decode("ascii")
    assert encoded.endswith("=") and len(encoded) * 3 // 4 > 32
    wrapped = encoded[:20] + "\n" + encoded[20:] + "\n"

    for text in (encoded, wrapped):
        connection = _make_connection(_legacy_ok_for_put)
        assert _run(connection.upload_image(
            scope="groups", owner_id="G1", source="base64://" + text,
        )) == "FI-legacy", repr(text)
        assert connection._http.puts()[0][1] == payload


def test_the_base64_size_counts_only_data():
    import base64

    for size in range(0, 10):
        text = base64.b64encode(b"x" * size).decode("ascii")
        assert media_module._base64_decoded_size(text) == size, (size, text)
        assert media_module._base64_decoded_size(" " + text[:2] + "\r\n" + text[2:] + "\n") == size


def test_the_size_check_and_the_decoder_agree_on_whitespace(monkeypatch):
    """The decoder drops exactly the whitespace the size check discounts (ASCII), so a
    text cannot pass one and fail the other; Unicode whitespace is simply invalid."""
    import base64

    payload = bytes.fromhex("89504e47") + b"q" * 12
    encoded = base64.b64encode(payload).decode("ascii")

    ascii_wrapped = media_module._decode_base64_source(encoded[:8] + "\r\n\t " + encoded[8:])
    assert ascii_wrapped.payload == payload

    ideographic = encoded[:8] + "\u3000" + encoded[8:]
    assert media_module._decode_base64_source(ideographic).payload == b""
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"})
    assert _run(connection.upload_image(
        scope="groups", owner_id="G1", source="base64://" + ideographic,
    )) == ""
    assert connection._http.calls == []


def test_an_undecodable_base64_source_fails_cleanly_and_is_not_logged_whole():
    logger = _ListLogger()
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"}, logger=logger)
    garbage = "base64://" + "!" * 5000

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=garbage)) == ""
    assert connection._http.calls == []
    assert logger.lines and all(len(line) < 300 for line in logger.lines), logger.lines


def test_a_long_missing_path_is_truncated_in_the_log():
    logger = _ListLogger()
    connection = _make_connection(lambda method, url, body: {"file_info": "FI"}, logger=logger)
    source = "/no/such/" + "x" * 4000 + ".png"

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=source)) == ""
    assert logger.lines and all(len(line) < 300 for line in logger.lines), logger.lines


@pytest.mark.parametrize(
    "parts",
    [["not-a-dict"], [{"index": "1.0", "presigned_url": "https://cos.example/p"}]],
)
def test_odd_part_lists_fail_cleanly_or_work(tmp_path, parts):
    """A part list without dicts is refused (not an ``IndexError``), and an index like
    ``"1.0"`` sorts with the same parser the loop uses (not a ``ValueError``)."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 8)

    def responder(method, url, body):
        if url.endswith("/upload_prepare"):
            return {"upload_id": "u", "block_size": "8", "parts": parts}
        if url.endswith("/files") and body.get("upload_id"):
            return {"file_info": "FI-chunked"}
        return {}

    connection = _make_connection(responder)
    connection._legacy_upload_unsupported = True

    file_info = _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker)))

    expected = "FI-chunked" if isinstance(parts[0], dict) else ""
    assert file_info == expected
    if not expected:
        assert not any("upload_part_finish" in url for url, _ in connection._http.posts())


def _legacy_rejected_with(status):
    def responder(method, url, body):
        if url.endswith("/files") and "file_size" in body:
            return _Response({"message": "bad request"}, status_code=status)
        return _legacy_then_nothing(method, url, body)
    return responder


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
def test_an_error_status_does_not_switch_legacy_off(tmp_path, status):
    """A 400 (file too large) or 404 (target unreachable) can be about this one image or
    target: it sends only this image down the chunked path."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)
    connection = _make_connection(_legacy_rejected_with(status))

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == "FI-chunked"
    assert connection._legacy_upload_unsupported is False


@pytest.mark.parametrize("answer", [None, ["not", "an", "object"], "<html>busy</html>"])
def test_a_malformed_legacy_answer_does_not_switch_legacy_off(tmp_path, answer):
    """A 200 whose body is not a JSON object (HTML, ``null``, an array) says nothing
    about the protocol; only a well-formed object without ``upload_url`` does."""
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)

    def responder(method, url, body):
        if url.endswith("/files") and "file_size" in body:
            return _Response(answer)
        return _legacy_then_nothing(method, url, body)

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == "FI-chunked"
    assert connection._legacy_upload_unsupported is False


def test_an_error_code_does_not_switch_legacy_off(tmp_path):
    sticker = tmp_path / "a.png"
    sticker.write_bytes(b"z" * 16)

    def responder(method, url, body):
        if url.endswith("/files") and "file_size" in body:
            return {"code": 40034, "message": "file too large"}
        return _legacy_then_nothing(method, url, body)

    connection = _make_connection(responder)

    assert _run(connection.upload_image(scope="groups", owner_id="G1", source=str(sticker))) == "FI-chunked"
    assert connection._legacy_upload_unsupported is False


def test_unpadded_base64_is_decoded():
    """``_base64_decoded_size`` accepts unpadded text; the decoder has to as well."""
    import base64

    payload = bytes.fromhex("89504e47") + b"q" * 3           # 7 bytes -> one "=" normally
    unpadded = base64.b64encode(payload).decode("ascii").rstrip("=")
    assert len(unpadded) % 4, "the fixture must really be unpadded"

    assert media_module._decode_base64_source(unpadded).payload == payload
    assert media_module._base64_decoded_size(unpadded) == len(payload)


def test_connect_clears_the_legacy_flag():
    """The flag is per connection: a fresh ``connect()`` probes the legacy protocol again."""
    connection = QQOpenPlatformConnection(app_id="a", client_secret="b")
    connection._legacy_upload_unsupported = True

    async def _stop():
        raise RuntimeError("stop after the reset")

    connection._refresh_token = _stop

    async def scenario():
        try:
            await connection.connect()
        finally:
            await connection._http.aclose()

    with pytest.raises(RuntimeError):
        _run(scenario())
    assert connection._legacy_upload_unsupported is False


def test_a_private_reply_is_a_passive_reply_with_distinct_seqs():
    """The reply segment used to become a literal "reply" placeholder with no ``msg_id``:
    an active message (quota-limited). Replies to the same id need distinct ``msg_seq``s."""
    connection = _make_connection(lambda method, url, body: {"id": "MID"})

    for text in ("第一句", "第二句"):
        _run(connection.send_private_message_segments(
            "U1", [{"type": "text", "data": {"text": f"[CQ:reply,id=IN-1]{text}"}}],
        ))

    sent = [body for url, body in connection._http.posts() if url.endswith("/messages")]
    assert sent == [
        {"content": "第一句", "msg_id": "IN-1", "msg_seq": 1},
        {"content": "第二句", "msg_id": "IN-1", "msg_seq": 2},
    ], sent


def test_group_replies_to_one_message_get_distinct_seqs():
    connection = _make_connection(lambda method, url, body: {"id": "MID"})

    for text in ("a", "b"):
        _run(connection.send_group_message_segments(
            "G1",
            [{"type": "reply", "data": {"id": "IN-9"}}, {"type": "text", "data": {"text": text}}],
            record_sent=False,
        ))

    seqs = [body.get("msg_seq") for url, body in connection._http.posts() if url.endswith("/messages")]
    assert seqs == [1, 2]


def test_msg_seq_counters_age_out_by_time_not_by_count(monkeypatch):
    """A count cap reset the counter of an id that was still inside its passive-reply
    window once enough other messages had been replied to, and the next reply then
    reused ``(msg_id, 1)``: a duplicate the platform rejects."""
    from types import SimpleNamespace

    from utils.connection.qq import open_platform as op_module

    clock = [1000.0]
    monkeypatch.setattr(op_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    connection = _make_connection(lambda method, url, body: {})

    assert connection._next_msg_seq("OLD") == 1
    for i in range(1000):
        connection._next_msg_seq(f"other-{i}")
    assert connection._next_msg_seq("OLD") == 2, "a live counter was dropped by count"

    clock[0] += op_module._MSG_SEQ_TTL_SECONDS + 1
    assert connection._next_msg_seq("NEW") == 1
    assert list(connection._msg_seq_by_reply_id) == ["NEW"], "stale counters were kept"


def test_send_private_image_reply_gets_a_seq_too():
    def responder(method, url, body):
        return {"id": "MID"} if url.endswith("/messages") else {"file_info": "FI"}

    connection = _make_connection(responder)
    _run(connection.send_private_image("U1", "https://cdn.example/a.png", reply_message_id="IN-3"))

    sent = [body for url, body in connection._http.posts() if url.endswith("/messages")]
    assert sent[0]["msg_id"] == "IN-3" and sent[0]["msg_seq"] == 1


@pytest.mark.parametrize("send", ["group", "private"])
def test_a_second_image_in_one_message_becomes_a_placeholder(send):
    """One ``msg_type=7`` message carries one media item: the first image is sent, the
    others stay visible as text instead of silently replacing it."""
    def responder(method, url, body):
        return {"id": "MID"} if url.endswith("/messages") else {"file_info": "FI"}

    connection = _make_connection(responder)
    segments = [
        {"type": "image", "data": {"file": "https://cdn.example/first.png"}},
        {"type": "image", "data": {"file": "https://cdn.example/second.png"}},
    ]
    if send == "group":
        _run(connection.send_group_message_segments("G1", segments, record_sent=False))
    else:
        _run(connection.send_private_message_segments("U1", segments, record_sent=False))

    uploads = [body.get("url") for url, body in connection._http.posts() if url.endswith("/files")]
    assert uploads == ["https://cdn.example/first.png"]
    sent = [body for url, body in connection._http.posts() if url.endswith("/messages")]
    assert sent == [{"msg_type": 7, "media": {"file_info": "FI"}, "content": "[图片]"}], sent


def test_cq_images_are_turned_into_text_by_the_expansion_itself():
    """The security rule sits in ``_expand_cq_segments``, so a future send path that
    expands CQ codes cannot forget it."""
    connection = _make_connection(lambda method, url, body: {})
    expanded = connection._expand_cq_segments(
        [{"type": "text", "data": {"text": "a[CQ:image,file=/etc/passwd]b"}}],
    )
    assert [seg["type"] for seg in expanded] == ["text", "text", "text"]
    assert "".join(seg["data"]["text"] for seg in expanded) == "a[图片]b"
