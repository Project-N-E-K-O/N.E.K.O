"""Slow global signal collection for topic hooks.

The signal layer deliberately does not decide what the user cares about.
It only keeps compact evidence across a longer window so the LLM can judge
stable, high-readiness topic opportunities instead of overfitting the last
few chat turns.
"""
from __future__ import annotations

import json
import time
import threading
import atexit
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_logic.topic.common import clean_text
from utils.file_utils import atomic_write_json
from utils.tokenize import truncate_to_tokens


# Per-turn evidence cap in tokens. The topic candidate prompt feeds this slow
# evidence as its only conversation input, so the per-turn budget lives here.
_MAX_SIGNAL_TOKENS_PER_TURN = 500
_TOKEN_PRECAP_CHARS_PER_TOKEN = 8
_MAX_GLOBAL_TURNS = 60
_SIGNAL_RETENTION_SECONDS = 12 * 60 * 60
_FILLER_TEXTS = {
    "你好",
    "啊",
    "嗯",
    "哦",
    "好",
    "可以",
    "对",
    "對",
    "行",
    "行吧",
    "哈哈",
    "没事",
    "沒事",
    "不知道",
}


_GLOBAL_SIGNAL_LABELS = {
    "zh": {
        "user": "用户",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "zh-TW": {
        "user": "使用者",
        "ai": "AI",
        "seconds_ago": "{value}s前",
        "minutes_ago": "{value}min前",
        "hours_ago": "{value}h前",
    },
    "en": {
        "user": "User",
        "ai": "AI",
        "seconds_ago": "{value}s ago",
        "minutes_ago": "{value}min ago",
        "hours_ago": "{value}h ago",
    },
    "ja": {
        "user": "ユーザー",
        "ai": "AI",
        "seconds_ago": "{value}秒前",
        "minutes_ago": "{value}分前",
        "hours_ago": "{value}時間前",
    },
    "ko": {
        "user": "사용자",
        "ai": "AI",
        "seconds_ago": "{value}초 전",
        "minutes_ago": "{value}분 전",
        "hours_ago": "{value}시간 전",
    },
    "es": {
        "user": "Usuario",
        "ai": "IA",
        "seconds_ago": "hace {value}s",
        "minutes_ago": "hace {value}min",
        "hours_ago": "hace {value}h",
    },
    "pt": {
        "user": "Usuário",
        "ai": "IA",
        "seconds_ago": "há {value}s",
        "minutes_ago": "há {value}min",
        "hours_ago": "há {value}h",
    },
    "ru": {
        "user": "Пользователь",
        "ai": "AI",
        "seconds_ago": "{value}с назад",
        "minutes_ago": "{value}мин назад",
        "hours_ago": "{value}ч назад",
    },
}


def _clean_text(value: Any, *, token_limit: int = _MAX_SIGNAL_TOKENS_PER_TURN) -> str:
    precap = max(token_limit, token_limit * _TOKEN_PRECAP_CHARS_PER_TOKEN)
    return truncate_to_tokens(clean_text(value, limit=precap), token_limit)


def _label_key_for_lang(lang: str | None) -> str:
    raw = str(lang or "").strip().replace("_", "-")
    if not raw:
        return "zh"
    if raw in _GLOBAL_SIGNAL_LABELS:
        return raw
    lower = raw.lower()
    if lower.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return "zh-TW"
    if lower.startswith("zh"):
        return "zh"
    short = lower.split("-", 1)[0]
    return short if short in _GLOBAL_SIGNAL_LABELS else "en"


def _format_age(age_s: float, labels: Mapping[str, str]) -> str:
    if age_s < 90:
        return labels["seconds_ago"].format(value=int(age_s))
    if age_s < 3600:
        return labels["minutes_ago"].format(value=int(age_s / 60))
    return labels["hours_ago"].format(value=int(age_s / 3600))


@dataclass(frozen=True)
class TopicTurnSignal:
    actor: str
    text: str
    timestamp: float


class TopicSignalStore:
    """Slow evidence store, scoped per character.

    The in-memory view may be backed by a small local-state JSON file so
    short restart loops merge into the same candidate window. Persistence is
    optional for tests and embedded callers.
    """

    def __init__(
        self,
        *,
        min_user_turns_for_topic: int = 8,
        max_turns: int = _MAX_GLOBAL_TURNS,
        retention_seconds: float = _SIGNAL_RETENTION_SECONDS,
        persistence_path: str | Path | None = None,
        persistence_flush_delay_seconds: float = 1.0,
    ) -> None:
        self._min_user_turns_for_topic = max(1, int(min_user_turns_for_topic))
        self._max_turns = max(1, int(max_turns))
        self._retention_seconds = max(0.0, float(retention_seconds))
        self._persistence_path = Path(persistence_path) if persistence_path else None
        self._persistence_flush_delay_seconds = max(
            0.0,
            float(persistence_flush_delay_seconds),
        )
        self._persist_lock = threading.RLock()
        self._persist_write_lock = threading.Lock()
        self._persist_timer: threading.Timer | None = None
        self._persist_dirty = False
        self._turns: dict[str, deque[TopicTurnSignal]] = defaultdict(
            lambda: deque(maxlen=self._max_turns)
        )
        self._load()
        if self._persistence_path is not None:
            atexit.register(self.flush)

    def note_turn(
        self,
        lanlan_name: str,
        *,
        actor: str,
        text: Any,
        now: float | None = None,
    ) -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        name = str(lanlan_name or "default")
        safe_actor = "ai" if actor == "ai" else "user"
        timestamp = float(now if now is not None else time.time())
        with self._persist_lock:
            self._turns[name].append(
                TopicTurnSignal(
                    actor=safe_actor,
                    text=cleaned,
                    timestamp=timestamp,
                )
            )
            self._prune(name, now=timestamp)
        self._request_persist()

    def clear(self, lanlan_name: str) -> bool:
        name = str(lanlan_name or "default")
        with self._persist_lock:
            changed = name in self._turns
            if changed:
                self._turns.pop(name, None)
        if changed:
            self._request_persist()
        return changed

    def clear_until(self, lanlan_name: str, *, timestamp: float | None) -> bool:
        if timestamp is None:
            return self.clear(lanlan_name)
        name = str(lanlan_name or "default")
        cutoff = float(timestamp)
        with self._persist_lock:
            turns = self._turns.get(name)
            if not turns:
                return False
            retained = [
                turn for turn in turns
                if float(turn.timestamp) > cutoff
            ][-self._max_turns:]
            changed = len(retained) != len(turns)
            if not changed:
                return False
            if retained:
                self._turns[name] = deque(retained, maxlen=self._max_turns)
            else:
                self._turns.pop(name, None)
        self._request_persist()
        return True

    def names(self) -> list[str]:
        with self._persist_lock:
            return list(self._turns)

    def last_turn_at(self, lanlan_name: str) -> float | None:
        name = str(lanlan_name or "default")
        with self._persist_lock:
            pruned = self._prune(name)
            turns = self._turns.get(name)
            if not turns:
                if pruned:
                    self._request_persist()
                return None
            timestamp = float(turns[-1].timestamp)
        if pruned:
            self._request_persist()
        return timestamp

    def readiness_percent(self, lanlan_name: str) -> int:
        # Coarse "have we heard enough to bother analysing" estimate, for logs.
        pruned = False
        with self._persist_lock:
            pruned = self._prune(lanlan_name)
            count = len(self._meaningful_user_turns(lanlan_name))
        if pruned:
            self._request_persist()
        return min(100, int(count * 100 / self._min_user_turns_for_topic))

    def is_ready(self, lanlan_name: str) -> bool:
        pruned = False
        with self._persist_lock:
            pruned = self._prune(lanlan_name)
            ready = (
                len(self._meaningful_user_turns(lanlan_name))
                >= self._min_user_turns_for_topic
            )
        if pruned:
            self._request_persist()
        return ready

    def format_global_signals(self, lanlan_name: str, *, lang: str | None = None) -> str:
        """Render the slow-evidence turns as the topic prompt's only context.

        Just the turn list — the readiness count gate (see ``is_ready`` /
        ``readiness_percent``) stays backend-only and never reaches the prompt.
        The caller fences this block with the conversation-history watermark.
        """
        name = str(lanlan_name or "default")
        pruned = False
        with self._persist_lock:
            pruned = self._prune(name)
            turns = list(self._turns.get(name, ()))
        if pruned:
            self._request_persist()
        if not turns:
            return ""

        labels = _GLOBAL_SIGNAL_LABELS[_label_key_for_lang(lang)]
        base_ts = turns[-1].timestamp
        lines: list[str] = []
        for turn in turns:
            age_s = max(0.0, base_ts - turn.timestamp)
            age = _format_age(age_s, labels)
            label = labels["user"] if turn.actor == "user" else labels["ai"]
            lines.append(f"- [{age}] {label}: {turn.text}")
        return "\n".join(lines)

    def _user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        name = str(lanlan_name or "default")
        self._prune(name)
        return [turn for turn in self._turns.get(name, ()) if turn.actor == "user"]

    def _meaningful_user_turns(self, lanlan_name: str) -> list[TopicTurnSignal]:
        return [
            turn for turn in self._user_turns(lanlan_name)
            if _is_meaningful_turn(turn.text)
        ]

    def _prune(self, lanlan_name: str, *, now: float | None = None) -> bool:
        name = str(lanlan_name or "default")
        turns = self._turns.get(name)
        if not turns:
            return False
        if self._retention_seconds <= 0:
            self._turns.pop(name, None)
            return True
        current_time = float(now if now is not None else time.time())
        retained = [
            turn for turn in turns
            if current_time - float(turn.timestamp) <= self._retention_seconds
        ][-self._max_turns:]
        changed = len(retained) != len(turns)
        if retained:
            self._turns[name] = deque(retained, maxlen=self._max_turns)
        else:
            self._turns.pop(name, None)
        return changed

    def _load(self) -> None:
        path = self._persistence_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        characters = payload.get("characters") if isinstance(payload, dict) else None
        if not isinstance(characters, dict):
            return
        pruned_on_load = False
        for name, entries in characters.items():
            if not isinstance(entries, list):
                continue
            safe_name = str(name or "default")
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                actor = "ai" if entry.get("actor") == "ai" else "user"
                text = _clean_text(entry.get("text"))
                if not text:
                    continue
                try:
                    timestamp = float(entry.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                self._turns[safe_name].append(
                    TopicTurnSignal(actor=actor, text=text, timestamp=timestamp)
                )
            pruned_on_load = self._prune(safe_name) or pruned_on_load
        if pruned_on_load:
            with self._persist_lock:
                self._persist_dirty = True
            self._request_persist()

    def flush(self) -> None:
        """Persist any pending topic signals.

        Normal chat turns request a delayed background flush so user-message
        handling never waits on fsync. Privacy cleanup and tests can call this
        method when they need the local-state file to reflect the in-memory
        view immediately.
        """
        with self._persist_write_lock:
            with self._persist_lock:
                timer = self._persist_timer
                self._persist_timer = None
                if timer is not None and timer is not threading.current_thread():
                    timer.cancel()
                if not self._persist_dirty:
                    return
                payload = self._persistence_payload_locked()
                self._persist_dirty = False

            write_result = self._write_payload(payload)
        if write_result is not False:
            return

        with self._persist_lock:
            self._persist_dirty = True
            if self._persistence_path is not None and self._persist_timer is None:
                timer = threading.Timer(self._persistence_flush_delay_seconds, self.flush)
                timer.daemon = True
                self._persist_timer = timer
                timer.start()

    def _request_persist(self) -> None:
        path = self._persistence_path
        if path is None:
            return
        with self._persist_lock:
            self._persist_dirty = True
            if self._persist_timer is not None:
                return
            timer = threading.Timer(self._persistence_flush_delay_seconds, self.flush)
            timer.daemon = True
            self._persist_timer = timer
            timer.start()

    def _persistence_payload_locked(self) -> dict[str, Any]:
        now = time.time()
        for name in list(self._turns):
            self._prune(name, now=now)
        return {
            "version": 1,
            "characters": {
                name: [
                    {
                        "actor": turn.actor,
                        "text": turn.text,
                        "timestamp": turn.timestamp,
                    }
                    for turn in turns
                ]
                for name, turns in self._turns.items()
                if turns
            },
        }

    def _write_payload(self, payload: Mapping[str, Any]) -> bool:
        path = self._persistence_path
        if path is None:
            return True
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


def _is_meaningful_turn(text: str) -> bool:
    """Whether a user turn carries enough signal to count toward readiness.

    Filler words and near-empty turns don't count; anything with a few real
    information characters does. Coarse "have we heard enough to bother
    analysing" gate, not a quality score.
    """
    cleaned = _clean_text(text, token_limit=120)
    if not cleaned or cleaned.lower() in _FILLER_TEXTS:
        return False
    signal_len = sum(
        1 for char in cleaned
        if ("一" <= char <= "鿿") or char.isalnum()
    )
    return signal_len >= 3
