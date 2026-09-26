from __future__ import annotations

import importlib.util
from pathlib import Path

from knowledge.packs import validate_pack


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "build_ruozhiba_knowledge_pack.py"
)
SPEC = importlib.util.spec_from_file_location("build_ruozhiba_pack", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_conversion_is_deduplicated_explicit_only_and_safety_labelled():
    rows = [
        {"instruction": "  普通 问题  ", "output": " 普通回答 "},
        {"instruction": "普通 问题", "output": "重复回答"},
        {"instruction": "刚要跳楼怎么办", "output": "请立即向警察求助。"},
        {"instruction": "", "output": "空问题"},
    ]

    entries, excluded = MODULE.convert_rows(rows)

    assert excluded == 2
    assert [entry["title"] for entry in entries] == [
        "普通 问题",
        "刚要跳楼怎么办",
    ]
    assert entries[0]["terms"] == {"alias": [], "recognition": []}
    assert entries[0]["summary"] == ""
    assert "quality:unverified" in entries[0]["tags"]
    assert "safety:self-harm" in entries[1]["tags"]


def test_converted_rows_form_a_valid_removable_corpus_pack():
    entries, excluded = MODULE.convert_rows(
        [{"instruction": "为什么太阳会发光?", "output": "这是趣味回答。"}]
    )
    payload = {
        "schema_version": 1,
        "pack_id": "ruozhiba-qa",
        "material_type": "corpus",
        "source": {
            "name": "LooksJuicy/ruozhiba 趣味问答",
            "homepage": MODULE.SOURCE_HOMEPAGE,
            "license": "Apache-2.0; GPT-generated answers are unverified",
        },
        "entries": entries,
    }

    pack = validate_pack(payload)

    assert excluded == 0
    assert pack.material_type == "corpus"
    assert pack.source_tag == "source:community.ruozhiba-qa"
    assert pack.entries[0].recognition_terms == ()
