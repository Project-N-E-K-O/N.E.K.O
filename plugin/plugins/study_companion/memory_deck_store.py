from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import re
import uuid
from typing import Any

from .fsrs_bridge import (
    FSRSBridge,
    StudyFsrsRating,
    create_card,
    rate_answer,
    retrievability,
)
from .models import json_copy


DECK_TYPES = {"word", "passage", "formula", "custom"}
ITEM_TYPES = {"word", "sentence", "paragraph", "cloze", "custom"}
WORD_ERROR_RATINGS = {
    "unknown_word": StudyFsrsRating.Again,
    "spelling": StudyFsrsRating.Hard,
    "meaning_confused": StudyFsrsRating.Hard,
    "example_misunderstood": StudyFsrsRating.Good,
    "correct": StudyFsrsRating.Easy,
}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def ensure_memory_schema(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            deck_type TEXT NOT NULL,
            subject TEXT,
            language TEXT,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_items (
            id TEXT PRIMARY KEY,
            deck_id TEXT NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            prompt TEXT NOT NULL,
            answer TEXT NOT NULL,
            metadata_json TEXT,
            fsrs_card_id INTEGER REFERENCES memory_fsrs_cards(id) ON DELETE SET NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_fsrs_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE REFERENCES memory_items(id) ON DELETE CASCADE,
            card_data TEXT NOT NULL,
            fsrs_state TEXT DEFAULT 'new',
            last_rating INTEGER,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_review_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES memory_fsrs_cards(id),
            rating INTEGER,
            scheduled_days INTEGER,
            actual_days INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            elapsed_ms INTEGER,
            error_type TEXT,
            reviewed_at TEXT DEFAULT (datetime('now')),
            session_id TEXT REFERENCES sessions(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recitation_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passage_item_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
            review_record_id INTEGER REFERENCES review_records(id) ON DELETE SET NULL,
            user_input_text TEXT NOT NULL,
            missing_count INTEGER DEFAULT 0,
            extra_count INTEGER DEFAULT 0,
            wrong_order_count INTEGER DEFAULT 0,
            hint_count INTEGER DEFAULT 0,
            score REAL,
            reviewed_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_deck ON memory_items(deck_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_items_card ON memory_items(fsrs_card_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_fsrs_cards_item ON memory_fsrs_cards(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_review_log_item ON memory_review_log(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_review_log_card ON memory_review_log(card_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_records_item ON review_records(item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_records_session ON review_records(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recitation_attempts_item ON recitation_attempts(passage_item_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recitation_attempts_review ON recitation_attempts(review_record_id)"
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_word_dedupe
        ON memory_items(deck_id, prompt)
        WHERE item_type = 'word'
        """
    )


def normalize_deck_type(value: object) -> str:
    text = str(value or "custom").strip().lower()
    return text if text in DECK_TYPES else "custom"


def normalize_item_type(value: object) -> str:
    text = str(value or "custom").strip().lower()
    return text if text in ITEM_TYPES else "custom"


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


def _count_units(value: str) -> int:
    return len([char for char in str(value or "") if not char.isspace()])


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


def rating_from_word_result(
    error_type: str, *, correct: bool | None = None
) -> StudyFsrsRating:
    if correct is True:
        return StudyFsrsRating.Easy
    normalized = str(error_type or "").strip().lower()
    return WORD_ERROR_RATINGS.get(
        normalized, StudyFsrsRating.Good if correct else StudyFsrsRating.Again
    )


def rating_from_recitation_score(score: float) -> StudyFsrsRating:
    value = max(0.0, min(1.0, float(score or 0.0)))
    if value >= 0.92:
        return StudyFsrsRating.Easy
    if value >= 0.70:
        return StudyFsrsRating.Good
    if value >= 0.40:
        return StudyFsrsRating.Hard
    return StudyFsrsRating.Again


def normalize_rating(value: str | int | StudyFsrsRating) -> StudyFsrsRating:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "again": StudyFsrsRating.Again,
            "forgot": StudyFsrsRating.Again,
            "unknown_word": StudyFsrsRating.Again,
            "hard": StudyFsrsRating.Hard,
            "spelling": StudyFsrsRating.Hard,
            "meaning_confused": StudyFsrsRating.Hard,
            "good": StudyFsrsRating.Good,
            "example_misunderstood": StudyFsrsRating.Good,
            "easy": StudyFsrsRating.Easy,
            "correct": StudyFsrsRating.Easy,
        }
        if normalized in aliases:
            return aliases[normalized]
    try:
        return StudyFsrsRating(int(value))
    except (TypeError, ValueError):
        return StudyFsrsRating.Good


class MemoryDeckStore:
    def __init__(self, store: Any, *, retention_target: float = 0.90) -> None:
        self.store = store
        self.fsrs = FSRSBridge(retention_target=retention_target)

    def create_deck(
        self,
        *,
        name: str,
        deck_type: str = "custom",
        subject: str = "",
        language: str = "",
        source: str = "manual",
    ) -> dict[str, Any]:
        name_text = str(name or "").strip()
        if not name_text:
            raise ValueError("deck name is required")
        deck_id = str(uuid.uuid4())
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                INSERT INTO decks (id, name, deck_type, subject, language, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    deck_id,
                    name_text,
                    normalize_deck_type(deck_type),
                    str(subject or ""),
                    str(language or ""),
                    str(source or "manual"),
                ),
            )
            conn.commit()
        deck = self.get_deck(deck_id)
        if deck is None:
            raise RuntimeError("deck create failed")
        return deck

    def get_or_create_default_deck(
        self, *, deck_type: str = "custom"
    ) -> dict[str, Any]:
        deck_kind = normalize_deck_type(deck_type)
        existing = self.find_deck_by_name("Default Memory Deck", deck_type=deck_kind)
        if existing is not None:
            return existing
        return self.create_deck(
            name="Default Memory Deck", deck_type=deck_kind, source="runtime"
        )

    def find_deck_by_name(
        self, name: str, *, deck_type: str | None = None
    ) -> dict[str, Any] | None:
        name_text = str(name or "").strip()
        if not name_text:
            return None
        params: list[Any] = [name_text]
        predicate = "name = ?"
        if deck_type:
            predicate += " AND deck_type = ?"
            params.append(normalize_deck_type(deck_type))
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    f"SELECT * FROM decks WHERE {predicate} ORDER BY updated_at DESC LIMIT 1",
                    params,
                )
                .fetchone()
            )
        return self._deck_from_row(row)

    def list_decks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    """
                SELECT d.*,
                       COUNT(mi.id) AS item_count
                FROM decks d
                LEFT JOIN memory_items mi ON mi.deck_id = d.id AND mi.status = 'active'
                GROUP BY d.id
                ORDER BY d.updated_at DESC, d.created_at DESC
                LIMIT ?
                """,
                    (max(1, int(limit or 100)),),
                )
                .fetchall()
            )
        return [
            deck
            for deck in (self._deck_from_row(row) for row in rows)
            if deck is not None
        ]

    def get_deck(self, deck_id: str) -> dict[str, Any] | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    "SELECT * FROM decks WHERE id = ?",
                    (str(deck_id or ""),),
                )
                .fetchone()
            )
        return self._deck_from_row(row)

    def update_deck(
        self,
        deck_id: str,
        *,
        name: str | None = None,
        subject: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        current = self.get_deck(deck_id)
        if current is None:
            raise ValueError("deck not found")
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                UPDATE decks
                SET name = ?, subject = ?, language = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    str(
                        name if name is not None else current.get("name") or ""
                    ).strip(),
                    str(
                        subject if subject is not None else current.get("subject") or ""
                    ),
                    str(
                        language
                        if language is not None
                        else current.get("language") or ""
                    ),
                    str(deck_id or ""),
                ),
            )
            conn.commit()
        updated = self.get_deck(deck_id)
        if updated is None:
            raise RuntimeError("deck update failed")
        return updated

    def delete_deck(self, deck_id: str) -> dict[str, Any]:
        with self.store._lock:
            conn = self.store._require_conn()
            before = self._memory_counts(conn, deck_id=str(deck_id or ""))
            cursor = conn.execute(
                "DELETE FROM decks WHERE id = ?", (str(deck_id or ""),)
            )
            conn.commit()
        return {"deleted": int(cursor.rowcount or 0), "cascade": before}

    def add_word(
        self,
        *,
        deck_id: str,
        word: str,
        meaning: str,
        example_sentence: str = "",
        pronunciation: str = "",
        tags: object = None,
    ) -> dict[str, Any]:
        metadata = {
            "example_sentence": str(example_sentence or ""),
            "pronunciation": str(pronunciation or ""),
            "tags": normalize_tags(tags),
        }
        return self.upsert_item(
            deck_id=deck_id,
            item_type="word",
            prompt=word,
            answer=meaning,
            metadata=metadata,
        )

    def upsert_item(
        self,
        *,
        deck_id: str,
        item_type: str,
        prompt: str,
        answer: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deck = self.get_deck(deck_id)
        if deck is None:
            raise ValueError("deck not found")
        item_kind = normalize_item_type(item_type)
        prompt_text = str(prompt or "").strip()
        answer_text = str(answer or "").strip()
        if not prompt_text:
            raise ValueError("memory item prompt is required")
        if not answer_text:
            raise ValueError("memory item answer is required")
        metadata_payload = json_copy(metadata or {})
        with self.store._lock:
            conn = self.store._require_conn()
            existing = None
            if item_kind == "word":
                existing = conn.execute(
                    """
                    SELECT *
                    FROM memory_items
                    WHERE deck_id = ? AND item_type = 'word' AND prompt = ?
                    LIMIT 1
                    """,
                    (str(deck_id or ""), prompt_text),
                ).fetchone()
            if existing is None:
                item_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO memory_items (
                        id, deck_id, item_type, prompt, answer, metadata_json, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))
                    """,
                    (
                        item_id,
                        str(deck_id or ""),
                        item_kind,
                        prompt_text,
                        answer_text,
                        self.store._json_dumps(metadata_payload),
                    ),
                )
                created = True
            else:
                item_id = str(existing["id"])
                conn.execute(
                    """
                    UPDATE memory_items
                    SET answer = ?, metadata_json = ?, status = 'active', updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (answer_text, self.store._json_dumps(metadata_payload), item_id),
                )
                created = False
            card = self._ensure_fsrs_card_locked(conn, item_id)
            conn.execute(
                "UPDATE decks SET updated_at = datetime('now') WHERE id = ?",
                (str(deck_id or ""),),
            )
            conn.commit()
        item = self.get_item(item_id)
        if item is None:
            raise RuntimeError("memory item upsert failed")
        return {"created": created, "item": item, "fsrs_card": card}

    def import_words_csv(self, *, deck_id: str, content: str) -> dict[str, Any]:
        stream = io.StringIO(str(content or ""))
        reader = csv.DictReader(stream)
        if (
            not reader.fieldnames
            or "word" not in reader.fieldnames
            or "meaning" not in reader.fieldnames
        ):
            return {
                "imported_count": 0,
                "updated_count": 0,
                "skipped_rows": [{"line": 1, "reason": "missing word/meaning header"}],
                "items": [],
            }
        return self._import_word_rows(deck_id=deck_id, rows=list(reader), line_offset=2)

    def import_words_json(
        self, *, deck_id: str, content: str | list[dict[str, Any]]
    ) -> dict[str, Any]:
        if isinstance(content, list):
            payload = content
        else:
            try:
                parsed = json.loads(str(content or ""))
            except (ValueError, TypeError) as exc:
                return {
                    "imported_count": 0,
                    "updated_count": 0,
                    "skipped_rows": [{"line": 1, "reason": f"invalid json: {exc}"}],
                    "items": [],
                }
            payload = parsed.get("items") if isinstance(parsed, dict) else parsed
        if not isinstance(payload, list):
            return {
                "imported_count": 0,
                "updated_count": 0,
                "skipped_rows": [
                    {
                        "line": 1,
                        "reason": "json payload must be a list or {items: [...]}",
                    }
                ],
                "items": [],
            }
        rows = [item if isinstance(item, dict) else {} for item in payload]
        return self._import_word_rows(deck_id=deck_id, rows=rows, line_offset=1)

    def import_words(
        self, *, deck_id: str, content: str, fmt: str = "csv"
    ) -> dict[str, Any]:
        normalized = str(fmt or "csv").strip().lower()
        if normalized == "json":
            return self.import_words_json(deck_id=deck_id, content=content)
        return self.import_words_csv(deck_id=deck_id, content=content)

    def import_passage(
        self, *, deck_id: str, text: str, title: str = ""
    ) -> dict[str, Any]:
        chunks = split_passage_text(text)
        items: list[dict[str, Any]] = []
        for chunk in chunks:
            first_sentence = str(chunk.get("sentences", [""])[0] or "").strip()
            prompt = first_sentence[:120] or str(title or "Passage")
            metadata = {
                "title": str(title or ""),
                "paragraph_index": int(chunk.get("paragraph_index") or 0),
                "chunk_index": int(chunk.get("chunk_index") or 0),
                "sentences": chunk.get("sentences") or [],
            }
            created = self.upsert_item(
                deck_id=deck_id,
                item_type="paragraph",
                prompt=prompt,
                answer=str(chunk.get("text") or ""),
                metadata=metadata,
            )
            items.append(created["item"])
        return {"imported_count": len(items), "items": items}

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    """
                SELECT mi.*, d.name AS deck_name, d.deck_type AS deck_type
                FROM memory_items mi
                LEFT JOIN decks d ON d.id = mi.deck_id
                WHERE mi.id = ?
                """,
                    (str(item_id or ""),),
                )
                .fetchone()
            )
        item = self._item_from_row(row)
        if item is None:
            return None
        card = self.get_fsrs_card(item["id"])
        if card is not None:
            item["fsrs_card"] = card
        return item

    def list_items(
        self, *, deck_id: str = "", limit: int = 100, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        clauses: list[str] = []
        if deck_id:
            clauses.append("mi.deck_id = ?")
            params.append(str(deck_id))
        if not include_archived:
            clauses.append("mi.status = 'active'")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, int(limit or 100)))
        with self.store._lock:
            rows = (
                self.store._require_conn()
                .execute(
                    f"""
                SELECT mi.*, d.name AS deck_name, d.deck_type AS deck_type
                FROM memory_items mi
                LEFT JOIN decks d ON d.id = mi.deck_id
                {where}
                ORDER BY mi.updated_at DESC, mi.created_at DESC
                LIMIT ?
                """,
                    params,
                )
                .fetchall()
            )
        return [
            item
            for item in (self._item_from_row(row) for row in rows)
            if item is not None
        ]

    def get_fsrs_card(self, item_id: str) -> dict[str, Any] | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    "SELECT * FROM memory_fsrs_cards WHERE item_id = ?",
                    (str(item_id or ""),),
                )
                .fetchone()
            )
        return self._card_from_row(row)

    def due_reviews(
        self, *, deck_id: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self._active_item_card_rows(deck_id=deck_id)
        due = self.fsrs.get_due_reviews(
            [self.store._json_loads(row["card_data"], {}) for row in rows]
        )
        due_by_item = {str(item.get("topic_id") or ""): item for item in due}
        result: list[dict[str, Any]] = []
        for row in rows:
            due_item = due_by_item.get(str(row["item_id"]))
            if not due_item:
                continue
            item = self._item_from_joined_row(row)
            card = self._card_from_joined_row(row)
            result.append(
                {
                    **due_item,
                    "item_id": str(row["item_id"]),
                    "deck_id": str(row["deck_id"]),
                    "deck": {
                        "id": str(row["deck_id"]),
                        "name": str(row["deck_name"] or ""),
                        "deck_type": str(row["deck_type"] or ""),
                    },
                    "item": item,
                    "fsrs_card": card,
                }
            )
        result.sort(
            key=lambda item: (
                str((item.get("deck") or {}).get("name") or ""),
                float(item.get("retrievability") or 0.0),
                str(item.get("due") or ""),
            )
        )
        return result[: max(1, int(limit or 50))]

    def review_item(
        self,
        *,
        item_id: str,
        rating: str | int | StudyFsrsRating | None = None,
        correct: bool | None = None,
        error_type: str = "",
        elapsed_ms: int | None = None,
        session_id: str = "",
    ) -> dict[str, Any]:
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("memory item not found")
        selected = (
            normalize_rating(rating)
            if rating is not None
            else rating_from_word_result(error_type, correct=correct)
        )
        session_key = str(session_id or "").strip()
        if session_key:
            self.store.ensure_session(session_id=session_key, mode="memory")
        with self.store._lock:
            conn = self.store._require_conn()
            card_row = conn.execute(
                "SELECT * FROM memory_fsrs_cards WHERE item_id = ?",
                (str(item_id),),
            ).fetchone()
            if card_row is None:
                card_row = self._ensure_fsrs_card_locked(conn, str(item_id))
                if isinstance(card_row, dict):
                    card_data = self.store._json_dumps(card_row.get("card") or {})
                    card_id = int(card_row["id"])
                else:
                    card_data = str(card_row["card_data"])
                    card_id = int(card_row["id"])
            else:
                card_data = str(card_row["card_data"])
                card_id = int(card_row["id"])
            updated, schedule = rate_answer(
                self.store._json_loads(card_data, {}), selected
            )
            conn.execute(
                """
                UPDATE memory_fsrs_cards
                SET card_data = ?, fsrs_state = ?, last_rating = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    self.store._json_dumps(updated.to_dict()),
                    updated.state,
                    int(selected),
                    card_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_review_log (item_id, card_id, rating, scheduled_days, actual_days, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    str(item_id),
                    card_id,
                    int(selected),
                    int(round(updated.scheduled_days)),
                    int(round(updated.elapsed_days)),
                ),
            )
            review_cursor = conn.execute(
                """
                INSERT INTO review_records (item_id, rating, correct, elapsed_ms, error_type, reviewed_at, session_id)
                VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
                """,
                (
                    str(item_id),
                    int(selected),
                    1
                    if (correct if correct is not None else int(selected) >= 3)
                    else 0,
                    int(elapsed_ms) if elapsed_ms is not None else None,
                    str(error_type or ""),
                    session_key or None,
                ),
            )
            conn.execute(
                "UPDATE memory_items SET fsrs_card_id = ?, updated_at = datetime('now') WHERE id = ?",
                (card_id, str(item_id)),
            )
            conn.commit()
            review_id = int(review_cursor.lastrowid)
        return {
            "item": self.get_item(item_id) or item,
            "rating": int(selected),
            "schedule": schedule,
            "review_record": self.get_review_record(review_id),
        }

    def add_recitation_attempt(
        self,
        *,
        item_id: str,
        user_input_text: str,
        hint_count: int = 0,
        elapsed_ms: int | None = None,
        session_id: str = "",
    ) -> dict[str, Any]:
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("memory item not found")
        expected = str(item.get("answer") or "")[:5000]
        actual = str(user_input_text or "")[:5000]
        diff = diff_recitation(
            expected, actual, hint_count=max(0, int(hint_count or 0))
        )
        rating = rating_from_recitation_score(float(diff.get("score") or 0.0))
        review = self.review_item(
            item_id=item_id,
            rating=rating,
            correct=float(diff.get("score") or 0.0) >= 0.80,
            error_type="recitation",
            elapsed_ms=elapsed_ms,
            session_id=session_id,
        )
        review_record = review.get("review_record") or {}
        with self.store._lock:
            conn = self.store._require_conn()
            cursor = conn.execute(
                """
                INSERT INTO recitation_attempts (
                    passage_item_id, review_record_id, user_input_text,
                    missing_count, extra_count, wrong_order_count, hint_count, score, reviewed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    str(item_id),
                    int(review_record.get("id") or 0) or None,
                    actual,
                    int(diff.get("missing_count") or 0),
                    int(diff.get("extra_count") or 0),
                    int(diff.get("wrong_order_count") or 0),
                    int(diff.get("hint_count") or 0),
                    float(diff.get("score") or 0.0),
                ),
            )
            conn.commit()
            attempt_id = int(cursor.lastrowid)
        return {
            "attempt": self.get_recitation_attempt(attempt_id),
            "diff": diff,
            "review": review,
        }

    def get_review_record(self, review_id: int) -> dict[str, Any] | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    "SELECT * FROM review_records WHERE id = ?",
                    (int(review_id or 0),),
                )
                .fetchone()
            )
        return self._review_from_row(row)

    def get_recitation_attempt(self, attempt_id: int) -> dict[str, Any] | None:
        with self.store._lock:
            row = (
                self.store._require_conn()
                .execute(
                    "SELECT * FROM recitation_attempts WHERE id = ?",
                    (int(attempt_id or 0),),
                )
                .fetchone()
            )
        return self._recitation_from_row(row)

    def create_word_draft(self, *, word: str, meaning: str) -> dict[str, Any]:
        word_text = str(word or "").strip()
        meaning_text = str(meaning or "").strip()
        if not word_text:
            raise ValueError("word is required")
        payload = {
            "draft_type": "word_example",
            "item_type": "word",
            "word": word_text,
            "meaning": meaning_text,
            "example_sentence": f"{word_text} means {meaning_text}."
            if meaning_text
            else f"Remember the word {word_text}.",
            "confusion_note": f"Check whether {word_text} is confused with a similar spelling or meaning.",
            "status": "candidate",
        }
        return self._upsert_memory_candidate("word_example", payload)

    def create_cloze_draft(self, *, sentence: str) -> dict[str, Any]:
        cloze = build_cloze_prompt(sentence)
        payload = {
            "draft_type": "sentence_cloze",
            "item_type": "cloze",
            "sentence": str(sentence or ""),
            **cloze,
            "status": "candidate",
        }
        return self._upsert_memory_candidate("sentence_cloze", payload)

    def create_recitation_error_draft(
        self, *, expected: str, actual: str
    ) -> dict[str, Any]:
        diff = diff_recitation(expected, actual)
        first_error = next(
            (item for item in diff["operations"] if item.get("type") != "equal"), {}
        )
        explanation = "Review the changed segment and recite it once more."
        if first_error:
            explanation = f"Focus on {first_error.get('type')} text: {first_error.get('expected') or first_error.get('actual')}"
        payload = {
            "draft_type": "recitation_error",
            "item_type": "paragraph",
            "diff": diff,
            "explanation": explanation,
            "status": "candidate",
        }
        return self._upsert_memory_candidate("recitation_error", payload)

    def status_summary(self, *, limit: int = 8) -> dict[str, Any]:
        decks = self.list_decks(limit=limit)
        due = self.due_reviews(limit=limit)
        with self.store._lock:
            counts = self._memory_counts(self.store._require_conn())
        return {
            **counts,
            "decks": decks,
            "due_count": len(self.due_reviews(limit=5000)),
            "due_reviews": due,
        }

    def export_deck_json(self, deck_id: str) -> dict[str, Any]:
        deck = self.get_deck(deck_id)
        if deck is None:
            raise ValueError("deck not found")
        return {
            "deck": deck,
            "items": self.list_items(
                deck_id=deck_id, limit=5000, include_archived=True
            ),
            "due_reviews": self.due_reviews(deck_id=deck_id, limit=5000),
        }

    def compat_card_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        card = (
            item.get("fsrs_card") or self.get_fsrs_card(str(item.get("id") or "")) or {}
        )
        raw_card = card.get("card") if isinstance(card, dict) else {}
        due_item = (
            self.fsrs.get_due_reviews([raw_card])[0]
            if raw_card and self.fsrs.get_due_reviews([raw_card])
            else None
        )
        return {
            "id": str(item.get("id") or ""),
            "topic_id": str(item.get("id") or ""),
            "item_id": str(item.get("id") or ""),
            "deck_id": str(item.get("deck_id") or ""),
            "front": str(item.get("prompt") or ""),
            "back": str(item.get("answer") or ""),
            "tags": list((item.get("metadata") or {}).get("tags") or []),
            "source": str((item.get("metadata") or {}).get("source") or ""),
            "card_type": "memory",
            "due": str(raw_card.get("due") or ""),
            "is_due": due_item is not None,
            "retrievability": round(
                float(due_item.get("retrievability"))
                if due_item
                else retrievability(raw_card),
                4,
            )
            if raw_card
            else 0.0,
            "state": str(raw_card.get("state") or ""),
            "scheduled_days": float(raw_card.get("scheduled_days") or 0.0),
            "reps": int(raw_card.get("reps") or 0),
            "lapses": int(raw_card.get("lapses") or 0),
            "last_review": str(raw_card.get("last_review") or ""),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
            "last_rating": int(card.get("last_rating") or 0)
            if isinstance(card, dict)
            else 0,
            "item": item,
            "fsrs_card": card,
        }

    def _import_word_rows(
        self, *, deck_id: str, rows: list[dict[str, Any]], line_offset: int
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
                skipped.append(
                    {"line": line, "reason": "word and meaning are required"}
                )
                continue
            result = self.add_word(
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

    def _ensure_fsrs_card_locked(self, conn: Any, item_id: str) -> dict[str, Any]:
        existing = conn.execute(
            "SELECT * FROM memory_fsrs_cards WHERE item_id = ?",
            (str(item_id),),
        ).fetchone()
        if existing is not None:
            return self._card_from_row(existing) or {}
        card = create_card(str(item_id)).to_dict()
        cursor = conn.execute(
            """
            INSERT INTO memory_fsrs_cards (item_id, card_data, fsrs_state, last_rating, updated_at)
            VALUES (?, ?, 'new', NULL, datetime('now'))
            """,
            (str(item_id), self.store._json_dumps(card)),
        )
        card_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE memory_items SET fsrs_card_id = ?, updated_at = datetime('now') WHERE id = ?",
            (card_id, str(item_id)),
        )
        return {
            "id": card_id,
            "item_id": str(item_id),
            "card": card,
            "fsrs_state": "new",
            "last_rating": 0,
            "updated_at": "",
        }

    def _active_item_card_rows(self, *, deck_id: str = "") -> list[Any]:
        params: list[Any] = []
        deck_clause = ""
        if deck_id:
            deck_clause = "AND mi.deck_id = ?"
            params.append(str(deck_id))
        with self.store._lock:
            return (
                self.store._require_conn()
                .execute(
                    f"""
                SELECT
                    mi.id AS item_id,
                    mi.deck_id AS deck_id,
                    mi.item_type AS item_type,
                    mi.prompt AS prompt,
                    mi.answer AS answer,
                    mi.metadata_json AS metadata_json,
                    mi.fsrs_card_id AS fsrs_card_id,
                    mi.status AS status,
                    mi.created_at AS item_created_at,
                    mi.updated_at AS item_updated_at,
                    d.name AS deck_name,
                    d.deck_type AS deck_type,
                    mfc.id AS card_id,
                    mfc.card_data AS card_data,
                    mfc.fsrs_state AS fsrs_state,
                    mfc.last_rating AS last_rating,
                    mfc.updated_at AS card_updated_at
                FROM memory_items mi
                JOIN decks d ON d.id = mi.deck_id
                JOIN memory_fsrs_cards mfc ON mfc.item_id = mi.id
                WHERE mi.status = 'active' {deck_clause}
                """,
                    params,
                )
                .fetchall()
            )

    def _upsert_memory_candidate(
        self, kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        digest = hashlib.sha1(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return self.store.upsert_candidate_item(
            item_type="memory_draft",
            payload=payload,
            source="memory_llm_fallback",
            dedupe_key=f"{kind}:{digest}",
            status="candidate",
        )

    def _memory_counts(self, conn: Any, *, deck_id: str = "") -> dict[str, int]:
        params: list[Any] = []
        deck_predicate = ""
        if deck_id:
            deck_predicate = "WHERE deck_id = ?"
            params.append(deck_id)
        deck_count = conn.execute("SELECT COUNT(*) AS count FROM decks").fetchone()[
            "count"
        ]
        item_count = conn.execute(
            f"SELECT COUNT(*) AS count FROM memory_items {deck_predicate}",
            params,
        ).fetchone()["count"]
        card_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM memory_fsrs_cards mfc
            JOIN memory_items mi ON mi.id = mfc.item_id
            """
            + ("WHERE mi.deck_id = ?" if deck_id else ""),
            params,
        ).fetchone()["count"]
        review_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM review_records rr
            JOIN memory_items mi ON mi.id = rr.item_id
            """
            + ("WHERE mi.deck_id = ?" if deck_id else ""),
            params,
        ).fetchone()["count"]
        recitation_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM recitation_attempts ra
            JOIN memory_items mi ON mi.id = ra.passage_item_id
            """
            + ("WHERE mi.deck_id = ?" if deck_id else ""),
            params,
        ).fetchone()["count"]
        return {
            "deck_count": safe_int(deck_count, 0),
            "item_count": safe_int(item_count, 0),
            "card_count": safe_int(card_count, 0),
            "review_count": safe_int(review_count, 0),
            "recitation_count": safe_int(recitation_count, 0),
        }

    def _deck_from_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["name"] or ""),
            "deck_type": str(row["deck_type"] or ""),
            "subject": str(row["subject"] or ""),
            "language": str(row["language"] or ""),
            "source": str(row["source"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "item_count": safe_int(row["item_count"], 0)
            if "item_count" in row.keys()
            else 0,
        }

    def _item_from_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "deck_id": str(row["deck_id"] or ""),
            "deck_name": str(row["deck_name"] or "")
            if "deck_name" in row.keys()
            else "",
            "deck_type": str(row["deck_type"] or "")
            if "deck_type" in row.keys()
            else "",
            "item_type": str(row["item_type"] or ""),
            "prompt": str(row["prompt"] or ""),
            "answer": str(row["answer"] or ""),
            "metadata": self.store._json_loads(row["metadata_json"], {}),
            "fsrs_card_id": safe_int(row["fsrs_card_id"], 0),
            "status": str(row["status"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def _item_from_joined_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row["item_id"]),
            "deck_id": str(row["deck_id"] or ""),
            "deck_name": str(row["deck_name"] or ""),
            "deck_type": str(row["deck_type"] or ""),
            "item_type": str(row["item_type"] or ""),
            "prompt": str(row["prompt"] or ""),
            "answer": str(row["answer"] or ""),
            "metadata": self.store._json_loads(row["metadata_json"], {}),
            "fsrs_card_id": safe_int(row["fsrs_card_id"], 0),
            "status": str(row["status"] or ""),
            "created_at": str(row["item_created_at"] or ""),
            "updated_at": str(row["item_updated_at"] or ""),
        }

    def _card_from_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "item_id": str(row["item_id"] or ""),
            "card": self.store._json_loads(row["card_data"], {}),
            "fsrs_state": str(row["fsrs_state"] or ""),
            "last_rating": safe_int(row["last_rating"], 0),
            "updated_at": str(row["updated_at"] or ""),
        }

    def _card_from_joined_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": int(row["card_id"]),
            "item_id": str(row["item_id"] or ""),
            "card": self.store._json_loads(row["card_data"], {}),
            "fsrs_state": str(row["fsrs_state"] or ""),
            "last_rating": safe_int(row["last_rating"], 0),
            "updated_at": str(row["card_updated_at"] or ""),
        }

    def _review_from_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "item_id": str(row["item_id"] or ""),
            "rating": int(row["rating"] or 0),
            "correct": bool(row["correct"]),
            "elapsed_ms": safe_int(row["elapsed_ms"], 0),
            "error_type": str(row["error_type"] or ""),
            "reviewed_at": str(row["reviewed_at"] or ""),
            "session_id": str(row["session_id"] or ""),
        }

    def _recitation_from_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "passage_item_id": str(row["passage_item_id"] or ""),
            "review_record_id": safe_int(row["review_record_id"], 0),
            "user_input_text": str(row["user_input_text"] or ""),
            "missing_count": safe_int(row["missing_count"], 0),
            "extra_count": safe_int(row["extra_count"], 0),
            "wrong_order_count": safe_int(row["wrong_order_count"], 0),
            "hint_count": safe_int(row["hint_count"], 0),
            "score": float(row["score"] or 0.0),
            "reviewed_at": str(row["reviewed_at"] or ""),
        }


__all__ = [
    "MemoryDeckStore",
    "build_cloze_prompt",
    "diff_recitation",
    "ensure_memory_schema",
    "normalize_rating",
    "rating_from_recitation_score",
    "rating_from_word_result",
    "split_passage_text",
]
