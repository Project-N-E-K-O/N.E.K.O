"""P31 deep-topic readiness/units contract smoke (UPSTREAM_SYNC_2026-06 Phase 3, #7=O).

Guards the **semantic-contract** half of the background topic-hook package
(``main_logic.topic``) that the testbench reuses via
``tests/testbench/pipeline/topic_sim.py``: the readiness gate, topic-unit
tokenization, and language/meaningfulness predicates. Imported directly (verified
side-effect-free, unlike ``main_logic.cross_server``). The runtime-mechanism half
— online material enrichment (aiohttp), delivery callback (TTS/WS), and the LLM
candidate call — is OOS per Phase 3.0.

Checks (any failure -> exit 1):

- C1  imports reachable (production helpers + adapter).
- C2  topic_units: CJK singles + bigrams emitted; stop chars dropped from both
      singles and bigrams.
- C3  is_meaningful_turn: filler words / <3-signal-char turns don't count; real
      content does.
- C4  label_key_for_lang: zh-TW / es / pt resolve natively; '' -> zh; unknown ->
      en. is_zh_lang true only for zh*.
- C5  readiness gate: N meaningful user turns -> ready (100%); filler turns don't
      count toward the threshold.

Usage:

    .venv/Scripts/python.exe tests/testbench/smoke/p31_topic_readiness_smoke.py

Exits 0 on all-clean, 1 on any violation.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

if isinstance(sys.stdout, io.TextIOWrapper):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_SUCCESS_BANNER = "P31 TOPIC READINESS SMOKE OK"


def check_c1_import() -> tuple[bool, str]:
    try:
        from main_logic.topic.common import topic_units  # noqa: F401
        from main_logic.topic.signals import (  # noqa: F401
            TopicSignalStore,
            _is_meaningful_turn,
            _label_key_for_lang,
        )
        from tests.testbench.pipeline import topic_sim  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"C1 FAIL: import error: {exc!r}"
    return True, "C1 OK: production topic helpers + adapter import"


def check_c2_topic_units() -> tuple[bool, str]:
    from tests.testbench.pipeline import topic_sim

    units = topic_sim.topic_units("今天天气")
    # singles + CJK bigrams (none of these are stop chars).
    if "天气" not in units or "今天" not in units:
        return False, f"C2 FAIL: CJK bigrams missing: {sorted(units)}"
    if "气" not in units:
        return False, f"C2 FAIL: CJK single missing: {sorted(units)}"

    # '我' / '你' are stop chars -> dropped from singles AND bigrams.
    units2 = topic_sim.topic_units("我喜欢你")
    if any("我" in u or "你" in u for u in units2):
        return False, f"C2 FAIL: stop char leaked into units: {sorted(units2)}"
    if "喜欢" not in units2:
        return False, f"C2 FAIL: real bigram dropped after stop filter: {sorted(units2)}"
    return True, "C2 OK: CJK singles+bigrams emitted, stop chars filtered"


def check_c3_meaningful() -> tuple[bool, str]:
    from tests.testbench.pipeline import topic_sim

    not_meaningful = ["嗯", "你好", "哈哈", "  ", "ab"]
    for t in not_meaningful:
        if topic_sim.is_meaningful_turn(t):
            return False, f"C3 FAIL: {t!r} wrongly counted as meaningful"
    meaningful = ["今天聊聊量子计算", "abc", "纳米机器人"]
    for t in meaningful:
        if not topic_sim.is_meaningful_turn(t):
            return False, f"C3 FAIL: {t!r} should be meaningful"
    return True, "C3 OK: filler/short turns don't count, real content does"


def check_c4_lang() -> tuple[bool, str]:
    from tests.testbench.pipeline import topic_sim

    cases = {
        "": "zh",
        "zh": "zh",
        "zh-TW": "zh-TW",
        "zh-Hant": "zh-TW",
        "es": "es",
        "pt": "pt",
        "pt-BR": "pt",
        "fr": "en",
    }
    for lang, expected in cases.items():
        got = topic_sim.label_key_for_lang(lang)
        if got != expected:
            return False, f"C4 FAIL: label_key_for_lang({lang!r}) = {got!r}, expected {expected!r}"
    if not topic_sim.is_zh_lang("zh-CN") or topic_sim.is_zh_lang("en"):
        return False, "C4 FAIL: is_zh_lang misclassified"
    return True, "C4 OK: lang label keys (zh/zh-TW/es/pt/en) + is_zh_lang"


def check_c5_readiness() -> tuple[bool, str]:
    from tests.testbench.pipeline import topic_sim

    # 3 meaningful user turns, threshold 3 -> ready, 100%.
    ready, pct = topic_sim.readiness_from_user_texts(
        ["今天聊聊量子计算", "量子纠缠很神奇", "还想了解量子隧穿"],
        min_user_turns_for_topic=3,
    )
    if not ready or pct != 100:
        return False, f"C5 FAIL: 3 meaningful turns -> ready={ready}, pct={pct} (expected True/100)"

    # 2 meaningful + 2 filler, threshold 3 -> not ready (filler doesn't count).
    ready2, pct2 = topic_sim.readiness_from_user_texts(
        ["今天聊聊量子计算", "嗯", "量子纠缠很神奇", "哈哈"],
        min_user_turns_for_topic=3,
    )
    if ready2:
        return False, f"C5 FAIL: filler turns counted toward readiness (pct={pct2})"
    return True, "C5 OK: readiness gate counts only meaningful user turns"


_CHECKS = [
    check_c1_import,
    check_c2_topic_units,
    check_c3_meaningful,
    check_c4_lang,
    check_c5_readiness,
]


def main() -> int:
    print("=" * 66)
    print(" P31 Deep-Topic Readiness/Units Contract Smoke (Phase 3 #7 = O)")
    print("=" * 66)
    print(" adapter:  tests/testbench/pipeline/topic_sim.py")
    print(" upstream: main_logic/topic/{common,signals}.py (imported directly;")
    print("           materials/delivery/pipeline LLM+aiohttp OOS)")
    print("")

    failures: list[str] = []
    for check in _CHECKS:
        try:
            ok, msg = check()
        except Exception as exc:  # noqa: BLE001
            ok, msg = False, f"{check.__name__} FAIL: raised {type(exc).__name__}: {exc}"
        print(msg)
        if not ok:
            failures.append(msg)

    print("")
    print("=" * 66)
    if failures:
        print(f" [FAIL] {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f.splitlines()[0]}")
        return 1
    print(f" [PASS] all {len(_CHECKS)} checks clean.")
    print(f" {_SUCCESS_BANNER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
