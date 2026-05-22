from __future__ import annotations
import csv
import io
import json
import uuid
from typing import Any

from .fsrs_bridge import (
    FSRSBridge,
    StudyFsrsRating,
    create_card,
    rate_answer,
    retrievability,
)
from .memory_candidates import upsert_memory_candidate
from .memory_imports import import_word_rows, normalize_csv_fieldnames
from .memory_queries import active_item_card_rows
from .memory_ratings import (
    normalize_rating,
    rating_from_recitation_score,
    rating_from_word_result,
)
from .memory_rows import (
    card_from_joined_row,
    card_from_row,
    deck_from_row,
    item_from_joined_row,
    item_from_row,
    memory_counts,
    recitation_from_row,
    review_from_row,
    safe_int,
)
from .memory_schema import ensure_memory_schema, normalize_deck_type, normalize_item_type
from .memory_text import (
    build_cloze_prompt,
    diff_recitation,
    normalize_tags,
    split_passage_text,
)
from .models import json_copy

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
        return deck_from_row(row)

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
            for deck in (deck_from_row(row) for row in rows)
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
        return deck_from_row(row)

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
        next_name = str(name if name is not None else current.get("name") or "").strip()
        if not next_name:
            raise ValueError("deck name is required")
        with self.store._lock:
            conn = self.store._require_conn()
            conn.execute(
                """
                UPDATE decks
                SET name = ?, subject = ?, language = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    next_name,
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
            before = memory_counts(conn, deck_id=str(deck_id or ""))
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
        stream = io.StringIO(str(content or "").lstrip("\ufeff"))
        reader = csv.DictReader(stream)
        reader.fieldnames = normalize_csv_fieldnames(reader.fieldnames)
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
        return import_word_rows(
            self.add_word, deck_id=deck_id, rows=list(reader), line_offset=2
        )

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
        return import_word_rows(
            self.add_word, deck_id=deck_id, rows=rows, line_offset=1
        )

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
        item = item_from_row(row, self.store._json_loads)
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
            for item in (item_from_row(row, self.store._json_loads) for row in rows)
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
        return card_from_row(row, self.store._json_loads)

    def due_reviews(
        self, *, deck_id: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.store._lock:
            rows = active_item_card_rows(
                self.store._require_conn(), deck_id=deck_id
            )
        due = self.fsrs.get_due_reviews(
            [self.store._json_loads(row["card_data"], {}) for row in rows]
        )
        due_by_item = {str(item.get("topic_id") or ""): item for item in due}
        result: list[dict[str, Any]] = []
        for row in rows:
            due_item = due_by_item.get(str(row["item_id"]))
            if not due_item:
                continue
            item = item_from_joined_row(row, self.store._json_loads)
            card = card_from_joined_row(row, self.store._json_loads)
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
                SET card_data = ?, fsrs_state = ?, last_rating = ?, next_due = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    self.store._json_dumps(updated.to_dict()),
                    updated.state,
                    int(selected),
                    str(updated.due or ""),
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
        if str(item.get("item_type") or "") not in {"sentence", "paragraph"}:
            raise ValueError("recitation is only supported for passage items")
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
        return review_from_row(row)

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
        return recitation_from_row(row)

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
        return upsert_memory_candidate(self.store, "word_example", payload)

    def create_cloze_draft(self, *, sentence: str) -> dict[str, Any]:
        cloze = build_cloze_prompt(sentence)
        payload = {
            "draft_type": "sentence_cloze",
            "item_type": "cloze",
            "sentence": str(sentence or ""),
            **cloze,
            "status": "candidate",
        }
        return upsert_memory_candidate(self.store, "sentence_cloze", payload)

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
        return upsert_memory_candidate(self.store, "recitation_error", payload)

    def status_summary(self, *, limit: int = 8) -> dict[str, Any]:
        decks = self.list_decks(limit=limit)
        due = self.due_reviews(limit=limit)
        with self.store._lock:
            counts = memory_counts(self.store._require_conn())
        return {
            **counts,
            "decks": decks,
            "due_count": self.count_due_reviews(),
            "due_reviews": due,
        }

    def count_due_reviews(self, *, deck_id: str = "") -> int:
        with self.store._lock:
            rows = active_item_card_rows(
                self.store._require_conn(), deck_id=deck_id
            )
        due = self.fsrs.get_due_reviews(
            [self.store._json_loads(row["card_data"], {}) for row in rows]
        )
        return len(due)

    def export_deck_json(self, deck_id: str) -> dict[str, Any]:
        deck = self.get_deck(deck_id)
        if deck is None:
            raise ValueError("deck not found")
        with self.store._lock:
            counts = memory_counts(self.store._require_conn(), deck_id=deck_id)
        item_limit = max(1, int(counts.get("item_count") or 0))
        due_limit = max(1, self.count_due_reviews(deck_id=deck_id))
        return {
            "deck": deck,
            "items": self.list_items(
                deck_id=deck_id, limit=item_limit, include_archived=True
            ),
            "due_reviews": self.due_reviews(deck_id=deck_id, limit=due_limit),
        }

    def compat_card_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        card = (
            item.get("fsrs_card") or self.get_fsrs_card(str(item.get("id") or "")) or {}
        )
        raw_card = card.get("card") if isinstance(card, dict) else {}
        due_reviews = self.fsrs.get_due_reviews([raw_card]) if raw_card else []
        due_item = due_reviews[0] if due_reviews else None
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

    def _ensure_fsrs_card_locked(self, conn: Any, item_id: str) -> dict[str, Any]:
        existing = conn.execute(
            "SELECT * FROM memory_fsrs_cards WHERE item_id = ?",
            (str(item_id),),
        ).fetchone()
        if existing is not None:
            return card_from_row(existing, self.store._json_loads) or {}
        card = create_card(str(item_id)).to_dict()
        cursor = conn.execute(
            """
            INSERT INTO memory_fsrs_cards (item_id, card_data, fsrs_state, last_rating, next_due, updated_at)
            VALUES (?, ?, 'new', NULL, ?, datetime('now'))
            """,
            (str(item_id), self.store._json_dumps(card), str(card.get("due") or "")),
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
            "next_due": str(card.get("due") or ""),
            "updated_at": "",
        }
