from __future__ import annotations

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


__all__ = [
    "build_cloze_prompt",
    "normalize_tags",
    "split_passage_text",
]
