from __future__ import annotations

from collections.abc import Callable
from typing import Any


AddWord = Callable[..., dict[str, Any]]


def normalize_csv_fieldnames(fieldnames: list[str] | None) -> list[str] | None:
    if not fieldnames:
        return fieldnames
    return [str(name or "").lstrip("\ufeff").strip().lower() for name in fieldnames]


def import_word_rows(
    add_word: AddWord, *, deck_id: str, rows: list[dict[str, Any]], line_offset: int
) -> dict[str, Any]:
    imported = 0
    updated = 0
    skipped: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        line = index + line_offset
        word = str(row.get("word") or "").strip()
        meaning = str(row.get("meaning") or "").strip()
        if not word or not meaning:
            if not any(str(value or "").strip() for value in row.values()):
                continue
            skipped.append({"line": line, "reason": "word and meaning are required"})
            continue
        result = add_word(
            deck_id=deck_id,
            word=word,
            meaning=meaning,
            example_sentence=str(row.get("example_sentence") or ""),
            pronunciation=str(row.get("pronunciation") or ""),
            tags=row.get("tags") or [],
        )
        if result.get("created"):
            imported += 1
        else:
            updated += 1
        items.append(result["item"])
    return {
        "imported_count": imported,
        "updated_count": updated,
        "skipped_rows": skipped,
        "items": items,
        "preview": items[:10],
    }


__all__ = ["import_word_rows", "normalize_csv_fieldnames"]
