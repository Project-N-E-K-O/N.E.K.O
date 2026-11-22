import hashlib
import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PURIFY_JS = PROJECT_ROOT / "static" / "libs" / "purify.js"
NOTICES_MD = PROJECT_ROOT / "static" / "libs" / "THIRD_PARTY_NOTICES.md"
DOMPURIFY_LICENSE = PROJECT_ROOT / "static" / "libs" / "licenses" / "DOMPURIFY.LICENSE"
PLUGIN_MANAGER_LOCK = PROJECT_ROOT / "frontend" / "plugin-manager" / "package-lock.json"
COOKIES_LOGIN_TEMPLATE = PROJECT_ROOT / "templates" / "cookies_login.html"


@pytest.mark.unit
def test_vendored_dompurify_matches_lockfile_version_and_provenance_notices():
    lock_data = json.loads(PLUGIN_MANAGER_LOCK.read_text(encoding="utf-8"))
    lock_entry = lock_data["packages"]["node_modules/dompurify"]
    expected_version = lock_entry["version"]

    purify_text = PURIFY_JS.read_text(encoding="utf-8")
    purify_bytes = PURIFY_JS.read_bytes()
    actual_sha256 = hashlib.sha256(purify_bytes).hexdigest()

    version_match = re.search(r"DOMPurify\.version\s*=\s*['\"]([^'\"]+)['\"]", purify_text)
    assert version_match is not None, "DOMPurify.version assignment missing from static/libs/purify.js"
    assert version_match.group(1) == expected_version
    assert f"DOMPurify {expected_version}" in purify_text.splitlines()[0]

    notices = NOTICES_MD.read_text(encoding="utf-8")
    assert "## dompurify" in notices
    dompurify_section = notices.split("## dompurify", 1)[1].split("\n## ", 1)[0]
    assert "`purify.js`" in dompurify_section
    assert f"`{expected_version}`" in dompurify_section
    assert actual_sha256 in dompurify_section

    assert DOMPURIFY_LICENSE.is_file()
    assert "Apache License" in DOMPURIFY_LICENSE.read_text(encoding="utf-8")


@pytest.mark.unit
def test_cookies_login_template_cache_busts_vendored_purify():
    template = COOKIES_LOGIN_TEMPLATE.read_text(encoding="utf-8")
    assert '<script src="/static/libs/purify.js?v={{ static_asset_version | default(\'0\') }}"></script>' in template
