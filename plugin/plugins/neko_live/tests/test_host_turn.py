from plugin.plugins.neko_live.core.host_turn import HostTurnSignal, HostTurnSignalStore


def test_host_turn_store_defaults_to_unknown_and_unavailable() -> None:
    signal = HostTurnSignalStore(now=lambda: 42.0).current()

    assert signal == HostTurnSignal(
        state="unknown",
        confidence=0.0,
        reliability="unavailable",
        observed_at=42.0,
        source="fallback",
    )


def test_host_turn_store_keeps_latest_normalized_host_runtime_signal() -> None:
    store = HostTurnSignalStore(now=lambda: 42.0)
    signal = HostTurnSignal(
        state="speaking",
        confidence=0.9,
        reliability="reliable",
        observed_at=41.0,
        source="host_runtime",
    )

    store.update(signal)

    assert store.current() == signal


def test_host_turn_store_reset_returns_to_unknown() -> None:
    store = HostTurnSignalStore(now=lambda: 42.0)
    store.update(
        HostTurnSignal(
            state="yielded",
            confidence=1.0,
            reliability="reliable",
            observed_at=41.0,
            source="host_runtime",
        )
    )

    store.reset()

    assert store.current().state == "unknown"
    assert store.current().source == "fallback"


def test_host_turn_store_expires_yielded_signal_to_unknown() -> None:
    now = [42.0]
    store = HostTurnSignalStore(now=lambda: now[0], yielded_ttl_seconds=5.0)
    signal = HostTurnSignal(
        state="yielded",
        confidence=1.0,
        reliability="reliable",
        observed_at=42.0,
        source="host_runtime",
    )
    store.update(signal)

    now[0] = 47.0
    assert store.current() == signal

    now[0] = 47.01
    expired = store.current()
    assert expired.state == "unknown"
    assert expired.reliability == "unavailable"
    assert expired.source == "fallback"


def test_host_turn_store_rejects_future_yielded_signal() -> None:
    store = HostTurnSignalStore(now=lambda: 42.0)
    store.update(
        HostTurnSignal(
            state="yielded",
            confidence=1.0,
            reliability="reliable",
            observed_at=43.0,
            source="host_runtime",
        )
    )

    assert store.current().state == "unknown"
