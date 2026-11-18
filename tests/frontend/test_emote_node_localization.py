"""Localization reconciliation for the emote maker.

The failure mode this exists for: labels are referenced through variables
(例如 `t(stateLabel(key))`), so scripts/check_i18n.py cannot see them —
a missing key puts a raw dotted path on screen while CI stays green. So the key set is
enumerated from the sources here and compared against all eight locale files.
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
    """对账全部 emoteMaker 键：JS 字面量 + HTML 的 data-i18n* + 动态拼接的状态名。

    守卫原来只扫节点编辑器两个模块，于是 emote_maker.js 与页面里新加的键可以静默漏掉
    —— 有一批键连中文兜底都没有，界面会直接露出裸键名。这里把范围扩到所有来源。
    """
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
    """扫描器看不见动态键，所以这里按字面量枚举出来逐个查。"""
    keys = _referenced_keys()
    assert keys, '应当能枚举出 emote 引用的键'
    for lang in LOCALES:
        bundle = json.loads((ROOT / 'static' / 'locales' / f'{lang}.json').read_text(encoding='utf-8'))
        emote = bundle.get('emoteMaker', {})
        missing = sorted(k for k in keys if not str(emote.get(k, '')).strip())
        assert not missing, f'{lang} 缺 {len(missing)} 个 emoteMaker 键: {missing[:8]}'


def test_locale_files_stay_line_aligned() -> None:
    """check_i18n_sync.py 要求 8 个文件逐行对齐；这里提前拦住。"""
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
    """页面上任何一处文案都该走过 t()；漏一个键就是一串点号路径印在脸上。

    静态对账只能覆盖"能被正则看见的引用"。这条是真的把页面打开、把编辑区里的文字
    逐个扫一遍——两边各漏各的，所以都要有。
    """
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
