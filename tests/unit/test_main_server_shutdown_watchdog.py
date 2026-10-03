from __future__ import annotations


def test_standalone_shutdown_watchdog_is_singleflight(monkeypatch) -> None:
    from app.main_server import __main__ as main_entry

    started: list[dict[str, object]] = []

    class FakeThread:
        def __init__(self, **kwargs) -> None:
            started.append(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(main_entry, "_standalone_watchdog_started", False)
    monkeypatch.setattr(main_entry.threading, "Thread", FakeThread)

    main_entry._arm_standalone_shutdown_watchdog()
    main_entry._arm_standalone_shutdown_watchdog()

    assert len(started) == 1
    assert started[0]["name"] == "main-server-shutdown-watchdog"
    assert started[0]["daemon"] is True
