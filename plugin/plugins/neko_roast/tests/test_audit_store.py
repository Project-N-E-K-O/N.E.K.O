"""AuditStore public projection and privacy boundary tests."""

from __future__ import annotations

import json

from plugin.plugins.neko_roast.stores.audit_store import AuditStore


class _SecretLike:
    def __str__(self) -> str:
        return "token=must-not-leak"

    def __bool__(self) -> bool:
        return True


def test_audit_store_preserves_normal_public_event_fields() -> None:
    store = AuditStore(limit=2)

    store.record(
        "live_listener_started",
        "danmaku listener started",
        level="warning",
        detail={"room_id": 123, "files": ["douyin_credential.enc"], "ok": True},
    )

    event = store.recent(1)[0]
    assert event["op"] == "live_listener_started"
    assert event["level"] == "warning"
    assert event["message"] == "danmaku listener started"
    assert event["detail"] == {
        "room_id": 123,
        "files": ["douyin_credential.enc"],
        "ok": True,
    }


def test_audit_store_redacts_credentials_and_never_stringifies_objects() -> None:
    store = AuditStore()
    secret = _SecretLike()

    store.record(
        secret,  # type: ignore[arg-type]
        secret,  # type: ignore[arg-type]
        level=secret,  # type: ignore[arg-type]
        detail={
            "cookie": "Cookie: ttwid=must-not-leak; odin_tt=also-hidden",
            "auth": "Authorization: Bearer bearer-secret",
            "nested": {
                "signature": "signature=signature-secret",
                "object": secret,
                "bytes": b"binary-secret",
            },
            secret: "bad-key",  # type: ignore[dict-item]
        },
    )

    event = store.recent(1)[0]
    rendered = json.dumps(event, ensure_ascii=False, sort_keys=True)
    assert event["op"] == "unknown"
    assert event["level"] == "info"
    assert event["message"] == ""
    assert "[redacted]" in rendered
    assert "must-not-leak" not in rendered
    assert "also-hidden" not in rendered
    assert "bearer-secret" not in rendered
    assert "signature-secret" not in rendered
    assert "binary-secret" not in rendered
    assert "bad-key" not in rendered
    assert event["detail"]["nested"]["object"] == ""
    assert event["detail"]["nested"]["bytes"] == ""


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
