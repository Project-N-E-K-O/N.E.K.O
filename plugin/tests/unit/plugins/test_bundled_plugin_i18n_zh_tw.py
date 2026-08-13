"""Every bundled plugin that ships a Chinese i18n bundle ships a Traditional one.

The SDK's ``locale_candidates`` appends ``zh-CN`` for any ``zh*`` locale, so a
missing ``zh-TW.json`` fails *silently*: a Traditional reader sees Simplified UI
rather than an error or an English fallback. Nothing else in CI notices.

Discovered from the tree rather than listed, so a plugin added later is covered
without editing this file (issue #2500).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# .../plugin/tests/unit/plugins/<this file>  ->  parents[3] == .../plugin
_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "plugins"

# Simplified-only forms; each is the Simplified counterpart of a character the
# Traditional bundles actually use. Characters identical in both orthographies
# are deliberately absent — 回 返 插 件 里 制 端 都 是 —— they would flag
# perfectly correct Traditional strings.
_SIMPLIFIED_ONLY = (
    "运态设备录网页节点开关闭时间处发讯问题终调试复载键号选项确认执业务"
    "动数据类别语转连应让说请谢记现场长学员单双从众会统图书实验证"
)

# Language endonyms are written in their own script by convention: a Traditional
# UI still labels the Simplified option 「简体中文」. Skipped by value, not by
# key, so the exemption cannot quietly widen to a neighbouring string.
_ENDONYMS = frozenset({"简体中文", "繁體中文", "日本語", "English", "한국어", "Русский"})


def _chinese_bundle_dirs() -> list[Path]:
    """Directories holding a zh-CN.json, i.e. plugins with a Chinese bundle."""
    found = sorted(
        {
            path.parent
            for path in _PLUGINS_DIR.glob("*/**/zh-CN.json")
            if path.is_file()
        }
    )
    assert found, f"没在 {_PLUGINS_DIR} 下找到任何 zh-CN.json —— 发现逻辑坏了"
    return found


def _ids(dirs: list[Path]) -> list[str]:
    return [str(d.relative_to(_PLUGINS_DIR)).replace("\\", "/") for d in dirs]


_BUNDLE_DIRS = _chinese_bundle_dirs()


@pytest.mark.parametrize("bundle_dir", _BUNDLE_DIRS, ids=_ids(_BUNDLE_DIRS))
def test_traditional_bundle_exists(bundle_dir: Path):
    assert (bundle_dir / "zh-TW.json").is_file(), (
        f"{bundle_dir.name} 有 zh-CN.json 但缺 zh-TW.json —— "
        "繁中用户会静默拿到简体界面"
    )


@pytest.mark.parametrize("bundle_dir", _BUNDLE_DIRS, ids=_ids(_BUNDLE_DIRS))
def test_traditional_bundle_has_the_same_keys(bundle_dir: Path):
    """A partial bundle is worse than none: the missing keys fall back to
    Simplified one by one, so the panel comes out half-and-half."""
    tw_path = bundle_dir / "zh-TW.json"
    if not tw_path.is_file():
        pytest.skip("covered by test_traditional_bundle_exists")
    cn = json.loads((bundle_dir / "zh-CN.json").read_text(encoding="utf-8"))
    tw = json.loads(tw_path.read_text(encoding="utf-8"))
    assert set(cn) == set(tw), (
        f"{bundle_dir.name}: 只缺 {sorted(set(cn) - set(tw))}，"
        f"多出 {sorted(set(tw) - set(cn))}"
    )


@pytest.mark.parametrize("bundle_dir", _BUNDLE_DIRS, ids=_ids(_BUNDLE_DIRS))
def test_traditional_bundle_is_actually_converted(bundle_dir: Path):
    """Guards a bundle copied in to satisfy a checklist without converting."""
    tw_path = bundle_dir / "zh-TW.json"
    if not tw_path.is_file():
        pytest.skip("covered by test_traditional_bundle_exists")
    cn = json.loads((bundle_dir / "zh-CN.json").read_text(encoding="utf-8"))
    tw = json.loads(tw_path.read_text(encoding="utf-8"))

    identical = [k for k in cn if k in tw and cn[k] == tw[k]]
    # Some values are legitimately identical across scripts (IDs, brand names,
    # "Account ID"). Requiring *most* values to differ catches a wholesale copy
    # without flagging those.
    assert len(identical) < len(cn) / 2, (
        f"{bundle_dir.name}: {len(identical)}/{len(cn)} 条与 zh-CN 逐字相同，像是拷贝"
    )

    offenders = {
        key: value
        for key, value in tw.items()
        if str(value) not in _ENDONYMS
        and any(ch in _SIMPLIFIED_ONLY for ch in str(value))
    }
    assert not offenders, (
        f"{bundle_dir.name} 的 zh-TW 有 {len(offenders)} 条仍是简体字形，"
        f"例如：{dict(list(offenders.items())[:3])}"
    )
