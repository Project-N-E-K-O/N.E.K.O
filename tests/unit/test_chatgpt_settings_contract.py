"""ChatGPT account panel: locale keys the page uses and where the panel sits."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ('en', 'zh-CN', 'zh-TW', 'ja', 'ko', 'es', 'pt', 'ru')


def test_every_chatgpt_key_the_page_uses_exists_in_all_locales():
    script = (ROOT / 'static/js/chatgpt_settings.js').read_text(encoding='utf-8')
    template = (ROOT / 'templates/api_key_settings.html').read_text(encoding='utf-8')
    settings = (ROOT / 'static/js/api_key_settings.js').read_text(encoding='utf-8')
    used = set(re.findall(r"\bt\('([a-zA-Z]+)'", script))
    used |= set(re.findall(r'api\.chatgpt\.([a-zA-Z]+)', template + settings))
    assert {'connected', 'signedOut', 'pending', 'instructions', 'title'} <= used
    for locale in LOCALES:
        messages = json.loads((ROOT / 'static/locales' / f'{locale}.json').read_text(encoding='utf-8'))['api']['chatgpt']
        missing = sorted(key for key in used if not messages.get(key))
        assert not missing, f'{locale} lacks {missing}'
        # The panel points at the provider by the name its dropdown shows.
        assert 'ChatGPT (subscription)' in messages['instructions'], locale


def test_panel_sits_between_key_book_and_custom_api_and_starts_collapsed():
    template = (ROOT / 'templates/api_key_settings.html').read_text(encoding='utf-8')
    assert template.index('id="key-book-section"') < template.index('id="chatgpt-settings"') < template.index('id="custom-api-section"')
    assert 'id="chatgpt-options" hidden' in template
    assert 'id="chatgpt-toggle-btn" aria-expanded="false"' in template
    # The one-click subscription switch was taken out of this panel.
    assert 'chatgpt-enabled' not in template and 'chatgpt-confirm-modal' not in template
