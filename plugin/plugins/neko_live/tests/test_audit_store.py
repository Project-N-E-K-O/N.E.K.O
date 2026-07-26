"""AuditStore public projection and privacy boundary tests."""

from __future__ import annotations

import json

from plugin.plugins.neko_live.stores.audit_store import AuditStore


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


def test_audit_store_recent_returns_independent_nested_copies() -> None:
    store = AuditStore()
    store.record("op", "message", detail={"nested": {"value": "original"}})

    first = store.recent(1)
    first[0]["detail"]["nested"]["value"] = "mutated"

    assert store.recent(1)[0]["detail"]["nested"]["value"] == "original"


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


def test_audit_store_recursively_redacts_extended_sensitive_keys() -> None:
    store = AuditStore()

    store.record(
        "nested_credentials",
        "safe",
        detail={
            "items": [
                {
                    "client_secret": "client-secret",
                    "service_token": "service-token",
                    "password": "password-secret",
                    "safe": "visible",
                }
            ],
            "credentials": {"arbitrary": "must-not-leak"},
        },
    )

    detail = store.recent()[0]["detail"]
    assert detail["items"][0] == {
        "client_secret": "[redacted]",
        "service_token": "[redacted]",
        "password": "[redacted]",
        "safe": "visible",
    }
    assert detail["credentials"] == "[redacted]"


def test_audit_store_hides_viewer_derived_topic_content() -> None:
    store = AuditStore()

    store.record(
        "active_topic",
        "selected",
        detail={
            "topic_material": {
                "source": "live_thread",
                "privacy_classification": "viewer_derived",
                "title": "private viewer words",
                "key": "thread:private-viewer-words",
                "hook": "repeat private viewer words",
                "interest": "private viewer words",
                "keywords": ["private"],
                "evidence": ["private viewer evidence"],
                "shape": "light_stance",
            }
        },
    )

    topic = store.recent()[0]["detail"]["topic_material"]
    assert topic == {
        "source": "live_thread",
        "privacy_classification": "viewer_derived",
        "shape": "light_stance",
    }


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
