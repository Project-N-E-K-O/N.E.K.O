from __future__ import annotations

from json import dumps
from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.adapters.neko_dispatcher import NekoDispatcher
from plugin.plugins.neko_live.core.contracts import LiveConfig, ViewerEvent, ViewerIdentity, ViewerProfile
from plugin.plugins.neko_live.core.meme_knowledge import (
    DEFAULT_MEME_KNOWLEDGE_PATH,
    load_meme_knowledge,
    meme_knowledge_metadata,
    render_meme_knowledge_block,
    retrieve_meme_knowledge,
)
from plugin.plugins.neko_live.modules.danmaku_response import DanmakuResponseModule


def test_meme_knowledge_retrieves_matching_entry() -> None:
    entries = retrieve_meme_knowledge("猫猫我真的蚌埠住了")

    assert entries
    assert entries[0].id == "bengbu"


def test_default_meme_knowledge_json_is_loaded() -> None:
    entries = load_meme_knowledge(DEFAULT_MEME_KNOWLEDGE_PATH)

    assert len(entries) >= 10
    assert {entry.id for entry in entries} >= {"bengbu", "dianzi_zhacai", "jichu_bujichu"}
    assert all(tag.startswith("meme:") for entry in entries for tag in entry.tags)


def test_meme_knowledge_loader_ignores_bad_json(tmp_path) -> None:
    path = tmp_path / "meme_knowledge.json"
    path.write_text("{bad json", encoding="utf-8")

    assert load_meme_knowledge(path) == ()


def test_meme_knowledge_loader_skips_malformed_entries(tmp_path) -> None:
    path = tmp_path / "meme_knowledge.json"
    path.write_text(
        dumps(
            {
                "entries": [
                    {"id": "missing_required_fields"},
                    {
                        "id": "ok",
                        "label": "可用条目",
                        "triggers": ["可用"],
                        "tags": ["test"],
                        "hint": "Use this only in tests.",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = load_meme_knowledge(path)

    assert [entry.id for entry in entries] == ["ok"]


def test_meme_knowledge_retrieves_recent_phrase_entry() -> None:
    entries = retrieve_meme_knowledge("这个功能看起来基础，实际就不基础")

    assert entries
    assert entries[0].id == "jichu_bujichu"


def test_meme_knowledge_does_not_match_generic_classification_tags() -> None:
    entries = retrieve_meme_knowledge(
        "Use this idle beat as a light tease, then invite a short reply."
    )

    assert entries == []


def test_meme_knowledge_metadata_is_compact() -> None:
    entries = retrieve_meme_knowledge("这波电子榨菜太下饭了")
    metadata = meme_knowledge_metadata(entries)

    assert metadata["meme_hint_ids"] == "dianzi_zhacai"
    assert "watching" in metadata["meme_hint_tags"]


def test_meme_knowledge_block_is_optional_advisory() -> None:
    block = render_meme_knowledge_block(retrieve_meme_knowledge("尊嘟假嘟"))

    assert "Meme knowledge hints" in block
    assert "Use at most one hint" in block
    assert "Do not explain meme origins" in block


def test_danmaku_response_prompt_includes_meme_knowledge_hint() -> None:
    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=LiveConfig(roast_strength="normal", dry_run=True))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="猫猫我真的蚌埠住了",
        source="live_danmaku",
        live_mode="solo_stream",
    )

    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )

    assert "Meme knowledge hints" in request.prompt_text
    assert "蚌埠住了" in request.prompt_text
    assert request.metadata["meme_hint_ids"] == "bengbu"
    assert "reaction" in request.metadata["meme_hint_tags"]


@pytest.mark.asyncio
async def test_dispatcher_carries_meme_hint_metadata() -> None:
    class Plugin:
        def __init__(self):
            self.metadata = None

        def push_message(self, **kwargs):
            self.metadata = kwargs["metadata"]

    module = DanmakuResponseModule()
    module.ctx = SimpleNamespace(config=LiveConfig(roast_strength="normal", dry_run=False))
    event = ViewerEvent(
        uid="42",
        nickname="viewer",
        danmaku_text="这波电子榨菜太下饭了",
        source="live_danmaku",
        live_mode="solo_stream",
    )
    request = module.build_request(
        event,
        ViewerIdentity(uid="42", nickname="viewer"),
        ViewerProfile(uid="42", nickname="viewer", roast_count=1),
    )
    plugin = Plugin()

    await NekoDispatcher(plugin).push_roast(request)

    assert plugin.metadata["meme_hint_ids"] == "dianzi_zhacai"
    assert "watching" in plugin.metadata["meme_hint_tags"]
