from __future__ import annotations
from .tutor_llm_agent_common import (
    Any,
    Awaitable,
    Callable,
    re,
    STUDY_JSON_CORRECTION_USER_TEMPLATE,
    SdkError,
    robust_json_loads,
    _JSON_CORRECTION_MAX_ATTEMPTS,
    _JSON_CORRECTION_BAD_OUTPUT_MAX_TOKENS,
    _JSON_CORRECTION_ERROR_MAX_TOKENS,
    _strip_code_fences,
    _bounded_prompt_text,
)


class _JSONCorrector:
    def __init__(self, *, logger: Any) -> None:
        self._logger = logger

    def parse_json_object(self, raw_text: str) -> dict[str, Any]:
        text = _strip_code_fences(str(raw_text or ""))
        try:
            parsed = robust_json_loads(text) if callable(robust_json_loads) else None
            if parsed is None:
                import json

                parsed = json.loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise SdkError("llm result is not valid json object")
            try:
                parsed = (
                    robust_json_loads(match.group(0))
                    if callable(robust_json_loads)
                    else None
                )
                if parsed is None:
                    import json

                    parsed = json.loads(match.group(0))
            except Exception as exc:
                raise SdkError(f"llm result is not valid json object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SdkError("llm result must be a json object")
        return dict(parsed)

    async def invoke_with_correction(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        call_model: Callable[..., Awaitable[str]],
    ) -> str:
        raw_text = await call_model(messages, operation=operation)
        last_error: Exception | None = None
        for attempt in range(_JSON_CORRECTION_MAX_ATTEMPTS + 1):
            try:
                self.parse_json_object(raw_text)
                return raw_text
            except SdkError as exc:
                last_error = exc
                if attempt >= _JSON_CORRECTION_MAX_ATTEMPTS:
                    break
            correction_messages = self._build_json_correction_messages(
                operation=operation,
                messages=messages,
                bad_output=raw_text,
                parse_error=last_error,
                attempt=attempt + 1,
                max_attempts=_JSON_CORRECTION_MAX_ATTEMPTS,
            )
            raw_text = await call_model(correction_messages, operation=operation)
        raise SdkError(
            f"llm result is not valid json object after correction: {last_error}"
        )

    def _build_json_correction_messages(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        bad_output: object,
        parse_error: object,
        attempt: int,
        max_attempts: int,
    ) -> list[dict[str, str]]:
        correction_messages = list(messages)
        correction_messages.append(
            {
                "role": "assistant",
                "content": _bounded_prompt_text(
                    bad_output, max_tokens=_JSON_CORRECTION_BAD_OUTPUT_MAX_TOKENS
                ),
            }
        )
        correction_messages.append(
            {
                "role": "user",
                "content": (
                    STUDY_JSON_CORRECTION_USER_TEMPLATE.format(
                        attempt=attempt,
                        max_attempts=max_attempts,
                        operation=operation,
                        parse_error=_bounded_prompt_text(
                            parse_error, max_tokens=_JSON_CORRECTION_ERROR_MAX_TOKENS
                        ),
                    )
                ),
            }
        )
        return correction_messages
