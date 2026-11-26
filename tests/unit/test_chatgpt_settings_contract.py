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
    assert {'connected', 'signedOut', 'pending', 'instructions', 'subscriptionTurnedOff'} <= used
    for locale in LOCALES:
        messages = json.loads((ROOT / 'static/locales' / f'{locale}.json').read_text(encoding='utf-8'))['api']['chatgpt']
        missing = sorted(key for key in used if not messages.get(key))
        assert not missing, f'{locale} lacks {missing}'
        # ChatGPT is chosen on its card in the subscriptions panel, not per slot.
        assert 'ChatGPT (subscription)' not in messages['instructions'], locale


def test_account_controls_sit_on_the_chatgpt_card_of_the_subscriptions_panel():
    template = (ROOT / 'templates/api_key_settings.html').read_text(encoding='utf-8')
    assert (
        template.index('id="key-book-section"')
        < template.index('id="subscriptions-settings"')
        < template.index('id="chatgpt-settings"')
        < template.index('id="custom-api-section"')
    )
    assert 'id="subscriptions-options" hidden' in template
    assert 'id="subscriptions-toggle-btn" aria-expanded="false"' in template
    card = template[template.index('id="chatgpt-settings"'):template.index('id="custom-api-section"')]
    for control in ('chatgpt-account', 'chatgpt-login', 'chatgpt-add', 'chatgpt-logout', 'chatgpt-status'):
        assert f'id="{control}"' in card
    # The old one-click switch and its snapshot/restore UI stay gone.
    assert 'chatgpt-enabled' not in template and 'chatgpt-confirm-modal' not in template
