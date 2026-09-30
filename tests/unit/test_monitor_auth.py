from app import monitor_auth


def test_monitor_token_is_optional_by_default(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "")
    assert monitor_auth.monitor_auth_enabled() is False
    assert monitor_auth.authenticate_monitor_request(headers={}, query_token=None)


def test_bearer_header_takes_precedence_over_other_transports(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    headers = {
        "Authorization": "Bearer secret",
        "X-Monitor-Token": "wrong",
    }
    assert monitor_auth.extract_monitor_token(headers=headers, query_token="wrong") == "secret"
    assert monitor_auth.authenticate_monitor_request(headers=headers, query_token="wrong")


def test_monitor_token_supports_browser_query_and_rejects_wrong_value(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    assert monitor_auth.authenticate_monitor_request(headers={}, query_token="secret")
    assert not monitor_auth.authenticate_monitor_request(headers={}, query_token="wrong")
    assert not monitor_auth.authenticate_monitor_request(headers={}, query_token=None)


def test_header_extraction_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    assert monitor_auth.extract_monitor_token(
        headers={"authorization": "Bearer secret"}, query_token=None
    ) == "secret"
    assert monitor_auth.extract_monitor_token(
        headers={"x-monitor-token": "secret"}, query_token=None
    ) == "secret"


def test_cookie_token_supports_follow_up_viewer_requests(monkeypatch):
    monkeypatch.setattr(monitor_auth, "MONITOR_TOKEN", "secret")
    assert monitor_auth.authenticate_monitor_request(
        headers={}, query_token=None, cookie_token="secret"
    )
