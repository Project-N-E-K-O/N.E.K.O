"""Keep both official Nginx variants wired to the token's actual backend.

Runtime HTTP/HTTPS proxy-header behavior is exercised in mutation-auth tests;
these deployment contracts catch an omitted or overbroad bootstrap location.
"""
from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.plugin_unit


@pytest.mark.parametrize("variant", ["HTTP-only", "HTTP+HTTPS"])
def test_official_nginx_routes_token_to_plugin_and_preserves_public_origin(variant):
    entrypoint = Path(__file__).resolve().parents[4] / "docker" / "entrypoint.sh"
    source = entrypoint.read_text(encoding="utf-8")
    marker = f'echo "🌐 Generating {variant} configuration'
    branch = source.split(marker, 1)[1].split("\nEOF", 1)[0]
    locations = re.findall(r"location\s+=\s+/security/csrf-token\s*\{([^}]+)\}", branch)
    assert len(locations) == 1, "Token bootstrap needs an exact route in each Nginx variant"
    directives = locations[0]
    assert "proxy_pass http://127.0.0.1:48916;" in directives
    assert r"proxy_set_header Host \$http_host;" in directives
    assert r"proxy_set_header X-Forwarded-Proto \$scheme;" in directives
    assert r"proxy_set_header X-Forwarded-For \$remote_addr;" in directives
    # The backend supplies no-store. No location-level add_header: it would
    # disable inheritance of the HTTPS server's HSTS header.
    assert "add_header" not in directives
    # Lifecycle paths and /ui must still reach that same plugin upstream.
    assert re.search(r"location ~ \^/\([^\n]*plugins\?[^\n]*\) \{\s*"
                     r"proxy_pass http://127\.0\.0\.1:48916;", branch)


@pytest.mark.parametrize("variant", ["HTTP-only", "HTTP+HTTPS"])
def test_official_proxy_routes_preserve_chain_except_token_bootstrap(variant):
    entrypoint = Path(__file__).resolve().parents[4] / "docker" / "entrypoint.sh"
    source = entrypoint.read_text(encoding="utf-8")
    branch = source.split(f'echo "🌐 Generating {variant} configuration', 1)[1].split("\nEOF", 1)[0]
    locations = re.findall(r"location[^\n]*\{(.*?)\n    \}", branch, re.DOTALL)
    proxy_locations = [block for block in locations if "proxy_pass" in block]
    assert len(proxy_locations) == 6
    for block in proxy_locations:
        if "proxy_set_header X-Real-IP" in block:
            assert r"proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;" in block
        else:
            assert r"proxy_set_header X-Forwarded-For \$remote_addr;" in block
