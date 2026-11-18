"""Ground manual false-positive measurement in the exact rejected draft."""

import json

import pytest

from scripts.analyze_numeric_v2_rejections import rejection_rows, sample_rows, summarize


def test_each_rejection_keeps_its_actual_body_quote_rule_and_resolution(tmp_path):
    path = tmp_path / "trace.jsonl"
    rows = [
        {"event": "actor.candidate", "data": {"performance": {"performance": "第一稿"}}},
        {"event": "contract.checked", "sequence": 2, "data": {
            "confirmation_verdicts": [{"rule": "玩家行动", "candidate_ref": "c0", "quote": "第一稿", "verdict": "clear"}]}},
        {"event": "actor.candidate", "data": {"performance": {"performance": "第二稿"}}},
        {"event": "contract.checked", "sequence": 4, "data": {
            "findings": [{"rule": "玩家行动", "candidate": {"id": "c0"}, "quote": "第二稿"}]}}
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    cleared, blocked = list(rejection_rows([path]))
    assert cleared["candidate"]["performance"] == cleared["quote"] == "第一稿"
    assert blocked["candidate"]["performance"] == blocked["quote"] == "第二稿"
    assert (cleared["decision"], blocked["decision"]) == ("cleared", "blocked")
    assert cleared["label"] is blocked["label"] is None


def test_unknown_labels_do_not_inflate_a_rule_false_positive_denominator():
    rows = [{"id": str(i), "rule": "rule", "label": label}
            for i, label in enumerate(("false_positive", "true_violation", "uncertain", None))]
    counts = summarize(rows)["by_rule"]["rule"]
    assert counts["false_positive_rate"] == .5 and counts["reviewed_decisive"] == 2
    rows[0]["label"] = "model_guessed"
    with pytest.raises(ValueError, match="invalid_manual_label"):
        summarize(rows)


def test_stratified_sample_is_repeatable_and_keeps_less_frequent_rules():
    rows = [{"id": str(i), "rule": "frequent"} for i in range(50)] + [{"id": "rare", "rule": "rare"}]
    picked = sample_rows(rows, size=30, seed=1)
    assert picked == sample_rows(rows, size=30, seed=1)
    assert len({row["id"] for row in picked}) == 30
    assert any(row["id"] == "rare" for row in picked)
