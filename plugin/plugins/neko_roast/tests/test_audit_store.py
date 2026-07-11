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
