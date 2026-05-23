from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


PreferenceDomain = str
PreferenceKey = str


@dataclass(slots=True)
class PreferenceRecord:
    domain: PreferenceDomain
    key: PreferenceKey
    value: dict[str, Any]
    source: str = "user"
    updated_at: float = field(default_factory=time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "key": self.key,
            "value": dict(self.value),
            "source": self.source,
            "updated_at": self.updated_at,
        }


class STS2PreferenceStore:
    _DOMAINS = (
        "event_preferences",
        "card_reward_preferences",
        "card_remove_preferences",
        "route_preferences",
        "combat_preferences",
        "event_overrides",
        "enemy_overrides",
    )

    def __init__(self) -> None:
        self._store: dict[PreferenceDomain, dict[PreferenceKey, PreferenceRecord]] = {
            domain: {} for domain in self._DOMAINS
        }

    def upsert(self, domain: PreferenceDomain, key: PreferenceKey, value: dict[str, Any], *, source: str = "user") -> dict[str, Any]:
        self._require_domain(domain)
        normalized_key = self._normalize_key(key)
        record = PreferenceRecord(
            domain=domain,
            key=normalized_key,
            value=dict(value),
            source=source,
        )
        self._store[domain][normalized_key] = record
        return record.as_dict()

    def get(self, domain: PreferenceDomain, key: PreferenceKey) -> dict[str, Any] | None:
        self._require_domain(domain)
        record = self._store[domain].get(self._normalize_key(key))
        return record.as_dict() if record is not None else None

    def list_domain(self, domain: PreferenceDomain) -> list[dict[str, Any]]:
        self._require_domain(domain)
        return [record.as_dict() for record in self._store[domain].values()]

    def delete(self, domain: PreferenceDomain, key: PreferenceKey) -> bool:
        self._require_domain(domain)
        normalized_key = self._normalize_key(key)
        return self._store[domain].pop(normalized_key, None) is not None

    def export_all(self) -> dict[str, list[dict[str, Any]]]:
        return {
            domain: [record.as_dict() for record in records.values()]
            for domain, records in self._store.items()
        }

    def _require_domain(self, domain: PreferenceDomain) -> None:
        if domain not in self._store:
            raise ValueError(f"unsupported preference domain: {domain}")

    def _normalize_key(self, key: PreferenceKey) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            raise ValueError("preference key must be non-empty")
        return normalized


__all__ = ["PreferenceRecord", "STS2PreferenceStore"]
