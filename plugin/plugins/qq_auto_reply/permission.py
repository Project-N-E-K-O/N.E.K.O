"""
权限管理模块

根据 QQ 号管理用户权限等级
"""

import math

from typing import Any, Dict, List, Optional


class PermissionManager:
    """权限管理器"""

    VALID_LEVELS = {"admin", "trusted", "normal"}
    NICKNAME_MAX_CHARS = 64
    _NICKNAME_FORBIDDEN_CHARS = frozenset("[]|")
    _NICKNAME_ALLOWED_FORMAT_CHARS = frozenset({"\u200d"})

    def __init__(
        self,
        trusted_users: List[Dict[str, Any]] = None,
        speaker_trust_profiles: Dict[str, Dict[str, Any]] | None = None,
    ):
        """
        初始化权限管理器

        Args:
            trusted_users: 信任用户列表，格式: [{"qq": "123456", "level": "admin", "nickname": "小明"}, ...]
        """
        self._users: Dict[str, Dict[str, Any]] = {}  # {qq: {level, nickname?, normal_relay_probability?}}
        self._speaker_trust_profiles: Dict[str, Dict[str, Any]] = {}

        for raw_qq, raw_profile in (speaker_trust_profiles or {}).items():
            qq = self._normalize_qq(raw_qq)
            if not qq or not isinstance(raw_profile, dict):
                continue
            self._speaker_trust_profiles[qq] = self._normalize_speaker_profile(
                raw_profile
            )

        if trusted_users:
            for user in trusted_users:
                qq = self._normalize_qq(user.get("qq", ""))
                level = self._normalize_level(user.get("level", "trusted"))
                nickname = str(user.get("nickname", "") or "").strip()
                normal_relay_probability = self._normalize_probability(user.get("normal_relay_probability"))
                if qq:
                    self._users[qq] = {
                        "level": level,
                        "nickname": nickname,
                        "normal_relay_probability": normal_relay_probability,
                    }

    @staticmethod
    def _normalize_qq(qq_number: str) -> str:
        return str(qq_number or "").strip()

    @classmethod
    def _normalize_level(cls, level: str) -> str:
        level_text = str(level or "trusted").strip().lower()
        return level_text if level_text in cls.VALID_LEVELS else "trusted"

    @staticmethod
    def _normalize_probability(value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            normalized = float(value)
        except Exception:
            return None
        if normalized < 0.0 or normalized > 1.0:
            return None
        return normalized

    @classmethod
    def _normalize_nickname_for_write(cls, nickname: str) -> Optional[str]:
        raw = str(nickname or "")
        normalized = raw.strip()
        if len(normalized) > cls.NICKNAME_MAX_CHARS:
            return None
        if any(
            char in cls._NICKNAME_FORBIDDEN_CHARS
            or (
                not char.isprintable()
                and char not in cls._NICKNAME_ALLOWED_FORMAT_CHARS
            )
            for char in raw
        ):
            return None
        return normalized

    def add_user(self, qq_number: str, level: str = "trusted", nickname: str = "", normal_relay_probability: Any = None):
        """
        添加用户

        Args:
            qq_number: QQ 号
            level: 权限等级 (admin, trusted, normal)
            nickname: 用户昵称（可选）
        """
        qq_str = self._normalize_qq(qq_number)
        if not qq_str:
            return False
        normalized_level = self._normalize_level(level)
        normalized_nickname = (
            "" if normalized_level == "admin"
            else self._normalize_nickname_for_write(nickname)
        )
        if normalized_nickname is None:
            return False
        self._users[qq_str] = {
            "level": normalized_level,
            "nickname": normalized_nickname,
            "normal_relay_probability": self._normalize_probability(normal_relay_probability),
        }
        return True

    def remove_user(self, qq_number: str):
        """移除用户"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str in self._users:
            del self._users[qq_str]

    def get_permission_level(self, qq_number: str) -> str:
        """
        获取用户权限等级

        Args:
            qq_number: QQ 号

        Returns:
            权限等级: admin, trusted, normal, none
        """
        qq_str = self._normalize_qq(qq_number)
        user = self._users.get(qq_str) or {}
        return str(user.get("level") or "none")

    def list_users(self) -> List[Dict[str, Any]]:
        """列出所有用户"""
        result = []
        for qq, user in self._users.items():
            user_info: Dict[str, Any] = {"qq": qq, "level": user.get("level", "trusted")}
            nickname = str(user.get("nickname") or "").strip()
            if nickname:
                user_info["nickname"] = nickname
            probability = self._normalize_probability(user.get("normal_relay_probability"))
            if probability is not None:
                user_info["normal_relay_probability"] = probability
            result.append(user_info)
        return result

    def get_nickname(self, qq_number: str) -> Optional[str]:
        """获取用户昵称"""
        user = self._users.get(self._normalize_qq(qq_number)) or {}
        nickname = str(user.get("nickname") or "").strip()
        return nickname or None

    def get_normal_relay_probability(self, qq_number: str) -> Optional[float]:
        user = self._users.get(self._normalize_qq(qq_number)) or {}
        return self._normalize_probability(user.get("normal_relay_probability"))

    @staticmethod
    def _normalize_speaker_profile(value: Dict[str, Any] | None) -> Dict[str, Any]:
        from config import (
            SPEAKER_TRUST_ADJUSTMENT_LIMIT,
            SPEAKER_TRUST_EVENT_HISTORY_LIMIT,
        )

        raw = value if isinstance(value, dict) else {}
        try:
            adjustment = float(raw.get("adjustment", 0.0) or 0.0)
        except (TypeError, ValueError):
            adjustment = 0.0
        if not math.isfinite(adjustment):
            adjustment = 0.0
        adjustment = max(
            -SPEAKER_TRUST_ADJUSTMENT_LIMIT,
            min(SPEAKER_TRUST_ADJUSTMENT_LIMIT, adjustment),
        )
        try:
            message_count = max(0, int(raw.get("message_count", 0) or 0))
        except (TypeError, ValueError):
            message_count = 0
        def _event_ids(key: str, *, durable: bool = False) -> list[str]:
            raw_events = raw.get(key)
            if not isinstance(raw_events, list):
                return []
            events: list[str] = []
            for event_id in raw_events:
                normalized = str(event_id or "").strip()
                if normalized and normalized not in events:
                    events.append(normalized[:96])
            if durable:
                return events
            return events[-SPEAKER_TRUST_EVENT_HISTORY_LIMIT:]
        return {
            "adjustment": adjustment,
            "message_count": message_count,
            # Activity and owner signals must not share an eviction ring:
            # message spam could otherwise evict correction ids and replay them.
            "processed_activity_events": _event_ids(
                "processed_activity_events"
            ),
            "processed_signal_events": _event_ids(
                "processed_signal_events", durable=True,
            ),
        }

    def speaker_trust_profiles(self) -> Dict[str, Dict[str, Any]]:
        return {
            qq: {
                "adjustment": float(profile.get("adjustment", 0.0) or 0.0),
                "message_count": int(profile.get("message_count", 0) or 0),
                "processed_activity_events": list(
                    profile.get("processed_activity_events") or []
                ),
                "processed_signal_events": list(
                    profile.get("processed_signal_events") or []
                ),
            }
            for qq, profile in self._speaker_trust_profiles.items()
        }

    def replace_speaker_trust_profiles(
        self, profiles: Dict[str, Dict[str, Any]],
    ) -> None:
        self._speaker_trust_profiles = {
            self._normalize_qq(qq): self._normalize_speaker_profile(profile)
            for qq, profile in (profiles or {}).items()
            if self._normalize_qq(qq) and isinstance(profile, dict)
        }

    def get_speaker_trust(
        self, qq_number: str, permission_level: str | None = None,
    ) -> float:
        """Global per-QQ trust shared by every group and private participant."""
        from config import (
            SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
            SPEAKER_TRUST_ACTIVITY_WEIGHT,
            SPEAKER_TRUST_BY_PERMISSION_LEVEL,
            SPEAKER_TRUST_DEFAULT,
        )

        qq = self._normalize_qq(qq_number)
        # A caller handling a buffered session may pass the permission snapshot
        # captured when that session started.  Evolution remains global per QQ,
        # while the baseline must not change halfway through the buffered write.
        level = permission_level or self.get_permission_level(qq)
        base = SPEAKER_TRUST_BY_PERMISSION_LEVEL.get(level, SPEAKER_TRUST_DEFAULT)
        profile = self._speaker_trust_profiles.get(qq) or {}
        adjustment = float(profile.get("adjustment", 0.0) or 0.0)
        activity = min(
            SPEAKER_TRUST_ACTIVITY_MAX_BONUS,
            max(0, int(profile.get("message_count", 0) or 0))
            * SPEAKER_TRUST_ACTIVITY_WEIGHT,
        )
        return max(0.0, min(1.0, base + adjustment + activity))

    def record_speaker_activity(
        self, qq_number: str, message_count: int, event_id: str,
    ) -> bool:
        qq = self._normalize_qq(qq_number)
        event = str(event_id or "").strip()[:96]
        count = max(0, int(message_count or 0))
        if not qq or not event or count == 0:
            return False
        profile = self._speaker_trust_profiles.setdefault(
            qq, self._normalize_speaker_profile({}),
        )
        processed = profile["processed_activity_events"]
        if event in processed:
            return False
        from config import SPEAKER_TRUST_EVENT_HISTORY_LIMIT
        processed.append(event)
        del processed[:-SPEAKER_TRUST_EVENT_HISTORY_LIMIT]
        profile["message_count"] = int(profile.get("message_count", 0) or 0) + count
        return True

    def apply_speaker_trust_events(self, events: list[dict]) -> int:
        """Apply only server-issued deterministic owner signals, idempotently."""
        from config import (
            SPEAKER_TRUST_ADJUSTMENT_LIMIT,
            SPEAKER_TRUST_CONFIRMATION_DELTA,
            SPEAKER_TRUST_CORRECTION_DELTA,
        )

        applied = 0
        for item in events or []:
            if not isinstance(item, dict):
                continue
            speaker_id = str(item.get("speaker_id") or "")
            platform, sep, qq = speaker_id.partition(":")
            event_id = str(item.get("event_id") or "").strip()[:96]
            kind = item.get("kind")
            if platform != "qq" or not sep or not qq or not event_id:
                continue
            if kind not in {"confirmation", "correction"}:
                continue
            profile = self._speaker_trust_profiles.setdefault(
                qq, self._normalize_speaker_profile({}),
            )
            processed = profile["processed_signal_events"]
            if event_id in processed:
                continue
            # Owner signals change arbitration power.  Their deterministic
            # IDs form an exact append-only replay ledger: truncating this
            # list would let an old correction apply again after eviction.
            processed.append(event_id)
            delta = (
                SPEAKER_TRUST_CONFIRMATION_DELTA
                if kind == "confirmation"
                else -SPEAKER_TRUST_CORRECTION_DELTA
            )
            profile["adjustment"] = max(
                -SPEAKER_TRUST_ADJUSTMENT_LIMIT,
                min(
                    SPEAKER_TRUST_ADJUSTMENT_LIMIT,
                    float(profile.get("adjustment", 0.0) or 0.0) + delta,
                ),
            )
            applied += 1
        return applied

    def set_nickname(self, qq_number: str, nickname: str):
        """设置用户昵称"""
        qq_str = self._normalize_qq(qq_number)
        if qq_str not in self._users:
            return False
        normalized = self._normalize_nickname_for_write(nickname)
        if normalized is None:
            return False
        self._users[qq_str]["nickname"] = normalized
        return True

    def find_users_by_nickname(self, nickname: str) -> List[Dict[str, Any]]:
        """按配置昵称查找用户（精确匹配）"""
        target = str(nickname or "").strip()
        if not target:
            return []
        result = []
        for qq, user in self._users.items():
            saved_nickname = str(user.get("nickname") or "").strip()
            if saved_nickname == target:
                user_info: Dict[str, Any] = {"qq": qq, "level": user.get("level", "trusted"), "nickname": saved_nickname}
                probability = self._normalize_probability(user.get("normal_relay_probability"))
                if probability is not None:
                    user_info["normal_relay_probability"] = probability
                result.append(user_info)
        return result

    def is_admin(self, qq_number: str) -> bool:
        """检查是否是管理员"""
        return self.get_permission_level(qq_number) == "admin"

    def is_trusted(self, qq_number: str) -> bool:
        """检查是否是信任用户（包括管理员）"""
        level = self.get_permission_level(qq_number)
        return level in ["admin", "trusted"]
