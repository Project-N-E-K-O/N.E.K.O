from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .reader import normalize_text


SENREN_BANKA_GAME_ID = "senren_banka"
_DEFAULT_LIBRARY_DIR = Path(__file__).with_name("data") / "dialogue_libraries"
_SENREN_BANKA_LIBRARY_FILE = "senren_banka.json"
_MIN_FUZZY_KEY_LENGTH = 8
_FUZZY_MATCH_THRESHOLD = 0.9
_SENREN_BANKA_PROCESS_NAMES = frozenset({"senrenbanka.exe", "senrenbanka"})


def _title_match_key(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().lower().replace(" ", "")


_SENREN_BANKA_TITLE_SUBSTRINGS = (
    "千恋万花",
    "千恋＊万花",
    "千恋*万花",
    "千戀萬花",
    "千戀＊萬花",
    "senren banka",
    "senren＊banka",
    "senren*banka",
)
_SENREN_BANKA_TITLE_KEYS = tuple(
    key for key in (_title_match_key(token) for token in _SENREN_BANKA_TITLE_SUBSTRINGS) if key
)
_BUILTIN_LIBRARY_SPECS = (
    {
        "game_id": SENREN_BANKA_GAME_ID,
        "title": "千恋＊万花",
        "file_name": _SENREN_BANKA_LIBRARY_FILE,
        "source": "developer_builtin",
    },
)


@dataclass(frozen=True, slots=True)
class DialogueLibraryLine:
    game_id: str
    line_id: str
    text: str
    speaker: str = ""
    aliases: tuple[str, ...] = ()

    def candidate_texts(self) -> tuple[str, ...]:
        values = [self.text, *self.aliases]
        if self.speaker:
            values.extend(f"{self.speaker}{separator}{self.text}" for separator in (":", "："))
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = normalize_text(str(value or "")).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class DialogueLibraryMatch:
    game_id: str
    line_id: str
    text: str
    speaker: str
    score: float
    matched_text: str

    def canonical_text(self) -> str:
        if self.speaker:
            return f"{self.speaker}：{self.text}"
        return self.text


class DialogueLibrary:
    def __init__(self, *, game_id: str, title: str, lines: Iterable[DialogueLibraryLine]) -> None:
        self.game_id = game_id
        self.title = title
        self.lines = tuple(lines)
        self._exact: dict[str, DialogueLibraryMatch] = {}
        self._candidates: list[tuple[str, DialogueLibraryMatch]] = []
        self._candidates_by_length: dict[int, list[tuple[str, DialogueLibraryMatch]]] = {}
        for line in self.lines:
            for candidate_text in line.candidate_texts():
                key = dialogue_library_key(candidate_text)
                if not key:
                    continue
                match = DialogueLibraryMatch(
                    game_id=line.game_id,
                    line_id=line.line_id,
                    text=line.text,
                    speaker=line.speaker,
                    score=1.0,
                    matched_text=candidate_text,
                )
                self._exact.setdefault(key, match)
                self._candidates.append((key, match))
                self._candidates_by_length.setdefault(len(key), []).append((key, match))

    def match(self, text: str) -> DialogueLibraryMatch | None:
        key = dialogue_library_key(text)
        if not key:
            return None
        exact = self._exact.get(key)
        if exact is not None:
            return exact
        if len(key) < _MIN_FUZZY_KEY_LENGTH:
            return None
        best_match: DialogueLibraryMatch | None = None
        best_score = 0.0
        for candidate_key, candidate_match in self._fuzzy_candidates_for_key(key):
            score = _match_score(key, candidate_key)
            if score > best_score:
                best_score = score
                best_match = candidate_match
        if best_match is None or best_score < _FUZZY_MATCH_THRESHOLD:
            return None
        return DialogueLibraryMatch(
            game_id=best_match.game_id,
            line_id=best_match.line_id,
            text=best_match.text,
            speaker=best_match.speaker,
            score=best_score,
            matched_text=best_match.matched_text,
        )

    def _fuzzy_candidates_for_key(self, key: str) -> Iterable[tuple[str, DialogueLibraryMatch]]:
        min_length = max(_MIN_FUZZY_KEY_LENGTH, int(len(key) * _FUZZY_MATCH_THRESHOLD))
        max_length = int(len(key) / _FUZZY_MATCH_THRESHOLD) + 1
        for length in range(min_length, max_length + 1):
            yield from self._candidates_by_length.get(length, ())


def dialogue_library_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", normalize_text(str(text or ""))).lower()
    kept: list[str] = []
    for ch in normalized:
        if ch.isspace():
            continue
        category = unicodedata.category(ch)
        if category.startswith("P") or category.startswith("S"):
            continue
        kept.append(ch)
    return "".join(kept)


def load_dialogue_library(path: Path) -> DialogueLibrary:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"dialogue library must be a JSON object: {path}")
    game_id = str(payload.get("game_id") or "").strip()
    if not game_id:
        raise ValueError(f"dialogue library missing game_id: {path}")
    title = str(payload.get("title") or "").strip()
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError(f"dialogue library lines must be a list: {path}")
    lines: list[DialogueLibraryLine] = []
    for index, raw_line in enumerate(raw_lines, start=1):
        line = _coerce_dialogue_line(raw_line, game_id=game_id, fallback_index=index)
        if line is None:
            raise ValueError(
                f"invalid dialogue library line #{index} for game_id={game_id}: {path}"
            )
        lines.append(line)
    return DialogueLibrary(game_id=game_id, title=title, lines=lines)


def match_dialogue_library_for_target(
    text: str,
    *,
    process_name: str,
    normalized_title: str,
    library_dir: Path | None = None,
) -> DialogueLibraryMatch | None:
    if not matches_senren_banka_target(
        process_name=process_name,
        normalized_title=normalized_title,
    ):
        return None
    try:
        library = _load_senren_banka_library(library_dir=library_dir)
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return None
    if library is None:
        return None
    return library.match(text)


def built_in_dialogue_library_status(
    *,
    process_name: str = "",
    normalized_title: str = "",
    library_dir: Path | None = None,
) -> dict[str, Any]:
    packs: list[dict[str, Any]] = []
    active_game_id = ""
    total_lines = 0
    for spec in _BUILTIN_LIBRARY_SPECS:
        path = (library_dir or _DEFAULT_LIBRARY_DIR) / str(spec["file_name"])
        available = path.is_file()
        line_count = 0
        if available:
            try:
                line_count = len(_load_dialogue_library_cached(str(path)).lines)
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                available = False
        matches_target = (
            spec["game_id"] == SENREN_BANKA_GAME_ID
            and matches_senren_banka_target(
                process_name=process_name,
                normalized_title=normalized_title,
            )
        )
        if available:
            total_lines += line_count
        if available and matches_target:
            active_game_id = str(spec["game_id"])
        packs.append(
            {
                "game_id": str(spec["game_id"]),
                "title": str(spec["title"]),
                "source": str(spec["source"]),
                "available": available,
                "line_count": line_count,
                "matches_target": matches_target,
            }
        )
    return {
        "source": "developer_builtin",
        "managed_by": "developer",
        "editable": False,
        "active_game_id": active_game_id,
        "total_line_count": total_lines,
        "packs": packs,
    }


def matches_senren_banka_target(*, process_name: str, normalized_title: str) -> bool:
    process = str(process_name or "").strip().lower()
    if process in _SENREN_BANKA_PROCESS_NAMES:
        return True
    title_key = _title_match_key(normalized_title)
    return any(token_key in title_key for token_key in _SENREN_BANKA_TITLE_KEYS)


def _load_senren_banka_library(*, library_dir: Path | None = None) -> DialogueLibrary | None:
    root = library_dir or _DEFAULT_LIBRARY_DIR
    path = root / _SENREN_BANKA_LIBRARY_FILE
    if not path.is_file():
        return None
    return _load_dialogue_library_cached(str(path))


@lru_cache(maxsize=8)
def _load_dialogue_library_cached(path: str) -> DialogueLibrary:
    return load_dialogue_library(Path(path))


def _coerce_dialogue_line(
    raw_line: Any,
    *,
    game_id: str,
    fallback_index: int,
) -> DialogueLibraryLine | None:
    if isinstance(raw_line, str):
        text = normalize_text(raw_line).strip()
        line_id = f"line:{fallback_index:06d}"
        speaker = ""
        aliases: tuple[str, ...] = ()
    elif isinstance(raw_line, dict):
        text = normalize_text(str(raw_line.get("text") or "")).strip()
        line_id = str(raw_line.get("id") or f"line:{fallback_index:06d}").strip()
        speaker = normalize_text(str(raw_line.get("speaker") or "")).strip()
        raw_aliases = raw_line.get("aliases")
        aliases = tuple(
            normalize_text(str(alias or "")).strip()
            for alias in (raw_aliases if isinstance(raw_aliases, list) else [])
            if normalize_text(str(alias or "")).strip()
        )
    else:
        return None
    if not text:
        return None
    return DialogueLibraryLine(
        game_id=game_id,
        line_id=line_id or f"line:{fallback_index:06d}",
        text=text,
        speaker=speaker,
        aliases=aliases,
    )


def _match_score(observed_key: str, candidate_key: str) -> float:
    if observed_key == candidate_key:
        return 1.0
    shorter, longer = sorted((observed_key, candidate_key), key=len)
    if shorter and shorter in longer:
        return len(shorter) / len(longer)
    distance = _levenshtein_distance(observed_key, candidate_key)
    return 1.0 - (distance / max(len(observed_key), len(candidate_key), 1))


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        return _levenshtein_distance(right, left)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left):
        current = [left_index + 1]
        for right_index, right_char in enumerate(right):
            current.append(
                min(
                    previous[right_index + 1] + 1,
                    current[right_index] + 1,
                    previous[right_index] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]
