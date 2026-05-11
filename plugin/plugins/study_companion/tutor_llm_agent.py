from __future__ import annotations

import asyncio
from typing import Any

from plugin.sdk.plugin import SdkError

from .llm_prompts import build_concept_explain_messages
from .models import MODE_CONCEPT_EXPLAIN, StudyConfig, TutorReply, utc_now_iso


class TutorLLMAgent:
    def __init__(self, *, logger: Any, config: StudyConfig) -> None:
        self._logger = logger
        self._config = config
        self._llm_cache: dict[tuple[Any, ...], Any] = {}
        self._lock: asyncio.Lock | None = None

    def update_config(self, config: StudyConfig) -> None:
        self._config = config
        self._llm_cache.clear()

    async def shutdown(self) -> None:
        llms = list(self._llm_cache.values())
        self._llm_cache.clear()
        for llm in llms:
            close = getattr(llm, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass

    async def concept_explain(
        self,
        text: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        normalized = str(text or "").strip()
        if not normalized:
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text="",
                reply="Please provide text or capture a readable screen first.",
                degraded=True,
                diagnostic="empty_input",
                created_at=utc_now_iso(),
            )
        messages = build_concept_explain_messages(
            text=normalized,
            language=self._config.language,
            context=context,
        )
        try:
            content = await self._call_model(messages)
            reply = content.strip()
            if not reply:
                raise SdkError("empty model response")
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text=normalized,
                reply=reply,
                degraded=False,
                created_at=utc_now_iso(),
            )
        except Exception as exc:
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text=normalized,
                reply=self._fallback_explanation(normalized),
                degraded=True,
                diagnostic=str(exc),
                created_at=utc_now_iso(),
            )

    async def _call_model(self, messages: list[dict[str, str]]) -> str:
        from utils.config_manager import get_config_manager
        from utils.llm_client import create_chat_llm
        from utils.token_tracker import set_call_type

        api_config = get_config_manager().get_model_api_config("summary")
        base_url = str(api_config.get("base_url") or "").strip()
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not base_url or not model:
            raise SdkError("missing configured summary model")
        key = (base_url, model, bool(api_key), self._config.llm_temperature, self._config.llm_max_tokens)
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            llm = self._llm_cache.get(key)
            if llm is None:
                llm = create_chat_llm(
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    temperature=float(self._config.llm_temperature),
                    max_completion_tokens=int(self._config.llm_max_tokens),
                    timeout=float(self._config.llm_call_timeout_seconds) + 0.5,
                )
                self._llm_cache[key] = llm
        set_call_type("summary")
        ainvoke = getattr(llm, "ainvoke", None)
        if callable(ainvoke):
            response = await ainvoke(messages)
        else:
            response = await asyncio.to_thread(llm.invoke, messages)
        return str(getattr(response, "content", "") or response)

    @staticmethod
    def _fallback_explanation(text: str) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text[:120])
        return (
            f"Key text: {first_line}\n\n"
            "Explanation: I could not reach the configured model, so this is a local fallback. "
            "Read the statement once for definitions, then identify the cause, result, and any formula or term that changes the conclusion.\n\n"
            "Check question: What is the main term or relationship you need to remember from this text?"
        )
