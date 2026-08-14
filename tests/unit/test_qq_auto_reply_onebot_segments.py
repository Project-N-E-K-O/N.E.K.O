"""OneBot segment -> MessageChain backend compatibility (file / at).

Aligns with the compatibility matrix already proven by AstrBot's aiocqhttp
adapter:
- file segment: Lagrange (direct ``data.url``) vs NapCat
  (``get_group_file_url`` / ``get_private_file_url``) vs the plain
  ``data.file`` fallback;
- at segment: nickname via ``get_group_member_info`` (card) then
  ``get_stranger_info`` (nick), "all" issues no API call, and total failure
  falls back to a bare At.

Every new API call must self-degrade and never raise out of the chain builder.
Also covers ``_fetch_file_content`` (background file-content pull): images go
through the VLM describer, text is decoded, binary is flagged, and the raw
CQ code in content is replaced by a readable form.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from plugin.plugins.qq_auto_reply.message_chain import At, File, Text
from plugin.plugins.qq_auto_reply.qq_client import QQClient


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status


class _FakeStream:
    def __init__(self, resp):
        self._resp = resp
        self.served = 0  # bytes the consumer actually pulled

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    @property
    def status_code(self):
        return self._resp.status_code

    async def aiter_bytes(self):
        content = self._resp.content
        for i in range(0, len(content), 4096):
            self.served += min(4096, len(content) - i)
            yield content[i : i + 4096]


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def stream(self, method, url):
        return _FakeStream(self._resp)


def _patch_http(resp):
    """Swap the httpx.AsyncClient used inside _fetch_file_content for a fake."""
    return patch("httpx.AsyncClient", lambda *_a, **_k: _FakeClient(resp))


def _client() -> QQClient:
    client = QQClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    return client


def _msg(*segments, message_type="group", group_id="g1") -> dict:
    return {
        "message_type": message_type,
        "group_id": group_id,
        "user_id": "u1",
        "time": 123,
        "message_id": "m1",
        "message": list(segments),
    }


# --- file segment ---

def test_collect_file_segments_keeps_busid():
    """busid must survive segment collection in both array and CQ-string forms,
    otherwise get_group_file_url cannot resolve URLs on backends that need it."""
    client = _client()
    array_files = client._collect_file_segments({
        "message_type": "group",
        "message": [{"type": "file", "data": {"file": "a.zip", "file_id": "fid1", "busid": 102}}],
    })
    assert array_files[0]["busid"] == 102
    cq_files = client._collect_file_segments({
        "message_type": "group",
        "message": "[CQ:file,file=a.zip,file_id=fid1,busid=102]",
        "raw_message": "[CQ:file,file=a.zip,file_id=fid1,busid=102]",
    })
    assert cq_files == [{"file_id": "fid1", "name": "a.zip", "url": "", "busid": 102}]


def test_collect_file_segments_tolerates_malformed_data():
    """Malformed file segments (data not a dict, busid not an int) must never
    raise or drop the whole message."""
    client = _client()
    weird = client._collect_file_segments({
        "message_type": "group",
        "message": [{"type": "file", "data": "oops"}],
    })
    assert weird == [{"file_id": "", "name": "", "url": "", "busid": 0}]
    bad_busid = client._collect_file_segments({
        "message_type": "group",
        "message": [{"type": "file", "data": {"file": "a.zip", "file_id": "fid1", "busid": "NaN"}}],
    })
    assert bad_busid[0]["busid"] == 0

async def test_file_lagrange_uses_direct_url():
    """Lagrange-style backends: data.url is an http direct link -> use it
    directly, never call get_group_file_url."""
    client = _client()
    with patch.object(client, "get_group_file_url", AsyncMock()) as mock:
        chain = await client._build_message_chain(_msg({
            "type": "file",
            "data": {"file": "abc", "file_name": "report.pdf", "url": "https://cdn/1.pdf"},
        }))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.name == "report.pdf"
    assert elem.url == "https://cdn/1.pdf"
    mock.assert_not_called()


async def test_file_napcat_group_calls_file_url_api():
    """NapCat group file: file_id + busid -> call get_group_file_url for the URL."""
    client = _client()
    async def fake(group_id, file_id, busid=0):
        assert group_id == "g1"
        assert file_id == "fid123"
        assert busid == 102
        return {"url": "https://dl/a.zip", "file_name": "a.zip"}
    with patch.object(client, "get_group_file_url", fake):
        chain = await client._build_message_chain(_msg({
            "type": "file",
            "data": {"file": "uuid", "file_id": "fid123", "busid": 102},
        }))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.name == "a.zip"
    assert elem.url == "https://dl/a.zip"


async def test_file_napcat_group_without_busid_uses_generic_get_file():
    """Group file carrying only file_id (no busid) -> use generic get_file_by_id,
    not get_group_file_url (which needs a real busid, 0 gets rejected)."""
    client = _client()
    async def fake(file_id):
        assert file_id == "fid7"
        return {"url": "https://dl/nobusid.txt", "name": "nobusid.txt"}
    with patch.object(client, "get_group_file_url", AsyncMock()) as group_mock, \
         patch.object(client, "get_file_by_id", fake):
        chain = await client._build_message_chain(_msg({
            "type": "file",
            "data": {"file": "uuid", "file_id": "fid7"},
        }))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.url == "https://dl/nobusid.txt"
    group_mock.assert_not_called()


async def test_file_napcat_private_calls_private_file_url():
    """NapCat private file: message_type=private -> call get_private_file_url
    with the sender's user_id (NapCat requires both user_id and file_id)."""
    client = _client()
    async def fake(user_id, file_id):
        assert user_id == "u1"  # _msg sets user_id="u1"
        assert file_id == "fid9"
        return {"url": "https://dl/p.bin", "name": "p.bin"}
    with patch.object(client, "get_private_file_url", fake):
        chain = await client._build_message_chain(_msg(
            {"type": "file", "data": {"file": "uuid", "file_id": "fid9"}},
            message_type="private", group_id="",
        ))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.url == "https://dl/p.bin"
    assert elem.name == "p.bin"


async def test_file_api_failure_falls_back():
    """get_group_file_url raising -> fall back to File(name=raw_file), no raise."""
    client = _client()
    with patch.object(client, "get_group_file_url", AsyncMock(side_effect=RuntimeError("not connected"))):
        chain = await client._build_message_chain(_msg({
            "type": "file",
            "data": {"file": "uuid", "file_id": "fid123"},
        }))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.name == "uuid"
    assert elem.url == ""


async def test_file_pure_fallback_no_url_no_file_id():
    """No url and no file_id -> fall back to File(name=raw_file)."""
    client = _client()
    chain = await client._build_message_chain(_msg({
        "type": "file",
        "data": {"file": "plain.dat"},
    }))
    elem = chain.elements[0]
    assert isinstance(elem, File)
    assert elem.name == "plain.dat"
    assert elem.url == ""


async def test_file_failure_does_not_break_following_segments():
    """After a file API failure, later segments still enter the chain."""
    client = _client()
    with patch.object(client, "get_group_file_url", AsyncMock(side_effect=RuntimeError)):
        chain = await client._build_message_chain(_msg(
            {"type": "file", "data": {"file": "f1", "file_id": "fid"}},
            {"type": "text", "data": {"text": "and then this"}},
        ))
    assert isinstance(chain.elements[0], File)
    assert isinstance(chain.elements[1], Text)
    assert chain.elements[1].text == "and then this"


# --- at segment ---

async def test_at_resolves_group_card_nickname():
    """Group message: get_group_member_info card -> At carries the nickname, no
    stranger lookup."""
    client = _client()
    with patch.object(client, "get_group_member_info", AsyncMock(return_value={"card": "Alice"})), \
         patch.object(client, "get_stranger_info", AsyncMock()) as stranger:
        chain = await client._build_message_chain(_msg({"type": "at", "data": {"qq": "12345"}}))
    elem = chain.elements[0]
    assert isinstance(elem, At)
    assert elem.pid == "12345"
    assert elem.nickname == "Alice"
    assert chain.repr == "[@Alice]"
    stranger.assert_not_called()


async def test_at_empty_card_falls_back_to_stranger():
    """Empty card -> get_stranger_info nick as fallback."""
    client = _client()
    with patch.object(client, "get_group_member_info", AsyncMock(return_value={"card": ""})), \
         patch.object(client, "get_stranger_info", AsyncMock(return_value={"nick": "Bob"})):
        chain = await client._build_message_chain(_msg({"type": "at", "data": {"qq": "12345"}}))
    elem = chain.elements[0]
    assert elem.nickname == "Bob"
    assert chain.repr == "[@Bob]"


async def test_at_all_no_api_call():
    """qq=all -> no API call, pid is 'all'."""
    client = _client()
    with patch.object(client, "get_group_member_info", AsyncMock()) as member, \
         patch.object(client, "get_stranger_info", AsyncMock()) as stranger:
        chain = await client._build_message_chain(_msg({"type": "at", "data": {"qq": "all"}}))
    elem = chain.elements[0]
    assert isinstance(elem, At)
    assert elem.pid == "all"
    member.assert_not_called()
    stranger.assert_not_called()


async def test_at_api_failures_fall_back_to_bare_at():
    """Both APIs failing -> At(pid, ""), no raise."""
    client = _client()
    with patch.object(client, "get_group_member_info", AsyncMock(side_effect=RuntimeError)), \
         patch.object(client, "get_stranger_info", AsyncMock(side_effect=RuntimeError)):
        chain = await client._build_message_chain(_msg({"type": "at", "data": {"qq": "12345"}}))
    elem = chain.elements[0]
    assert elem.nickname == ""


async def test_at_private_message_only_calls_stranger():
    """Private message (no group_id) -> only get_stranger_info is called."""
    client = _client()
    with patch.object(client, "get_group_member_info", AsyncMock()) as member, \
         patch.object(client, "get_stranger_info", AsyncMock(return_value={"nickname": "Zoe"})):
        chain = await client._build_message_chain(_msg(
            {"type": "at", "data": {"qq": "12345"}},
            message_type="private", group_id="",
        ))
    elem = chain.elements[0]
    assert elem.nickname == "Zoe"
    member.assert_not_called()


# --- _fetch_file_content (background file-content pull) ---

def _file_msg(*, message_type="private", group_id="", cq="[CQ:file,file=a.md,file_id=fid1,file_size=100]"):
    return {
        "message_type": message_type,
        "group_id": group_id,
        "user_id": "u_sender",
        "raw_message": cq,
        "content": cq,
    }


async def test_file_fetch_text_private_decodes():
    """Private text file: get_private_file_url -> download -> utf-8 decode into
    content, CQ code replaced."""
    client = _client()
    async def fake_private(user_id, file_id):
        assert user_id == "u_sender"
        assert file_id == "fid1"
        return {"url": "https://dl/a.md"}
    message = _file_msg()
    files = [{"file_id": "fid1", "name": "a.md", "url": "", "busid": 0}]
    with patch.object(client, "get_private_file_url", fake_private), \
         _patch_http(_FakeResp(b"# design doc\nfirst line")):
        await client._fetch_file_content(message, files)
    assert "# design doc" in message["content"]
    assert "a.md" in message["content"]
    assert "[CQ:file," not in message["content"]


async def test_file_fetch_group_without_busid_uses_generic_get_file():
    """Group file without busid in _fetch_file_content -> get_file_by_id."""
    client = _client()
    async def fake(file_id):
        assert file_id == "fid8"
        return {"url": "https://dl/g.txt"}
    message = _file_msg(message_type="group", group_id="g1",
                        cq="[CQ:file,file=g.txt,file_id=fid8]")
    files = [{"file_id": "fid8", "name": "g.txt", "url": "", "busid": 0}]
    with patch.object(client, "get_group_file_url", AsyncMock()) as group_mock, \
         patch.object(client, "get_file_by_id", fake), \
         _patch_http(_FakeResp(b"group content")):
        await client._fetch_file_content(message, files)
    assert "group content" in message["content"]
    group_mock.assert_not_called()


async def test_file_fetch_group_calls_group_file_url():
    """Group file: goes through get_group_file_url (with busid)."""
    client = _client()
    async def fake_group(group_id, file_id, busid=0):
        assert group_id == "g1"
        assert file_id == "fid2"
        assert busid == 102
        return {"url": "https://dl/g.txt"}
    message = _file_msg(message_type="group", group_id="g1",
                        cq="[CQ:file,file=g.txt,file_id=fid2]")
    files = [{"file_id": "fid2", "name": "g.txt", "url": "", "busid": 102}]
    with patch.object(client, "get_group_file_url", fake_group), \
         _patch_http(_FakeResp(b"group content")):
        await client._fetch_file_content(message, files)
    assert "group content" in message["content"]


async def test_file_fetch_image_goes_to_vlm():
    """Image file -> routes to the VLM describer, injects its description."""
    client = _client()
    client._image_describer = AsyncMock(return_value="a sleeping cat")
    message = _file_msg(cq="[CQ:file,file=cat.png,file_id=fid3]")
    files = [{"file_id": "fid3", "name": "cat.png", "url": "https://dl/cat.png", "busid": 0}]
    with patch.object(client, "get_private_file_url", AsyncMock()) as mock:
        await client._fetch_file_content(message, files)
    assert "a sleeping cat" in message["content"]
    assert "cat.png" in message["content"]
    mock.assert_not_called()  # image goes through VLM, no URL pull


async def test_file_fetch_image_without_describer_marks_only():
    """Image but no _image_describer -> just marks it, does not crash."""
    client = _client()
    client._image_describer = None
    message = _file_msg(cq="[CQ:file,file=cat.png,file_id=fid3]")
    files = [{"file_id": "fid3", "name": "cat.png", "url": "https://dl/cat.png", "busid": 0}]
    await client._fetch_file_content(message, files)
    assert "cat.png" in message["content"]
    assert "a sleeping cat" not in message["content"]


async def test_file_fetch_binary_marks_unreadable():
    """Bytes containing NUL -> flagged as unreadable binary."""
    client = _client()
    message = _file_msg(cq="[CQ:file,file=app.exe,file_id=fid4]")
    files = [{"file_id": "fid4", "name": "app.exe", "url": "https://dl/app.exe", "busid": 0}]
    with _patch_http(_FakeResp(b"MZ\x00\x00\x00binary")):
        await client._fetch_file_content(message, files)
    assert "app.exe" in message["content"]


async def test_file_fetch_truncates_over_limit():
    """Content over 100KB -> truncated."""
    client = _client()
    big = b"x" * (client._FILE_TEXT_MAX_BYTES + 10)
    message = _file_msg(cq="[CQ:file,file=big.txt,file_id=fid5]")
    files = [{"file_id": "fid5", "name": "big.txt", "url": "https://dl/big.txt", "busid": 0}]
    with _patch_http(_FakeResp(big)):
        await client._fetch_file_content(message, files)
    assert len(message["content"]) < client._FILE_TEXT_MAX_BYTES + 64


async def test_file_fetch_download_is_memory_bounded():
    """A huge file is streamed and only read up to the cap -- never the whole
    body (resp.content would pull hundreds of MB into memory)."""
    client = _client()
    huge = b"y" * (client._FILE_TEXT_MAX_BYTES * 8)  # 800KB, far over cap

    class _TrackedClient(_FakeClient):
        def __init__(self, resp):
            super().__init__(resp)
            self.streams: list[_FakeStream] = []

        def stream(self, method, url):
            s = _FakeStream(self._resp)
            self.streams.append(s)
            return s

    fake = _TrackedClient(_FakeResp(huge))
    message = _file_msg(cq="[CQ:file,file=big.bin,file_id=fid6]")
    files = [{"file_id": "fid6", "name": "big.bin", "url": "https://dl/big.bin", "busid": 0}]
    with patch("httpx.AsyncClient", lambda *_a, **_k: fake):
        await client._fetch_file_content(message, files)

    served = fake.streams[0].served
    assert served < len(huge)  # did not pull the whole body
    assert served <= client._FILE_TEXT_MAX_BYTES + 4096
    assert "big.bin" in message["content"]


async def test_file_fetch_api_failure_falls_back():
    """get_private_file_url raising -> fall back to a bare file mark, no crash."""
    client = _client()
    message = _file_msg()
    files = [{"file_id": "fid1", "name": "a.md", "url": "", "busid": 0}]
    with patch.object(client, "get_private_file_url", AsyncMock(side_effect=RuntimeError)):
        await client._fetch_file_content(message, files)
    assert "a.md" in message["content"]
    assert "design doc" not in message["content"]


async def test_file_fetch_no_cq_code_appends():
    """Content without a CQ file code (array-format empty raw_message) -> the
    readable form is appended."""
    client = _client()
    message = {
        "message_type": "private",
        "group_id": "",
        "raw_message": "",
        "content": "",
    }
    files = [{"file_id": "fid1", "name": "a.md", "url": "https://dl/a.md", "busid": 0}]
    with _patch_http(_FakeResp(b"hello")):
        await client._fetch_file_content(message, files)
    assert "hello" in message["content"]
    assert "a.md" in message["content"]
