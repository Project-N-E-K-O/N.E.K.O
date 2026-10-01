from starlette.requests import HTTPConnection

from app import monitor_auth


def _conn(headers=(), query=b""):
    return HTTPConnection({
        "type": "http",
        "path": "/",
        "query_string": query,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
    })


def test_monitor_token_is_optional_by_default(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "")
    assert monitor_auth.monitor_auth_enabled() is False
    assert monitor_auth.verify_monitor_token(None)


def test_verify_monitor_token_rejects_wrong_and_missing(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    assert monitor_auth.verify_monitor_token("secret")
    assert not monitor_auth.verify_monitor_token("wrong")
    assert not monitor_auth.verify_monitor_token("")
    assert not monitor_auth.verify_monitor_token(None)


def test_bearer_header_takes_precedence_over_other_transports():
    conn = _conn([("Authorization", "Bearer secret"), ("X-Monitor-Token", "wrong")], b"token=wrong")
    assert monitor_auth.extract_monitor_token(conn) == ("secret", "header")


def test_x_monitor_token_then_query_fallbacks():
    assert monitor_auth.extract_monitor_token(_conn([("x-monitor-token", "h")], b"token=q")) == ("h", "header")
    assert monitor_auth.extract_monitor_token(_conn(query=b"token=q")) == ("q", "query")
    assert monitor_auth.extract_monitor_token(_conn([("Authorization", "Basic abc")])) == (None, None)


def test_viewer_session_is_signed_expiring_and_not_the_token(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    session = monitor_auth.issue_viewer_session(now=1_000)
    assert "secret" not in session
    assert monitor_auth.verify_viewer_session(session, now=1_001)
    expires_at = 1_000 + monitor_auth.VIEWER_SESSION_TTL_SECONDS
    assert not monitor_auth.verify_viewer_session(session, now=expires_at)
    assert not monitor_auth.verify_viewer_session(session[:-1] + "0", now=1_001)
    assert not monitor_auth.verify_viewer_session(f"{expires_at + 99}.{session.split('.', 1)[1]}", now=1_001)
    assert not monitor_auth.verify_viewer_session("secret", now=1_001)
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "rotated")
    assert not monitor_auth.verify_viewer_session(session, now=1_001)


def test_viewer_session_cookie_name_is_scoped_by_port():
    assert monitor_auth.VIEWER_SESSION_COOKIE == f"neko_monitor_session_{monitor_auth.MONITOR_SERVER_PORT}"


def test_token_scopes(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    monkeypatch.setattr(monitor_auth, "MONITOR_VIEWER_TOKEN", "viewer")
    assert monitor_auth.monitor_token_scope("secret") == "full"
    assert monitor_auth.monitor_token_scope("viewer") == "viewer"
    assert monitor_auth.monitor_token_scope("wrong") is None
    assert not monitor_auth.verify_monitor_token("viewer")
    monkeypatch.setattr(monitor_auth, "MONITOR_VIEWER_TOKEN", "")
    assert monitor_auth.monitor_token_scope("") is None
