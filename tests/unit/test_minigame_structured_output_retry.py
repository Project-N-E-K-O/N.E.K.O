import pytest

from main_logic.mini_game_sdk import (
    StructuredOutputContentError,
    run_isolated_structured_output,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_retry_uses_a_new_isolation_id_and_recovers():
    attempts = []

    async def attempt_factory(attempt, isolation_id):
        attempts.append((attempt, isolation_id))
        if attempt == 1:
            return {"stance": "unknown"}
        return {"stance": "ready"}

    def validator(value):
        if value.get("stance") != "ready":
            return value, [{"field": "stance", "reason": "unsupported_value"}]
        return value, []

    result = await run_isolated_structured_output(attempt_factory, validator)

    assert result.value == {"stance": "ready"}
    assert result.attempts == 2
    assert result.recovered is True
    assert [attempt for attempt, _ in attempts] == [1, 2]
    assert attempts[0][1] != attempts[1][1]
    assert result.failures[0].issues == ({"field": "stance", "reason": "unsupported_value"},)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_retry_does_not_retry_provider_failures():
    attempts = 0

    async def attempt_factory(_attempt, _isolation_id):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        await run_isolated_structured_output(attempt_factory, lambda value: (value, []))

    assert attempts == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structured_output_retry_is_hard_limited_to_one_retry():
    async def attempt_factory(_attempt, _isolation_id):
        raise StructuredOutputContentError("invalid_json")

    with pytest.raises(ValueError, match="between 0 and 1"):
        await run_isolated_structured_output(
            attempt_factory,
            lambda value: (value, []),
            content_retries=2,
        )
