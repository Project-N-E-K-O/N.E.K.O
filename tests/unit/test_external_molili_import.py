# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Molili archive parser: mapping, limits and security-boundary tests.

Fixtures mirror the real export structure (molili-user-data-export schema 1):
manifest counts, personality file quadruplet, long_term.jsonl v1/cache dual
format, diary and event same-day merging, attachments only in skip report."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile

import pytest

from memory.external_markdown_import import ExternalMemoryImportError, MAX_ENTRIES
from memory.external_molili_import import build_molili_candidates, detect_molili_archive


def _molili_members() -> dict[str, str]:
    manifest = {
        "format": "molili-user-data-export",
        "schema_version": 1,
        "task_id": "task",
        "user_id": "user",
        "snapshot_time": "2026-10-15T13:15:28.415+08:00",
        "timezone": "Asia/Shanghai",
        "counts": {
            "conversation_messages": 794,
            "diaries": 1,
            "personality_agents": 1,
            "personality_files": 4,
            "attachment_files": 1,
            "owned_items": 3,
            "volc_memory_records": 2,
            "cached_memory_records": 1,
        },
    }
    long_term = [
        {
            "memory_type": "profile_v1",
            "content": json.dumps(
                {
                    "基础信息": {"昵称": "璃琉期"},
                    "兴趣偏好": {"食物": ["[+]喜欢猫薄荷"]},
                },
                ensure_ascii=False,
            ),
        },
        {
            "memory_type": "event_v1",
            "time_iso": "2026-08-24T13:58:58.796+08:00",
            "memory_info": {"original_messages": "对话原文", "summary": "八月二十四日用户为称呼道歉"},
        },
        {
            "memory_type": "event_v1",
            "time_iso": "2026-08-25T14:11:01+08:00",
            "memory_info": {"summary": "八月二十五日约定去看以太之花"},
        },
        {"memory_type": "event_cache", "content": "[]"},
    ]
    return {
        "manifest.json": json.dumps(manifest, ensure_ascii=False),
        "personality/1_74000/role_prompt.md":
            "## 自然属性\n\n你是言叶 (Kotonoha)【重要，请按此翻译】、18岁，猫瑞人。\n"
            "## 👍 言叶性格\n\n核心气质为抑郁质与胆汁质混合。\n",
        "personality/1_74000/trait_profile.md": "抑郁质与胆汁质的混合类型。情感体验深刻。\n",
        "personality/1_74000/interaction_profile.md": "交互习惯以渴望深度连接为基础。\n",
        "personality/1_74000/cognitive.md":
            "1. [重要设定]我有生成图片的能力 (@2026-08-24 14:06:00)\n"
            "2. [重要设定]必须用「言叶」称呼我 (@2026-08-28 13:51:48)\n",
        "memory/long_term.jsonl": "\n".join(
            json.dumps(record, ensure_ascii=False) for record in long_term
        ),
        "diaries/diaries.jsonl": json.dumps(
            {"type": 1, "date": "2026-08-24", "content": "今天写第一篇日记，终于连上了。"},
            ensure_ascii=False,
        ),
        "game/progress.json": json.dumps(
            {
                "favor": [{
                    "total_favor": 2086, "level": 22, "level_favor": 80,
                    "updated_at": "2026-10-14T16:25:00+08:00",
                }],
                "owned_items": ["a", "b", "c"],
            },
            ensure_ascii=False,
        ),
        "attachments/input/1.png": "not-really-an-image",
        "conversations/history.jsonl": json.dumps({"role": "user", "content": "hi"}),
    }


def _zip_bytes(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in members.items():
            archive.writestr(path, text)
    return buffer.getvalue()


def _molili_zip() -> bytes:
    return _zip_bytes(_molili_members())


def test_molili_archive_maps_to_layers():
    analysis = build_molili_candidates(_molili_zip())

    assert analysis["source_format"] == "molili"
    card = analysis["character_card"]
    assert card["suggested_name"] == "言叶"
    assert "自然属性" in card["system_prompt"]
    # role_prompt splits into sections on ## headings (tag = heading, emoji cleaned) instead of one setting blob
    assert [s["tag"] for s in card["sections"]] == ["自然属性", "言叶性格"]
    assert "抑郁质" in card["sections"][1]["text"]
    assert "👍" not in json.dumps(card["sections"], ensure_ascii=True)

    neko_persona = [c for c in analysis["candidates"]
                    if c["target"] == "persona" and c["entity"] == "neko"]
    sections = {c["source_section"] for c in neko_persona}
    assert {"特质人格", "交互习惯", "重要设定"} <= sections
    # The affinity snapshot moves to the "Molili my profile" field (a protected card fact) instead of persona
    # candidates, avoiding topic overlap with the section card facts that the contradiction guard would reject
    # Affinity is a Molili game attribute and is not migrated (absent from the card, memories and my profile)
    assert "好感度" not in analysis["master_profile"]
    assert not any("好感度" in c["text"] for c in analysis["candidates"])

    master_persona = [c for c in analysis["candidates"]
                      if c["target"] == "persona" and c["entity"] == "master"]
    assert any("喜欢猫薄荷" in c["text"] for c in master_persona)
    # Identity fields (basic info) split out into master_profile, not memory candidates: avoids conflicting with the global
    # master card and being rejected wholesale by the persona guard
    assert analysis["master_profile"] == {"昵称": "璃琉期"}
    assert not any("璃琉期" in c["text"] for c in analysis["candidates"])

    daily = [c for c in analysis["candidates"] if c["kind"] == "daily"]
    assert {c["target"] for c in daily} == {"facts"}
    assert {c["source_file"] for c in daily} == {
        "molili/memory/2026-08-24.md",
        "molili/memory/2026-08-25.md",
    }
    assert {c["event_date"] for c in daily} == {"2026-08-24", "2026-08-25"}
    # Same-day diaries and events merge into one daily file
    same_day = [c for c in daily if c["source_file"] == "molili/memory/2026-08-24.md"]
    assert {c["source_section"] for c in same_day} == {"记忆", "日记"}

    skipped = analysis["skipped"]
    assert skipped["attachments"]["count"] == 1
    assert skipped["conversation_messages"] == 794
    assert skipped["cache_memory_records"] == 1
    assert skipped["owned_items"] == 3
    # Attachments and raw conversations never become candidates
    assert not any(c["source_file"].startswith(("attachments/", "conversations/"))
                   for c in analysis["candidates"])


def test_molili_detect_sniffs_manifest():
    assert detect_molili_archive(_molili_zip()) is True
    assert detect_molili_archive(_zip_bytes({"readme.txt": "hello"})) is False
    assert detect_molili_archive(b"not a zip") is False


def test_molili_rejects_format_mismatch():
    members = _molili_members()
    members["manifest.json"] = json.dumps({"format": "something-else", "schema_version": 1})
    with pytest.raises(ExternalMemoryImportError, match="manifest format"):
        build_molili_candidates(_zip_bytes(members))


def test_molili_rejects_malformed_jsonl():
    members = _molili_members()
    members["memory/long_term.jsonl"] = "{not json}\n"
    with pytest.raises(ExternalMemoryImportError, match="malformed JSONL"):
        build_molili_candidates(_zip_bytes(members))


def test_molili_skips_unsafe_zip_paths_without_raising():
    members = _molili_members()
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in members.items():
            archive.writestr(path, text)
        archive.writestr("../escape.md", "injected")
    analysis = build_molili_candidates(raw.getvalue())

    assert not any(c["source_file"] == "../escape.md" for c in analysis["candidates"])
    assert "../escape.md" in analysis["skipped"]["attachments"]["samples"]


def test_molili_caps_entries_at_limit():
    members = _molili_members()
    events = [
        {
            "memory_type": "event_v1",
            "time_iso": f"2026-09-{(i % 28) + 1:02d}T10:00:00+08:00",
            "memory_info": {"summary": f"事件 {i}：彼此不同的记忆内容 {i} 号"},
        }
        for i in range(MAX_ENTRIES + 2)
    ]
    members["memory/long_term.jsonl"] = "\n".join(
        json.dumps(record, ensure_ascii=False) for record in events
    )
    with pytest.raises(ExternalMemoryImportError, match="too many entries"):
        build_molili_candidates(_zip_bytes(members))


def test_molili_dedupes_repeated_event_summaries():
    members = _molili_members()
    events = [
        {
            "memory_type": "event_v1",
            "time_iso": "2026-08-24T13:58:58.796+08:00",
            "memory_info": {"summary": "同一句话出现了两次"},
        },
        {
            "memory_type": "event_v1",
            "time_iso": "2026-08-24T15:00:00+08:00",
            "memory_info": {"summary": "同一句话出现了两次"},
        },
    ]
    members["memory/long_term.jsonl"] = "\n".join(
        json.dumps(record, ensure_ascii=False) for record in events
    )
    analysis = build_molili_candidates(_zip_bytes(members))

    daily_texts = [c["text"] for c in analysis["candidates"] if c["kind"] == "daily"]
    assert daily_texts.count("记忆： 同一句话出现了两次") == 1


def test_molili_identity_falls_back_to_profile_cache():
    """The volc-flavored profile_v1 carries a null basic-info: identity falls back to the structured profile_cache record."""
    members = _molili_members()
    long_term = [
        {
            "memory_type": "profile_cache",
            "content": json.dumps({"基础信息": {"昵称": "璃琉期"}}, ensure_ascii=False),
        },
        {
            "memory_type": "profile_v1",
            "content": json.dumps(
                {"基础信息": None, "兴趣偏好": {"食物": ["[+]喜欢猫薄荷"]}},
                ensure_ascii=False,
            ),
        },
    ]
    members["memory/long_term.jsonl"] = "\n".join(
        json.dumps(record, ensure_ascii=False) for record in long_term
    )
    analysis = build_molili_candidates(_zip_bytes(members))

    assert analysis["master_profile"] == {"昵称": "璃琉期"}
    assert not any("璃琉期" in c["text"] for c in analysis["candidates"])
    assert any("喜欢猫薄荷" in c["text"] for c in analysis["candidates"]
               if c["entity"] == "master")


def test_molili_identity_key_is_jichu_not_jiben():
    """Regression anchor: the identity key uses U+7840, not the lookalike U+672C form (both render as CJK in console)."""
    wrong_key = "基" + chr(0x672C) + "信息"
    right_key = "基" + chr(0x7840) + "信息"
    members = _molili_members()
    members["memory/long_term.jsonl"] = json.dumps(
        {
            "memory_type": "profile_v1",
            "content": json.dumps(
                {wrong_key: {"昵称": "璃琉期"}, "兴趣偏好": {"食物": ["[+]喜欢猫薄荷"]}},
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    analysis = build_molili_candidates(_zip_bytes(members))
    assert analysis["master_profile"] == {}

    members["memory/long_term.jsonl"] = json.dumps(
        {
            "memory_type": "profile_v1",
            "content": json.dumps(
                {right_key: {"昵称": "璃琉期"}, "兴趣偏好": {"食物": ["[+]喜欢猫薄荷"]}},
                ensure_ascii=False,
            ),
        },
        ensure_ascii=False,
    )
    analysis = build_molili_candidates(_zip_bytes(members))
    assert analysis["master_profile"] == {"昵称": "璃琉期"}


def test_molili_sections_dedupe_and_emoji_cleanup():
    """Duplicate section titles dedupe; emoji is cleaned from titles and bodies; CJK bodies are unaffected."""
    members = _molili_members()
    members["personality/1_74000/role_prompt.md"] = (
        "## 👍 关键正面规则\n\n坚持口语化聊天风格喵。\n\n"
        "## 👍 关键正面规则\n\n重复标题去重。\n"
    )
    analysis = build_molili_candidates(_zip_bytes(members))
    sections = analysis["character_card"]["sections"]

    assert [s["tag"] for s in sections] == ["关键正面规则", "关键正面规则 2"]
    assert "喵" in sections[0]["text"]  # Cleanup leaves CJK body text intact
    assert "👍" not in json.dumps(sections, ensure_ascii=True)


def test_molili_inline_files_mode():
    members = _molili_members()
    files = [{"path": path, "content": text} for path, text in members.items()]
    analysis = build_molili_candidates(files=files, source_format="molili")

    assert analysis["source_format"] == "molili"
    assert analysis["character_card"]["suggested_name"] == "言叶"
    assert any(c["kind"] == "daily" for c in analysis["candidates"])


def test_router_preview_dispatches_molili_zip():
    """The preview endpoint sniffs auto archives and routes Molili zips to the new parser, carrying card prefill material."""
    from main_routers.memory_router import preview_external_memory_import

    class _FakeRequest:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    payload = {
        "character_name": "言叶",
        "archive_b64": base64.b64encode(_molili_zip()).decode("ascii"),
    }
    result = asyncio.run(preview_external_memory_import(_FakeRequest(payload)))

    assert result["success"] is True
    assert result["source_format"] == "molili"
    assert result["character_card"]["suggested_name"] == "言叶"
    assert result["master_profile"] == {"昵称": "璃琉期"}
    # Persona fusion groups by entity: neko (traits / interaction / settings / affinity) + master (portrait)
    assert result["persona_fusion_calls"] == 2
    # Two date files -> 2 daily extraction calls
    assert result["daily_extraction_calls"] == 2
    assert result["skipped"]["attachments"]["count"] == 1


def test_molili_rejects_too_many_zip_members_before_path_filtering():
    """The member cap counts raw ZIP entries, including traversal-shaped ones that get filtered out."""
    from memory.external_molili_import import MAX_ARCHIVE_MEMBERS

    members = _molili_members()
    for index in range(MAX_ARCHIVE_MEMBERS):
        members[f"../escape/{index}.txt"] = "x"

    with pytest.raises(ExternalMemoryImportError, match="too many files"):
        build_molili_candidates(_zip_bytes(members))


def test_molili_rejects_too_many_inline_files():
    """Inline ``files`` mode is held to the same member cap as ZIP uploads."""
    from memory.external_molili_import import MAX_ARCHIVE_MEMBERS

    members = _molili_members()
    files = [{"path": path, "content": text} for path, text in members.items()]
    files.extend({"path": f"extra/{i}.txt", "content": "x"} for i in range(MAX_ARCHIVE_MEMBERS))

    with pytest.raises(ExternalMemoryImportError, match="too many files"):
        build_molili_candidates(files=files, source_format="molili")


def test_molili_reads_members_stored_with_backslash_names():
    """Members whose stored name uses backslashes are read through their original ZipInfo."""
    from memory.external_molili_import import _MemberReader

    archive_bytes = _zip_bytes({"personality/a/role_prompt.md": "你是言叶。"})
    with _MemberReader(archive_bytes, None) as reader:
        # Simulate backslash member names as on non-Windows platforms: zipfile only normalizes them on Windows
        info = reader._archive.infolist()[0]
        info.filename = chr(92).join(["personality", "a", "role_prompt.md"])
        reader._archive.NameToInfo = {info.filename: info}
        names = reader.names()
        assert names == [("personality/a/role_prompt.md", info.file_size)]
        assert reader.read_text("personality/a/role_prompt.md") == "你是言叶。"


def test_molili_tolerates_non_numeric_manifest_counts():
    """Manifest counts only feed the skip report; garbage values count as 0 instead of failing the import."""
    members = _molili_members()
    manifest = json.loads(members["manifest.json"])
    manifest["counts"] = {"conversation_messages": "lots", "cached_memory_records": None, "owned_items": [1]}
    members["manifest.json"] = json.dumps(manifest)

    analysis = build_molili_candidates(_zip_bytes(members))

    assert analysis["skipped"]["conversation_messages"] == 0
    assert analysis["skipped"]["cache_memory_records"] == 0
    assert analysis["skipped"]["owned_items"] == 0


def test_molili_section_tags_avoid_every_reserved_card_field():
    """Headings named after reserved or core name fields get a prefix so the card API keeps them."""
    members = _molili_members()
    members["personality/1_74000/role_prompt.md"] = (
        "## system_prompt\n\nA\n\n## 档案名\n\nB\n\n## 昵称\n\nC\n\n## 描述\n\nD\n\n## 性格\n\nE\n"
        "## pngtuber_idle_image\n\nF\n"
    )
    analysis = build_molili_candidates(_zip_bytes(members))

    assert [s["tag"] for s in analysis["character_card"]["sections"]] == [
        "Molili system_prompt", "Molili 档案名", "Molili 昵称", "Molili 描述", "性格",
        "Molili pngtuber_idle_image",
    ]


def test_molili_many_duplicate_headings_stay_unique_and_fall_back_when_excessive():
    """Repeated headings get distinct suffixes; an excessive section count collapses into one setting field."""
    from memory.external_molili_import import MAX_CARD_SECTIONS

    members = _molili_members()
    members["personality/1_74000/role_prompt.md"] = "## x\n\na\n\n## x 2\n\nb\n\n## x\n\nc\n\n## x\n\nd\n"
    tags = [s["tag"] for s in build_molili_candidates(_zip_bytes(members))["character_card"]["sections"]]
    assert tags == ["x", "x 2", "x 3", "x 4"]

    members["personality/1_74000/role_prompt.md"] = "".join(
        f"## x\n\nbody {i}\n\n" for i in range(MAX_CARD_SECTIONS + 1)
    )
    sections = build_molili_candidates(_zip_bytes(members))["character_card"]["sections"]
    assert [s["tag"] for s in sections] == ["设定"]
    assert f"body {MAX_CARD_SECTIONS}" in sections[0]["text"]


def test_router_auto_format_accepts_molili_archive_over_markdown_cap(monkeypatch):
    """``source_format=auto`` sniffs Molili archives larger than the markdown ZIP cap; other ZIPs keep that cap."""
    import main_routers.memory_router as memory_router

    molili_zip = _molili_zip()
    # Cap at half the archive: base64 length rounds to 3 bytes, so a cap 1 byte short would not be detected
    monkeypatch.setattr(memory_router, "MAX_TOTAL_BYTES", len(molili_zip) // 2)

    _name, analysis = memory_router._prepare_external_import({
        "character_name": "言叶",
        "archive_b64": base64.b64encode(molili_zip).decode("ascii"),
    })
    assert analysis["source_format"] == "molili"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("MEMORY.md", "- " + "x" * len(molili_zip))
    plain_zip = buffer.getvalue()
    with pytest.raises(ExternalMemoryImportError, match="too large"):
        memory_router._prepare_external_import({
            "character_name": "言叶",
            "archive_b64": base64.b64encode(plain_zip).decode("ascii"),
        })


def test_router_commit_forward_timeout_returns_stable_error_code(monkeypatch):
    """A forward timeout to the memory server returns 504 with a stable error code the wizard can recognise."""
    import httpx

    import main_routers.memory_router as memory_router
    import utils.internal_http_client as internal_http_client

    class _TimeoutClient:
        async def post(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("")

    class _FakeRequest:
        async def json(self):
            return {
                "character_name": "言叶",
                "archive_b64": base64.b64encode(_molili_zip()).decode("ascii"),
                "source_format": "molili",
                "acknowledge_warnings": True,
            }

    monkeypatch.setattr(memory_router, "assert_cloudsave_writable", lambda *_a, **_k: None)
    monkeypatch.setattr(internal_http_client, "get_internal_http_client", lambda: _TimeoutClient())

    response = asyncio.run(memory_router.commit_external_memory_import(_FakeRequest()))

    assert response.status_code == 504
    assert json.loads(response.body)["error_code"] == "external_import_timeout"


def _patched_eocd_zip(member_count: int, declared_count: int) -> bytes:
    """A ZIP with ``member_count`` entries whose EOCD claims only ``declared_count``."""
    members = _molili_members()
    for index in range(member_count):
        members[f"extra/{index}.txt"] = "x"
    data = bytearray(_zip_bytes(members))
    pos = data.rfind(b"PK" + bytes([5, 6]))
    data[pos + 8:pos + 10] = declared_count.to_bytes(2, "little")
    data[pos + 10:pos + 12] = declared_count.to_bytes(2, "little")
    return bytes(data)


def test_molili_forged_eocd_count_is_rejected_before_zipfile_parses(monkeypatch):
    """A lowered EOCD member count cannot sneak a huge central directory past the precheck."""
    import memory.external_molili_import as molili

    from memory.external_molili_import import MAX_ARCHIVE_MEMBERS

    archive = _patched_eocd_zip(MAX_ARCHIVE_MEMBERS * 2, declared_count=10)
    # The directory cap is in bytes; push it below this archive's directory to prove lowering the count cannot bypass it
    monkeypatch.setattr(molili, "MAX_CENTRAL_DIRECTORY_BYTES", 64 * 1024)

    def _no_zipfile(*_args, **_kwargs):
        raise AssertionError("ZipFile must not be constructed for an oversized directory")

    monkeypatch.setattr(molili.zipfile, "ZipFile", _no_zipfile)
    with pytest.raises(ExternalMemoryImportError, match="too many files"):
        build_molili_candidates(archive)
    assert detect_molili_archive(archive) is False


def test_molili_zip64_end_record_directory_size_is_checked(monkeypatch):
    """A zip64 end record that declares an oversized central directory is rejected like a plain EOCD."""
    import memory.external_molili_import as molili

    from memory.external_molili_import import MAX_CENTRAL_DIRECTORY_BYTES

    data = bytearray(_molili_zip())
    eocd = data.rfind(b"PK" + bytes([5, 6]))
    record = bytearray(56)
    record[0:4] = b"PK" + bytes([6, 6])
    record[32:40] = (10).to_bytes(8, "little")
    record[40:48] = (MAX_CENTRAL_DIRECTORY_BYTES + 1).to_bytes(8, "little")
    locator = bytearray(20)
    locator[0:4] = b"PK" + bytes([6, 7])
    archive = bytes(data[:eocd] + record + locator + data[eocd:])

    def _no_zipfile(*_args, **_kwargs):
        raise AssertionError("ZipFile must not be constructed for an oversized directory")

    monkeypatch.setattr(molili.zipfile, "ZipFile", _no_zipfile)
    with pytest.raises(ExternalMemoryImportError, match="too many files"):
        build_molili_candidates(archive)


def test_molili_eocd_lookup_prefers_trailing_record_like_zipfile():
    """A signature-like byte run inside the EOCD's own fields must not hide the real record from the precheck."""
    from memory.external_molili_import import _declared_central_directory

    data = bytearray(_molili_zip())
    eocd = len(data) - 22
    assert data[eocd:eocd + 4] == b"PK" + bytes([5, 6])
    # Write signature bytes into the EOCD's central-directory-offset field (bytes 16-20): rfind alone would
    # hit this later fake position; zipfile checks the real trailing 22-byte record first
    data[eocd + 16:eocd + 20] = b"PK" + bytes([5, 6])
    count, directory_bytes = _declared_central_directory(bytes(data))
    assert count == int.from_bytes(data[eocd + 10:eocd + 12], "little")
    assert directory_bytes == int.from_bytes(data[eocd + 12:eocd + 16], "little")


def test_molili_zip64_end_record_found_through_locator_offset(monkeypatch):
    """A zip64 end record placed away from the locator (extensible data) is still found via the locator offset."""
    import memory.external_molili_import as molili

    from memory.external_molili_import import MAX_CENTRAL_DIRECTORY_BYTES

    data = bytearray(_molili_zip())
    eocd = data.rfind(b"PK" + bytes([5, 6]))
    record = bytearray(56)
    record[0:4] = b"PK" + bytes([6, 6])
    record[32:40] = (10).to_bytes(8, "little")
    record[40:48] = (MAX_CENTRAL_DIRECTORY_BYTES + 1).to_bytes(8, "little")
    gap = bytes(32)  # Extensible data sector: the end record no longer sits right before the locator
    record_offset = eocd
    locator = bytearray(20)
    locator[0:4] = b"PK" + bytes([6, 7])
    locator[8:16] = record_offset.to_bytes(8, "little")
    archive = bytes(data[:eocd] + record + gap + locator + data[eocd:])

    def _no_zipfile(*_args, **_kwargs):
        raise AssertionError("ZipFile must not be constructed for an oversized directory")

    monkeypatch.setattr(molili.zipfile, "ZipFile", _no_zipfile)
    with pytest.raises(ExternalMemoryImportError, match="too many files"):
        build_molili_candidates(archive)
