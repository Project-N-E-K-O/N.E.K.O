import asyncio


def test_internal_http_client_is_reused_per_event_loop():
    from utils.internal_http_client import (
        aclose_internal_http_client_current_loop,
        get_internal_http_client,
    )

    async def get_two_clients():
        first = get_internal_http_client()
        second = get_internal_http_client()
        return first, second

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        client_a, client_a_again = loop_a.run_until_complete(get_two_clients())
        client_b, client_b_again = loop_b.run_until_complete(get_two_clients())

        assert client_a is client_a_again
        assert client_b is client_b_again
        assert client_a is not client_b

        loop_a.run_until_complete(aclose_internal_http_client_current_loop())
        loop_b.run_until_complete(aclose_internal_http_client_current_loop())
    finally:
        loop_a.close()
        loop_b.close()
