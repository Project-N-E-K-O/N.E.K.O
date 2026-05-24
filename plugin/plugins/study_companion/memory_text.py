from __future__ import annotations

import difflib
import re
from typing import Any


def normalize_tags(value: object, *, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        raw_items: list[object] = re.split(r"[,，;；\s]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        tag = str(raw or "").strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag[:40])
        if len(tags) >= limit:
            break
    return tags


def split_passage_text(text: str) -> list[dict[str, Any]]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    paragraphs = [
        item.strip()
        for item in re.split(r"(?:\r?\n\s*){2,}", normalized)
        if item.strip()
    ]
    if not paragraphs:
        paragraphs = [normalized]
    chunks: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        paragraph_chunks = [
            paragraph[index : index + 5000] for index in range(0, len(paragraph), 5000)
        ] or [paragraph]
        for chunk_index, chunk in enumerate(paragraph_chunks, start=1):
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[。！？.!?])\s*", chunk)
                if item.strip()
            ]
            chunks.append(
                {
                    "paragraph_index": paragraph_index,
                    "chunk_index": chunk_index,
                    "text": chunk,
                    "sentences": sentences or [chunk],
                }
            )
    return chunks


def build_cloze_prompt(sentence: str) -> dict[str, str]:
    text = str(sentence or "").strip()
    if not text:
        return {"prompt": "", "answer": "", "hint": ""}
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}|\S", text)
    candidate = ""
    for token in words:
        if re.fullmatch(r"[A-Za-z][A-Za-z'-]{3,}", token):
            candidate = token
            break
    if not candidate:
        midpoint = max(1, len(text) // 2)
        candidate = text[midpoint : midpoint + 1]
    prompt = text.replace(candidate, "____", 1)
    return {"prompt": prompt, "answer": candidate, "hint": candidate[:1]}


def diff_recitation(
    expected: str, actual: str, *, hint_count: int = 0
) -> dict[str, Any]:
    target = str(expected or "")[:5000]
    user_input = str(actual or "")[:5000]
    matcher = difflib.SequenceMatcher(a=target, b=user_input, autojunk=False)
    operations: list[dict[str, Any]] = []
    missing_count = 0
    extra_count = 0
    wrong_count = 0
    wrong_order_count = 0
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        expected_text = target[a_start:a_end]
        actual_text = user_input[b_start:b_end]
        if tag == "delete":
            missing = _count_units(expected_text)
            missing_count += missing
            if expected_text and expected_text in user_input[b_end:]:
                wrong_order_count += 1
            operations.append(
                {
                    "type": "missing",
                    "expected": expected_text,
                    "actual": "",
                    "count": missing,
                }
            )
        elif tag == "insert":
            extra = _count_units(actual_text)
            extra_count += extra
            if actual_text and actual_text in target[a_end:]:
                wrong_order_count += 1
            operations.append(
                {"type": "extra", "expected": "", "actual": actual_text, "count": extra}
            )
        else:
            missing = _count_units(expected_text)
            extra = _count_units(actual_text)
            missing_count += missing
            extra_count += extra
            wrong_count += max(missing, extra)
            operations.append(
                {
                    "type": "wrong",
                    "expected": expected_text,
                    "actual": actual_text,
                    "count": max(missing, extra),
                }
            )
    denominator = max(1, _count_units(target))
    penalty = (
        missing_count * 0.40
        + extra_count * 0.20
        + wrong_order_count * 0.25
        + max(0, int(hint_count or 0)) * 0.15
    )
    score = max(0.0, min(1.0, 1.0 - penalty / denominator))
    return {
        "missing_count": missing_count,
        "extra_count": extra_count,
        "wrong_count": wrong_count,
        "wrong_order_count": wrong_order_count,
        "hint_count": max(0, int(hint_count or 0)),
        "score": round(score, 4),
        "operations": operations,
    }


def _count_units(value: str) -> int:
    return len([char for char in str(value or "") if not char.isspace()])


__all__ = [
    "build_cloze_prompt",
    "diff_recitation",
    "normalize_tags",
    "split_passage_text",
]
