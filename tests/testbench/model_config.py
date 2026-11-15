"""Per-session model configuration for the four testbench LLM roles.

Requirements from PLAN §Key technical points + §Settings workspace:

- **chat**     Target AI (system under test) — sends ChatCompletion messages and consumes wire_messages
- **simuser**  Simulated user AI — generates user messages for testers in SimUser mode
- **judge**    Scoring AI — produces scoring JSON according to ScoringSchema
- **memory**   Memory-synthesis AI — runs LLM calls for compression/reflection/persona updates

Each group is a ``ModelGroupConfig`` (provider presets + OpenAI-compatible endpoint details).
``api_key`` remains plaintext for direct requests; persistence paths (saved_sessions/autosave/
export) automatically redact it to ``\"<redacted>\"`` through the persistence layer (P21). This file handles
only **in-session memory copies**, without persistence.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# Four supported roles. Order determines left-to-right UI rendering.
GroupKey = Literal["chat", "simuser", "judge", "memory"]
GROUP_KEYS: tuple[GroupKey, ...] = ("chat", "simuser", "judge", "memory")


class ModelGroupConfig(BaseModel):
    """One OpenAI-compatible endpoint configuration.

    Matches the kwargs accepted by :class:`utils.llm_client.ChatOpenAI`
    (plus a ``provider`` breadcrumb pointing back to the preset that seeded
    the form, purely cosmetic).
    """

    provider: str | None = Field(
        default=None,
        description=(
            "Preset key from config/api_providers.json assist_api_providers "
            "(e.g. 'qwen'). None means the user filled fields manually."
        ),
    )
    base_url: str = Field(default="", description="OpenAI-compatible base URL.")
    api_key: str = Field(
        default="",
        description=(
            "Plaintext API key. Empty is OK when the picked ``provider`` is a"
            " free-tier preset (``config/api_providers.json`` ships its own"
            " key) or when ``tests/api_keys.json`` already holds one for"
            " this provider. Persistence layer redacts on save."
        ),
    )
    model: str = Field(default="", description="Model name to send in request body.")
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description=(
            "Optional sampling temperature. ``None`` means \"do not send a"
            " temperature field in the request body\" — required for models"
            " that reject the parameter entirely (o1 / o3 / gpt-5-thinking /"
            " Claude extended-thinking). UI exposes an empty input = None."
        ),
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional hard cap on response tokens. ``None`` = no cap sent,"
            " model decides its own default."
        ),
    )
    timeout: float | None = Field(
        default=60.0,
        ge=1.0,
        description=(
            "Client-side request timeout in seconds. Not a wire parameter;"
            " only affects the httpx client. ``None`` delegates to the SDK"
            " default."
        ),
    )

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/") if v else v

    def is_configured(self) -> bool:
        """Enough fields filled that a request can actually be attempted."""
        return bool(self.base_url and self.model)

    def summary(self) -> dict[str, Any]:
        """UI-safe dict (api_key masked, never plaintext)."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "api_key_configured": bool(self.api_key),
            "is_configured": self.is_configured(),
        }


class ModelConfigBundle(BaseModel):
    """The 4 groups held as a single bundle so we can (de)serialize in one hop.

    Using an explicit model (vs. a bare ``dict[GroupKey, ModelGroupConfig]``)
    gives us field-level validation + a stable JSON shape across phases.
    """

    chat: ModelGroupConfig = Field(default_factory=ModelGroupConfig)
    simuser: ModelGroupConfig = Field(default_factory=ModelGroupConfig)
    judge: ModelGroupConfig = Field(default_factory=ModelGroupConfig)
    memory: ModelGroupConfig = Field(default_factory=ModelGroupConfig)

    def get(self, group: GroupKey) -> ModelGroupConfig:
        return getattr(self, group)

    def set(self, group: GroupKey, value: ModelGroupConfig) -> None:
        setattr(self, group, value)

    def summary(self) -> dict[str, Any]:
        return {key: self.get(key).summary() for key in GROUP_KEYS}

    @classmethod
    def from_session_value(cls, raw: Any) -> "ModelConfigBundle":
        """Accept either a stored dict (legacy/loaded session) or None.

        Returning a fresh default bundle on ``None`` / ``{}`` keeps Session's
        ``model_config: dict`` default compatible without special-casing.
        """
        if not raw:
            return cls()
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            # Allow partial payloads — missing keys fall back to defaults.
            data: dict[str, Any] = {}
            for key in GROUP_KEYS:
                group_raw = raw.get(key)
                if isinstance(group_raw, ModelGroupConfig):
                    data[key] = group_raw
                elif isinstance(group_raw, dict):
                    data[key] = ModelGroupConfig.model_validate(group_raw)
            return cls(**data)
        raise TypeError(f"Unsupported model_config payload type: {type(raw)!r}")


__all__ = [
    "GROUP_KEYS",
    "GroupKey",
    "ModelConfigBundle",
    "ModelGroupConfig",
]
