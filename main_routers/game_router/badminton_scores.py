# -*- coding: utf-8 -*-
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

"""Badminton leaderboard: sqlite score store, score session
reservation/dedup and the leaderboard endpoints.

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import _coerce_payload_bool, _normalize_short_text, logger, router

import math
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict
from fastapi import Request
from ..shared_state import get_config_manager


_BADMINTON_SCORE_SESSION_TTL_SECONDS = 10 * 60


_BADMINTON_SCORING_MODES = {"duel"}


_BADMINTON_GAME_TYPES = {"badminton"}


_BADMINTON_SHOT_TYPE_ALIASES = {
    "line_in": "line_in",
    "net_touch": "net_touch",
    "zone_in": "zone_in",
    "out": "out",
    "net": "net",
}


_badminton_recent_score_sessions: Dict[tuple[str, str], dict] = {}


_BADMINTON_SCORES_DB_PATH: Path | None = None


# Existing installs created this DB next to the former main_routers/game_router.py,
# i.e. in main_routers/ — keep the legacy lookup anchored there, not in this package.
_BADMINTON_LEGACY_SCORES_DB_PATH = Path(__file__).resolve().parent.parent / "badminton_scores.db"


def _is_badminton_game_type(game_type: Any) -> bool:
    return str(game_type or "").strip().lower() in _BADMINTON_GAME_TYPES


def _normalize_badminton_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    # shooter/timed/time_attack/HORSE modes were removed; unknown modes fall back to spectator.
    return mode if mode in {"spectator", "duel"} else "spectator"


def _is_badminton_scoring_mode(value: Any) -> bool:
    return _normalize_badminton_mode(value) in _BADMINTON_SCORING_MODES


def _normalize_badminton_non_negative_int(value: Any, *, default: int = 0, max_value: int = 999999) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0, min(int(number), int(max_value)))


def _normalize_badminton_distance(value: Any, *, default: float = 0.0, max_value: float = 10000.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0.0, min(number, float(max_value)))


_BADMINTON_PX_PER_METER = 12.0 * 3.28084


def _format_badminton_distance_meters(distance_px: float) -> str:
    return f"{distance_px / _BADMINTON_PX_PER_METER:.1f}"


def _score_db_game_slug(game_type: Any = "badminton") -> str:
    # This backend layer owns only badminton scores; other game types must not
    # share this leaderboard implicitly.
    return "badminton"


def _get_badminton_scores_db_path(game_type: Any = "badminton") -> Path:
    override = _BADMINTON_SCORES_DB_PATH
    if override is not None:
        return Path(override)
    try:
        base_dir = Path(get_config_manager().app_docs_dir)
    except Exception:
        base_dir = Path.cwd()
    return base_dir / "state" / "game_scores" / f"{_score_db_game_slug(game_type)}_scores.db"


def _prepare_badminton_scores_db_path(game_type: Any = "badminton") -> Path:
    slug = _score_db_game_slug(game_type)
    db_path = _get_badminton_scores_db_path(slug)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migration_attempted = bool(getattr(_prepare_badminton_scores_db_path, "_migration_attempted", False))
    if (
        slug == "badminton"
        and
        _BADMINTON_SCORES_DB_PATH is None
        and not migration_attempted
        and not db_path.exists()
        and _BADMINTON_LEGACY_SCORES_DB_PATH.exists()
    ):
        try:
            shutil.copy2(_BADMINTON_LEGACY_SCORES_DB_PATH, db_path)
            logger.info(
                "🏸 已迁移羽毛球排行榜 DB: %s -> %s",
                _BADMINTON_LEGACY_SCORES_DB_PATH,
                db_path,
            )
        except Exception as exc:
            logger.warning(
                "🏸 羽毛球排行榜 DB 迁移失败，将使用新 runtime DB: %s",
                exc,
            )
    if slug == "badminton":
        setattr(_prepare_badminton_scores_db_path, "_migration_attempted", True)
    return db_path


def _open_badminton_scores_db(game_type: Any = "badminton") -> sqlite3.Connection:
    conn = sqlite3.connect(str(_prepare_badminton_scores_db_path(game_type)), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_badminton_scores_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS badminton_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            lanlan_name TEXT NOT NULL DEFAULT '',
            score INTEGER NOT NULL,
            streak INTEGER NOT NULL,
            max_distance_px REAL NOT NULL,
            line_in_count INTEGER NOT NULL DEFAULT 0,
            net_touch_count INTEGER NOT NULL DEFAULT 0,
            zone_in_count INTEGER NOT NULL DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'spectator',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    badminton_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(badminton_scores)").fetchall()
    }
    if "mode" not in badminton_columns:
        conn.execute("ALTER TABLE badminton_scores ADD COLUMN mode TEXT NOT NULL DEFAULT 'spectator'")
    badminton_score_count_columns = {
        "line_in_count": "INTEGER NOT NULL DEFAULT 0",
        "net_touch_count": "INTEGER NOT NULL DEFAULT 0",
        "zone_in_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_definition in badminton_score_count_columns.items():
        if column_name not in badminton_columns:
            conn.execute(
                f"ALTER TABLE badminton_scores ADD COLUMN {column_name} {column_definition}"
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bd_scores_score ON badminton_scores(score DESC)"
    )


def _badminton_score_rows(conn: sqlite3.Connection, limit: int | None = None, offset: int = 0) -> list[sqlite3.Row]:
    if limit is not None:
        clean_limit = _normalize_badminton_non_negative_int(limit, default=10, max_value=100)
        clean_offset = _normalize_badminton_non_negative_int(offset, default=0, max_value=100000)
        return conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (clean_limit, clean_offset),
        ).fetchall()
    return conn.execute(
        """
        SELECT
            id, session_id, lanlan_name, score, streak, max_distance_px,
            line_in_count, net_touch_count, zone_in_count, mode, created_at
        FROM badminton_scores
        ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
        """,
    ).fetchall()


def _badminton_rank_for_row(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    rank_row = conn.execute(
        """
        SELECT COUNT(*) + 1 AS rank
        FROM badminton_scores
        WHERE
            score > ?
            OR (score = ? AND streak > ?)
            OR (score = ? AND streak = ? AND max_distance_px > ?)
            OR (score = ? AND streak = ? AND max_distance_px = ? AND created_at < ?)
            OR (score = ? AND streak = ? AND max_distance_px = ? AND created_at = ? AND id < ?)
        """,
        (
            row["score"],
            row["score"], row["streak"],
            row["score"], row["streak"], row["max_distance_px"],
            row["score"], row["streak"], row["max_distance_px"], row["created_at"],
            row["score"], row["streak"], row["max_distance_px"], row["created_at"], row["id"],
        ),
    ).fetchone()
    return _normalize_badminton_non_negative_int(rank_row["rank"] if rank_row else 1, default=1)


def _badminton_row_to_public_dict(row: sqlite3.Row, rank: int) -> dict:
    created_at = str(row["created_at"] or "")
    distance_px = _normalize_badminton_distance(row["max_distance_px"], default=0.0)
    return {
        "rank": rank,
        "name": _normalize_short_text(row["lanlan_name"], max_chars=80),
        "score": _normalize_badminton_non_negative_int(row["score"]),
        "streak": _normalize_badminton_non_negative_int(row["streak"]),
        "max_distance_m": _format_badminton_distance_meters(distance_px),
        "mode": _normalize_badminton_mode(row["mode"]),
        "date": created_at[:10],
    }


def _badminton_player_identity(lanlan_name: str, session_id: str) -> tuple[str, str]:
    clean_name = _normalize_short_text(lanlan_name, max_chars=80)
    clean_session = _normalize_short_text(session_id, max_chars=120)
    return clean_name, clean_session


def _badminton_score_totals_from_data(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None

    score_value = None
    for key in ("score", "totalScore", "total_score", "player"):
        if key in data:
            score_value = data.get(key)
            break
    if score_value is None:
        return None

    streak_value = 0
    for key in ("streak", "best_streak", "bestStreak", "final_streak", "finalStreak"):
        if key in data:
            streak_value = data.get(key)
            break

    distance_value = 0.0
    for key in ("max_distance_px", "maxDistancePx", "max_distance", "maxDistance", "final_distance", "finalDistance"):
        if key in data:
            distance_value = data.get(key)
            break

    return {
        "score": _normalize_badminton_non_negative_int(score_value),
        "streak": _normalize_badminton_non_negative_int(streak_value),
        "max_distance_px": _normalize_badminton_distance(distance_value),
    }


def _badminton_score_totals_match_submission(data: dict, expected: Any) -> bool:
    if not isinstance(expected, dict):
        return True
    actual = _badminton_score_totals_from_data(data)
    if not actual:
        return False
    return (
        actual["score"] == _normalize_badminton_non_negative_int(expected.get("score"))
        and actual["streak"] == _normalize_badminton_non_negative_int(expected.get("streak"))
        and math.isclose(
            actual["max_distance_px"],
            _normalize_badminton_distance(expected.get("max_distance_px")),
            rel_tol=0.0,
            abs_tol=0.001,
        )
    )


def _prune_badminton_score_sessions(now: float | None = None) -> None:
    current = time.time() if now is None else now
    for key, meta in list(_badminton_recent_score_sessions.items()):
        if float(meta.get("expires_at") or 0.0) <= current:
            _badminton_recent_score_sessions.pop(key, None)


def _remember_badminton_score_session(
    lanlan_name: str,
    session_id: str,
    mode: Any,
    score_totals: dict | None = None,
) -> None:
    clean_mode = _normalize_badminton_mode(mode)
    if not _is_badminton_scoring_mode(clean_mode):
        return
    clean_name, clean_session = _badminton_player_identity(lanlan_name, session_id)
    if not clean_name or not clean_session:
        return
    _prune_badminton_score_sessions()
    meta = {
        "mode": clean_mode,
        "expires_at": time.time() + _BADMINTON_SCORE_SESSION_TTL_SECONDS,
    }
    clean_totals = _badminton_score_totals_from_data(score_totals)
    if clean_totals:
        meta["score_totals"] = clean_totals
    _badminton_recent_score_sessions[(clean_name, clean_session)] = meta


def _badminton_end_payload_completed_round(data: dict) -> bool:
    if _coerce_payload_bool(data.get("round_completed")) is True:
        return True
    if _coerce_payload_bool(data.get("roundCompleted")) is True:
        return True
    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        if _normalize_short_text(current_state.get("state"), max_chars=40) == "game_over":
            return True
        if _coerce_payload_bool(current_state.get("round_completed")) is True:
            return True
        if _coerce_payload_bool(current_state.get("roundCompleted")) is True:
            return True
    return False


def _badminton_score_session_key_from_payload(data: dict) -> tuple[str, str, str] | None:
    session_id = _normalize_short_text(data.get("session_id"), max_chars=120)
    lanlan_name = _normalize_short_text(data.get("lanlan_name"), max_chars=80)
    if not session_id or not lanlan_name:
        return None
    mode = _normalize_badminton_mode(data.get("mode"))
    if not _is_badminton_scoring_mode(mode):
        return None
    return lanlan_name, session_id, mode


def _badminton_score_submission_allowed(data: dict, *, reserve: bool = False) -> bool:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return False
    lanlan_name, session_id, mode = session_key
    _prune_badminton_score_sessions()
    key = (lanlan_name, session_id)
    meta = _badminton_recent_score_sessions.get(key)
    if not (meta and meta.get("mode") == mode):
        return False
    if not _badminton_score_totals_match_submission(data, meta.get("score_totals")):
        return False
    if meta.get("reserved") is True:
        return False
    if reserve:
        meta["reserved"] = True
    return True


def _badminton_score_data_for_insert(data: dict) -> dict:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return data
    lanlan_name, session_id, mode = session_key
    meta = _badminton_recent_score_sessions.get((lanlan_name, session_id))
    totals = meta.get("score_totals") if isinstance(meta, dict) else None
    if not isinstance(totals, dict):
        return data
    insert_data = dict(data)
    insert_data.update({
        "score": _normalize_badminton_non_negative_int(totals.get("score")),
        "streak": _normalize_badminton_non_negative_int(totals.get("streak")),
        "max_distance_px": _normalize_badminton_distance(totals.get("max_distance_px")),
        "mode": mode,
    })
    return insert_data


def _release_badminton_score_session_reservation(data: dict) -> None:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return
    lanlan_name, session_id, _mode = session_key
    meta = _badminton_recent_score_sessions.get((lanlan_name, session_id))
    if meta:
        meta.pop("reserved", None)


def _consume_badminton_score_session(data: dict) -> None:
    session_key = _badminton_score_session_key_from_payload(data)
    if not session_key:
        return
    lanlan_name, session_id, _mode = session_key
    _badminton_recent_score_sessions.pop((lanlan_name, session_id), None)


def _badminton_leaderboard_total_players(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT CASE
            WHEN TRIM(lanlan_name) <> '' THEN lanlan_name
            ELSE session_id
        END) AS total_players
        FROM badminton_scores
        """
    ).fetchone()
    if not row:
        return 0
    return _normalize_badminton_non_negative_int(row["total_players"], default=0)


def _badminton_leaderboard_total_scores(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS total_scores FROM badminton_scores").fetchone()
    if not row:
        return 0
    return _normalize_badminton_non_negative_int(row["total_scores"], default=0)


def _badminton_personal_best(conn: sqlite3.Connection, lanlan_name: str, session_id: str) -> dict | None:
    clean_name, clean_session = _badminton_player_identity(lanlan_name, session_id)
    if not clean_name and not clean_session:
        return None
    if clean_name:
        row = conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            WHERE lanlan_name = ?
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (clean_name,),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT
                id, session_id, lanlan_name, score, streak, max_distance_px,
                line_in_count, net_touch_count, zone_in_count, mode, created_at
            FROM badminton_scores
            WHERE session_id = ?
            ORDER BY score DESC, streak DESC, max_distance_px DESC, created_at ASC, id ASC
            LIMIT 1
            """,
            (clean_session,),
        ).fetchone()
    if not row:
        return None
    return {
        "rank": _badminton_rank_for_row(conn, row),
        "score": _normalize_badminton_non_negative_int(row["score"]),
    }


def _badminton_insert_score(data: dict, *, game_type: Any = "badminton") -> tuple[int, int, bool]:
    session_id = _normalize_short_text(data.get("session_id"), max_chars=120)
    lanlan_name = _normalize_short_text(data.get("lanlan_name"), max_chars=80)
    if not session_id:
        session_id = f"badminton-{uuid.uuid4().hex}"
    score = _normalize_badminton_non_negative_int(data.get("score"))
    streak = _normalize_badminton_non_negative_int(data.get("streak"))
    max_distance_px = _normalize_badminton_distance(data.get("max_distance_px"))
    line_in_count = _normalize_badminton_non_negative_int(data.get("line_in_count"))
    net_touch_count = _normalize_badminton_non_negative_int(data.get("net_touch_count"))
    zone_in_count = _normalize_badminton_non_negative_int(data.get("zone_in_count"))
    mode = _normalize_badminton_mode(data.get("mode"))
    with _open_badminton_scores_db(game_type) as conn:
        _ensure_badminton_scores_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                INSERT INTO badminton_scores (
                    session_id, lanlan_name, score, streak, max_distance_px,
                    line_in_count, net_touch_count, zone_in_count, mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    lanlan_name,
                    score,
                    streak,
                    max_distance_px,
                    line_in_count,
                    net_touch_count,
                    zone_in_count,
                    mode,
                ),
            )
            inserted_id = cursor.lastrowid
            inserted_row = conn.execute(
                """
                SELECT
                    id, session_id, lanlan_name, score, streak, max_distance_px,
                    line_in_count, net_touch_count, zone_in_count, mode, created_at
                FROM badminton_scores
                WHERE id = ?
                """,
                (inserted_id,),
            ).fetchone()
            if not inserted_row:
                raise RuntimeError("badminton_score_insert_missing")
            rank = _badminton_rank_for_row(conn, inserted_row)
            inserted_score = _normalize_badminton_non_negative_int(inserted_row["score"])
            total_players = _badminton_leaderboard_total_players(conn)
            personal_best = _badminton_personal_best(conn, lanlan_name, session_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return rank, total_players, personal_best is not None and personal_best.get("rank") == rank and personal_best.get("score") == inserted_score


@router.get("/{game_type}/leaderboard")
async def game_badminton_leaderboard(
    game_type: str,
    session_id: str = "",
    lanlan_name: str = "",
    limit: int = 10,
    offset: int = 0,
):
    clean_limit = _normalize_badminton_non_negative_int(limit, default=10, max_value=100)
    clean_offset = _normalize_badminton_non_negative_int(offset, default=0, max_value=100000)
    if not _is_badminton_game_type(game_type):
        return {
            "ok": True,
            "top": [],
            "total_players": 0,
            "total_scores": 0,
            "limit": clean_limit,
            "offset": clean_offset,
            "has_more": False,
            "your_best": None,
        }
    with _open_badminton_scores_db(game_type) as conn:
        _ensure_badminton_scores_schema(conn)
        rows = _badminton_score_rows(conn, limit=clean_limit, offset=clean_offset)
        total_players = _badminton_leaderboard_total_players(conn)
        total_scores = _badminton_leaderboard_total_scores(conn)
        top = [_badminton_row_to_public_dict(row, _badminton_rank_for_row(conn, row)) for row in rows]
        your_best = _badminton_personal_best(conn, lanlan_name, session_id)
    return {
        "ok": True,
        "top": top,
        "total_players": total_players,
        "total_scores": total_scores,
        "limit": clean_limit,
        "offset": clean_offset,
        "has_more": clean_offset + len(top) < total_scores,
        "your_best": your_best,
    }


@router.post("/{game_type}/leaderboard")
async def game_badminton_leaderboard_submit(game_type: str, request: Request):
    if not _is_badminton_game_type(game_type):
        return {"ok": False, "reason": f"暂不支持 {game_type} 的排行榜"}
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}
    if not _badminton_score_submission_allowed(data, reserve=True):
        return {"ok": False, "reason": "invalid_session"}
    insert_data = _badminton_score_data_for_insert(data)
    try:
        rank, total_players, is_personal_best = _badminton_insert_score(insert_data, game_type=game_type)
    except Exception:
        _release_badminton_score_session_reservation(data)
        raise
    _consume_badminton_score_session(data)
    return {
        "ok": True,
        "rank": rank,
        "total_players": total_players,
        "is_personal_best": is_personal_best,
    }
