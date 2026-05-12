from __future__ import annotations

import asyncio
import hashlib
import inspect
from typing import Any

from plugin.sdk.plugin import SdkError

from .llm_prompts import build_concept_explain_messages
from .constants import MODE_COMPANION, MODE_TEACHING
from .mode_manager import build_transition_phrase, normalize_mode, study_i18n_t
from .models import MODE_CONCEPT_EXPLAIN, StudyConfig, TutorReply, utc_now_iso


class TutorLLMAgent:
    def __init__(self, *, logger: Any, config: StudyConfig) -> None:
        self._logger = logger
        self._config = config
        self._llm_cache: dict[tuple[Any, ...], Any] = {}
        self._llm_locks: dict[tuple[Any, ...], asyncio.Lock] = {}

    def update_config(self, config: StudyConfig) -> None:
        llms = list(self._llm_cache.values())
        for llm in llms:
            self._close_cached_llm(llm)
        self._config = config
        self._llm_cache.clear()
        self._llm_locks.clear()

    async def shutdown(self) -> None:
        llms = list(self._llm_cache.values())
        self._llm_cache.clear()
        self._llm_locks.clear()
        for llm in llms:
            await self._close_cached_llm_async(llm)

    def _close_cached_llm(self, llm: Any) -> None:
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            try:
                result = close()
            except Exception:
                return
            if inspect.isawaitable(result):
                self._finalize_async_close(result)
            return

    async def _close_cached_llm_async(self, llm: Any) -> None:
        for method_name in ("shutdown", "aclose"):
            close = getattr(llm, method_name, None)
            if not callable(close):
                continue
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
            return

    def _finalize_async_close(self, close_result: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(close_result)
            except Exception:
                pass
            return
        task = loop.create_task(close_result)
        task.add_done_callback(self._consume_close_exception)

    @staticmethod
    def _consume_close_exception(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    @staticmethod
    def _is_english_language(language: str | None) -> bool:
        language_tag = str(language or "").strip().lower().replace("_", "-")
        primary = language_tag.split("-", 1)[0]
        return primary == "en" or primary == "eng"

    def _localize_reply(self, language: str | None, key: str, **values: Any) -> str:
        if key == "empty_input":
            return study_i18n_t(
                language,
                "reply.empty_input",
                default=str(values.get("default") or "Please provide text or capture a readable screen first."),
            )
        if key == "fallback_explanation":
            first_line = str(values.get("first_line") or "").strip()
            return study_i18n_t(
                language,
                "reply.fallback_explanation",
                default=str(values.get("default") or ""),
                first_line=first_line,
            )
        return str(values.get("default") or "")

    async def concept_explain(
        self,
        text: str,
        *,
        mode: str = MODE_COMPANION,
        context: dict[str, Any] | None = None,
    ) -> TutorReply:
        normalized = str(text or "").strip()
        if not normalized:
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text="",
                reply=self._localize_reply(self._config.language, "empty_input"),
                degraded=True,
                diagnostic="empty_input",
                created_at=utc_now_iso(),
            )
        selected_mode = normalize_mode(mode)
        teaching_prefix = (
            build_transition_phrase(MODE_TEACHING, language=self._config.language, outcome="changed")
            if selected_mode == MODE_TEACHING
            else ""
        )
        messages = build_concept_explain_messages(
            text=normalized,
            language=self._config.language,
            mode=selected_mode,
            context=context,
        )
        try:
            content = await self._call_model(messages)
            reply = content.strip()
            if not reply:
                raise SdkError("empty model response")
            if teaching_prefix and not reply.startswith(teaching_prefix):
                reply = f"{teaching_prefix}\n\n{reply}"
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text=normalized,
                reply=reply,
                degraded=False,
                created_at=utc_now_iso(),
            )
        except Exception as exc:
            fallback_reply = self._localize_reply(
                self._config.language,
                "fallback_explanation",
                default=(
                    "Key text: {first_line}\n\n"
                    "Explanation: I could not reach the configured model, so this is a local fallback. "
                    "Read the statement once for definitions, then identify the cause, result, and any formula or term that changes the conclusion.\n\n"
                    "Check question: What is the main term or relationship you need to remember from this text?"
                ),
                first_line=next((line.strip() for line in normalized.splitlines() if line.strip()), normalized[:120]),
            )
            if teaching_prefix and not fallback_reply.startswith(teaching_prefix):
                fallback_reply = f"{teaching_prefix}\n\n{fallback_reply}"
            return TutorReply(
                operation=MODE_CONCEPT_EXPLAIN,
                input_text=normalized,
                reply=fallback_reply,
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
        key = (
            base_url,
            model,
            self._api_key_cache_fingerprint(api_key),
            self._config.llm_temperature,
            self._config.llm_max_tokens,
        )
        llm = self._llm_cache.get(key)
        if llm is None:
            lock = self._llm_locks.setdefault(key, asyncio.Lock())
            async with lock:
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
            llm = self._llm_cache.get(key)
        if llm is None:
            raise SdkError("failed to initialize summary model")
        set_call_type("summary")
        ainvoke = getattr(llm, "ainvoke", None)
        if callable(ainvoke):
            response = await ainvoke(messages)
        else:
            response = await asyncio.to_thread(llm.invoke, messages)
        return str(getattr(response, "content", "") or response)

    @staticmethod
    def _api_key_cache_fingerprint(api_key: str) -> tuple[str, str]:
        if not api_key:
            return ("empty", "")
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        return ("sha256", digest)
