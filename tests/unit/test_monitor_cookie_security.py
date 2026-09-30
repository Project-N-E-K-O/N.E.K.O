from pathlib import Path


def test_monitor_token_cookie_uses_request_scheme_for_secure_flag():
    source = (Path(__file__).parents[2] / "app" / "monitor.py").read_text(encoding="utf-8")
    cookie_start = source.index('response.set_cookie(\n            "monitor_token"')
    cookie_block = source[cookie_start:source.index("        )", cookie_start)]
    assert 'secure=request.url.scheme.lower() == "https"' in cookie_block
