from plugin.plugins.neko_roast.stores.audit_store import AuditStore


def test_audit_store_redacts_text_and_structured_secrets() -> None:
    store = AuditStore()

    store.record(
        "credential_check",
        "Authorization: Bearer top-secret",
        detail={
            "token": "plain-secret",
            "nested": {"SESSDATA": "session-secret", "uid": "42"},
        },
    )

    event = store.recent()[0]
    assert "top-secret" not in event["message"]
    assert event["detail"]["token"] == "[redacted]"
    assert event["detail"]["nested"]["SESSDATA"] == "[redacted]"
    assert event["detail"]["nested"]["uid"] == "42"


def test_audit_store_normalizes_known_level_case() -> None:
    store = AuditStore()

    store.record("warning", "warning", level="WARNING")
    store.record("error", "error", level="Error")
    store.record("unknown", "unknown", level="critical")

    events = {event["op"]: event for event in store.recent()}
    assert events["warning"]["level"] == "warning"
    assert events["error"]["level"] == "error"
    assert events["unknown"]["level"] == "info"


def test_audit_store_redacts_complete_cookie_header() -> None:
    store = AuditStore()
    header = "request failed\nCookie: sid=secret; theme=blue\nstatus=500"

    store.record("request", header, detail={"header": header})

    event = store.recent()[0]
    for text in (event["message"], event["detail"]["header"]):
        assert "secret" not in text
        assert "theme" not in text
        assert "blue" not in text
        assert "status=500" in text
