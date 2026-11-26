"""内置主题的可读性门槛（WCAG 对比度）。

主题不会报错、不会掉帧，配色没配好的唯一表现是「用户默默换回默认」。
`theme_studio/contrast.py` 定义了 17 组真实配对（文字压在它真正所处的底色上），
这里把「102 套 × 亮/暗 × 17 组」变成每次改动都要过的回归项。

发现不达标时，先看是哪一对：多数情况是文字色没跟底色一起调，
改 `theme_studio/presets/<id>.json` 的文字键即可（可参考 `docs/theme_studio_builtin.md` §88）。
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from theme_studio import contrast

PRESETS = pathlib.Path(__file__).resolve().parents[2] / "theme_studio" / "presets"


def test_presets_dir_exists():
    assert PRESETS.is_dir(), f"找不到预设目录：{PRESETS}"
    assert len(list(PRESETS.glob("*.json"))) >= 100


def test_all_presets_meet_contrast_floors():
    """每一套主题、亮/暗两套、17 组配对都要达标。"""
    themes, fails, pair_checks = contrast.audit_dir(str(PRESETS))
    assert pair_checks > 3000, f"检查对数偏少（{pair_checks}），预设可能没读全"
    if fails:
        details = "\n".join(
            f"  {theme_id} [{mode}] {row['pair']}: {row['ratio']:.2f} < {row['floor']:.1f} ({row['fg']} / {row['bg']})"
            for theme_id, mode, row in sorted(fails, key=lambda it: it[2]["ratio"])[:20]
        )
        pytest.fail(f"{len(fails)} 处不达标 / 共 {pair_checks} 对：\n{details}")


def test_audit_can_actually_fail():
    """门槛必须有牙：把一套主题的链接色改成接近底色，审计必须报出来。"""
    file_path = PRESETS / "arctic-glass-material.json"
    doc = json.loads(file_path.read_text(encoding="utf-8-sig"))
    broken = copy.deepcopy(doc)
    broken.setdefault("vars", {})["--nts-link"] = "#eef2f4"

    fails, pair_checks = contrast.failing([("broken", broken)])
    assert pair_checks > 0
    link_fails = [row for _, _, row in fails if row["pair"] == "链接"]
    assert link_fails, "链接色贴近底色却没被审计发现"
    assert link_fails[0]["ratio"] < 1.5

    # 原文件必须仍然是达标的（别把「能报错」变成「本来就坏」）
    ok_fails, _ = contrast.failing([("original", doc)])
    assert not [row for _, _, row in ok_fails if row["pair"] == "链接"]


def test_contrast_extremes():
    white, black = (255, 255, 255, 1.0), (0, 0, 0, 1.0)
    assert contrast.contrast(black, white) == pytest.approx(21.0, abs=0.02)
    assert contrast.contrast(white, white) == pytest.approx(1.0, abs=1e-6)


def test_parse_color_formats():
    assert contrast.parse_color("#4089B3") == (64, 137, 179, 1.0)
    assert contrast.parse_color("#abc") == (170, 187, 204, 1.0)
    assert contrast.parse_color("rgba(64, 137, 179, 0.5)") == (64, 137, 179, 0.5)
    assert contrast.parse_color("none") is None
    assert contrast.parse_color("不是颜色") is None