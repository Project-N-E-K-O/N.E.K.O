from __future__ import annotations

from typing import Any


class STS2ModeController:
    _ALIASES = {
        "program": "program",
        "full-program": "program",
        "full_program": "program",
        "half-program": "program",
        "half_program": "program",
        "full-model": "program",
        "full_model": "program",
        "model": "program",
        "全程序": "program",
        "半程序": "program",
        "全模型": "program",
        "standby": "standby",
        "待机": "standby",
    }

    def __init__(self, mode: str = "program") -> None:
        self._default_mode = self.normalize(mode)

    def normalize(self, mode: Any) -> str:
        raw = str(mode or self._default_mode or "program").strip().lower().replace(" ", "_")
        return self._ALIASES.get(raw, "program")

    def is_standby(self, mode: Any) -> bool:
        return self.normalize(mode) == "standby"

    def describe(self, mode: Any) -> dict[str, Any]:
        normalized = self.normalize(mode)
        return {
            "mode": normalized,
            "standby": self.is_standby(normalized),
            "allows_planner": normalized == "program",
            "allows_game_llm": False,
            "prefers_heuristic": normalized == "program",
            "prefers_model": False,
        }


__all__ = ["STS2ModeController"]
