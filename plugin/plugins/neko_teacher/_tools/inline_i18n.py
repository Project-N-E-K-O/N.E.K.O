#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pack-time helper: inline i18n.json content into index.html."""
import json
import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(r"D:\N.E.K.O自强之路\2026.05.28\N.E.K.O\plugin\plugins\neko_teacher")
HTML = PLUGIN_DIR / "static" / "index.html"
I18N = PLUGIN_DIR / "static" / "i18n.json"

def main():
    html = HTML.read_text(encoding="utf-8")
    i18n = I18N.read_text(encoding="utf-8")
    pattern = re.compile(
        r'<script id="i18n-inline" type="application/json">\s*__I18N_JSON__\s*</script>',
        re.MULTILINE,
    )
    if not pattern.search(html):
        print("[FAIL] __I18N_JSON__ placeholder not found in index.html", file=sys.stderr)
        sys.exit(1)
    new_html = pattern.sub(
        '<script id="i18n-inline" type="application/json">' + i18n + "</script>",
        html,
        count=1,
    )
    HTML.write_text(new_html, encoding="utf-8")
    print(f"[OK] inlined i18n into {HTML} (size={len(new_html)} bytes)")

if __name__ == "__main__":
    main()
