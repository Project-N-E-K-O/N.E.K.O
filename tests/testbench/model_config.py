"""Per-session model configuration for the four testbench LLM roles.

PLAN §关键技术点 + §Settings workspace 要求:

- **chat**     目标 AI (被测对象) — 发消息给 ChatCompletion, 消费 wire_messages
- **simuser**  假想用户 AI — 在 SimUser 模式下替测试人员生成 user 消息
- **judge**    评分 AI — 根据 ScoringSchema 出评分 JSON
- **memory**   记忆合成 AI — 跑压缩/反思/persona 更新的 LLM 调用

每组都是一份 ``ModelGroupConfig`` (provider 预设 + OpenAI 兼容端点细节).
``api_key`` 字段保留明文以便直接发请求; 持久化路径 (saved_sessions/autosave/
export) 里会由 persistence 层 (P21) 自动脱敏成 ``"<redacted>"``. 本文件只关心
**会话内内存副本**, 不做任何持久化.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# 受支持的 4 个角色. 顺序影响 UI 左→右渲染顺序.
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
