"""Build a local N.E.K.O knowledge pack from LooksJuicy/ruozhiba.

The source file is intentionally supplied by the caller.  Runtime knowledge
code never downloads community content, and the generated pack remains an
explicitly installed, removable ``corpus`` knowledge pack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge.packs import PACK_SCHEMA_VERSION


SOURCE_HOMEPAGE = "https://huggingface.co/datasets/LooksJuicy/ruozhiba"
SOURCE_REVISION = "2a39d86721e0109a7c598a25a1338e297c639d2f"
SOURCE_SHA256 = "d26d609499b3de2cb4987f73f138cd986eac79574579b36bd9fae10b7aa589bc"
EXPECTED_SOURCE_ROWS = 1_496

BASE_TAGS = (
    "dataset:ruozhiba-qa",
    "type:entertainment-qa",
    "quality:unverified",
    "generation:gpt-4",
)

# The source answers are model-generated, not an authority.  These previewed
# rows contain direct factual mistakes rather than merely debatable humour.
EXCLUDED_QUESTIONS = frozenset(
    {
        "鸡柳是鸡身上哪个部位啊?",
        "为什么四川人说自己是古蜀后人,重庆人不说自己是古巴后人呢",
    }
)

SENSITIVE_MARKERS = {
    "safety:self-harm": ("自杀", "轻生", "跳楼", "紫砂"),
    "safety:adult": ("内裤", "生殖器", "丁丁", "几把", "打胶", "强奸"),
    "safety:violence": ("杀人", "死刑", "原子弹", "劫匪", "火葬"),
    "safety:medical": ("艾滋", "癌症", "医生", "医院", "手术", "药物"),
    "safety:identity": ("黑人", "犹太", "歧视", "纳粹", "希特勒"),
}


def _single_line(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.split())


def _content_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\r\n", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safety_tags(question: str, answer: str) -> tuple[str, ...]:
    combined = f"{question}\n{answer}"
    return tuple(
        tag
        for tag, markers in SENSITIVE_MARKERS.items()
        if any(marker in combined for marker in markers)
    )


def convert_rows(rows: Iterable[object]) -> tuple[list[dict[str, object]], int]:
    """Normalize, deduplicate, label, and convert source QA rows."""
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    excluded = 0
    for row in rows:
        if not isinstance(row, dict):
            excluded += 1
            continue
        question = _single_line(row.get("instruction"))
        answer = _content_text(row.get("output"))
        key = question.casefold()
        if not question or not answer or key in seen or question in EXCLUDED_QUESTIONS:
            excluded += 1
            continue
        seen.add(key)
        entries.append(
            {
                "title": question,
                "terms": {"alias": [], "recognition": []},
                "tags": [*BASE_TAGS, *_safety_tags(question, answer)],
                "summary": "",
                "content": answer,
            }
        )
    return entries, excluded


def build_pack(source_path: Path) -> tuple[dict[str, object], int]:
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"unexpected source SHA-256: {digest}")
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != EXPECTED_SOURCE_ROWS:
        raise ValueError("unexpected source row count")
    entries, excluded = convert_rows(rows)
    return (
        {
            "schema_version": PACK_SCHEMA_VERSION,
            "pack_id": "ruozhiba-qa",
            "material_type": "corpus",
            "source": {
                "name": "LooksJuicy/ruozhiba 趣味问答",
                "homepage": SOURCE_HOMEPAGE,
                "license": (
                    "Apache-2.0; answers are GPT-4-generated and are not "
                    "authoritative factual references"
                ),
            },
            "entries": entries,
        },
        excluded,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Pinned ruozhiba_qa.json")
    parser.add_argument("output", type=Path, help="Generated N.E.K.O pack JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    pack, excluded = build_pack(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(pack, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "source_revision": SOURCE_REVISION,
                "entries": len(pack["entries"]),
                "excluded": excluded,
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
