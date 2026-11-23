"""Reconcile all emote-maker localization references against eight locales.

Dynamic expressions such as t(stateLabel(key)) are invisible to the static
scanner. Enumerating their keys here prevents untranslated dotted paths from
reaching the interface while static checks remain green.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW']


def _referenced_keys() -> set[str]:
    """Check literal, HTML and dynamically assembled emoteMaker keys across all editor sources."""
    keys: set[str] = set()
    for name in ('emote_maker.js', 'emote_node_graph.js',
                 'emote_state_stage.js',
                 'emote_pngtuber_pack.js', 'emote_pngtuber_layered.js', 'emote_sheet.js'):
        src = (ROOT / 'static' / 'js' / name).read_text(encoding='utf-8')
        keys |= {m.split('emoteMaker.', 1)[1] for m in re.findall(r"'(emoteMaker\.[A-Za-z0-9_.]+)'", src)}
        keys |= {m.split('emoteMaker.', 1)[1]
                 for m in re.findall(r'`(emoteMaker\.[A-Za-z0-9_.]+)\$\{', src)}
    # JS 兜底表里的键（形如 'emoteMaker.x': '…'）
    for name in ('emote_maker.js', 'emote_node_graph.js',
                 'emote_state_stage.js',
                 'emote_pngtuber_pack.js', 'emote_pngtuber_layered.js', 'emote_sheet.js'):
        src = (ROOT / 'static' / 'js' / name).read_text(encoding='utf-8')
        keys |= set(re.findall(r"'emoteMaker\.([A-Za-z0-9_]+)'\s*:", src))
    page_html = (ROOT / 'templates' / 'emote_maker.html').read_text(encoding='utf-8')
    keys |= set(re.findall(
        r'data-i18n(?:-placeholder|-title|-aria|-alt)?="emoteMaker\.([A-Za-z0-9_]+)"', page_html))
    pack_src = (ROOT / 'static' / 'js' / 'emote_pngtuber_pack.js').read_text(encoding='utf-8')
    # 与 stage 里的 stateLabel 同一条规则：去掉 _image、首字母大写
    keys |= {'packState' + k[:1].upper() + k[1:]
             for k in re.findall(r"key: '([a-z]+)_image'", pack_src)}
    # 全程只用裸名：混着带前缀和不带前缀两种形态，查找时 split 就会越界
    return {k for k in keys if k and k != 'packState' and '.' not in k}


def test_every_emote_key_exists_in_all_eight_locales() -> None:
    """Enumerate dynamic localization keys that the static scanner cannot discover."""
    keys = _referenced_keys()
    assert keys, '应当能枚举出 emote 引用的键'
    for lang in LOCALES:
        bundle = json.loads((ROOT / 'static' / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        emote = bundle.get('emoteMaker', {})
        missing = sorted(k for k in keys if not str(emote.get(k, '')).strip())
        assert not missing, f'{lang} 缺 {len(missing)} 个 emoteMaker 键: {missing[:8]}'


def test_locale_files_stay_line_aligned() -> None:
    """Require identical key ordering in all eight locale files before the sync gate runs."""
    counts = set()
    for lang in LOCALES:
        raw = (ROOT / 'static' / 'locales' / f'{lang}.json').read_text(encoding='utf-8')
        json.loads(raw)
        counts.add(len(raw.split('\n')))
    assert len(counts) == 1, f'行数不再一致: {counts}'




REVEAL = """() => {
    document.body.classList.remove('editor-empty');
    document.querySelector('.animation-dock').hidden = false;
    document.querySelector('.inspector-panel').hidden = false;
}"""


def test_no_raw_key_leaks_into_the_visible_page(page: Page, running_server: str) -> None:
    """Scan the rendered editor for raw localization keys as well as checking static references."""
    page.goto(f"{running_server}/emote_maker", wait_until="networkidle")
    page.wait_for_timeout(400)
    page.evaluate(REVEAL)
    leaks = page.evaluate(
        """() => {
            const seen = [];
            ['.maker-grid', '.inspector-panel', '.animation-dock'].forEach((selector) => {
                document.querySelectorAll(selector).forEach((host) => {
                    host.querySelectorAll('*').forEach((el) => {
                        if (el.children.length) return;
                        const text = (el.textContent || '').trim();
                        if (text.indexOf('emoteMaker.') >= 0) {
                            seen.push((el.className || el.tagName) + ' :: ' + text.slice(0, 40));
                        }
                    });
                });
            });
            return seen;
        }"""
    )
    assert not leaks, leaks[:6]
