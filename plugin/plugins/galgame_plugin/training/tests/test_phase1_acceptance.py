from __future__ import annotations

from pathlib import Path

from plugin.plugins.galgame_plugin.training.classify.phase1_acceptance import (
    Phase1PredictionRecord,
    build_replay_ticks,
    label_to_stage,
    load_split_samples,
    summarize_predictions,
)
from plugin.plugins.galgame_plugin.core.vision.labels import vision_label_to_screen_type


def test_label_to_stage_maps_galgame_labels_to_runtime_stages() -> None:
    assert label_to_stage("dialogue") == "dialogue_stage"
    assert label_to_stage("choice_menu") == "menu_stage"
    assert label_to_stage("backlog") == "gallery_stage"
    assert label_to_stage("save_load") == "save_load_stage"
    assert label_to_stage("gallery") == "gallery_stage"
    assert label_to_stage("title_screen") == "title_stage"
    assert label_to_stage("config") == "config_stage"
    assert label_to_stage("loading") == "transition_stage"
    assert label_to_stage("unknown") == "default"


def test_label_to_stage_uses_shared_plugin_mapping() -> None:
    for label in ("dialogue", "choice_menu", "backlog", "save_load", "unknown"):
        assert label_to_stage(label) == vision_label_to_screen_type(label)


def test_load_split_samples_skips_malformed_jsonl_rows(tmp_path, caplog) -> None:
    image_path = tmp_path / "dialogue.png"
    image_path.write_text("fake", encoding="utf-8")
    (tmp_path / "train.jsonl").write_text(
        "\n".join(
            [
                "{not json",
                '{"image_path":"dialogue.png","label":"dialogue"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        samples = load_split_samples(tmp_path, ["train"])

    assert len(samples) == 1
    assert samples[0]["label"] == "dialogue"
    assert "invalid JSONL row" in caplog.text


def test_build_replay_ticks_repeats_samples_to_requested_count() -> None:
    samples = [
        {"image_path": "a.png", "label": "dialogue"},
        {"image_path": "b.png", "label": "config"},
        {"image_path": "c.png", "label": "save_load"},
    ]

    ticks = build_replay_ticks(samples, tick_count=8)

    assert [tick["tick_index"] for tick in ticks] == list(range(8))
    assert [Path(tick["image_path"]).name for tick in ticks] == [
        "a.png",
        "b.png",
        "c.png",
        "a.png",
        "b.png",
        "c.png",
        "a.png",
        "b.png",
    ]


def test_summarize_predictions_reports_accuracy_agreement_and_fallback() -> None:
    records = [
        Phase1PredictionRecord(
            tick_index=0,
            image_path="a.png",
            expected_label="dialogue",
            expected_stage="dialogue_stage",
            cnn_label="dialogue",
            cnn_stage="dialogue_stage",
            cnn_confidence=0.91,
            cnn_latency_ms=0.5,
            prototype_stage="dialogue_stage",
            prototype_confidence=0.7,
            prototype_latency_ms=0.1,
        ),
        Phase1PredictionRecord(
            tick_index=1,
            image_path="b.png",
            expected_label="save_load",
            expected_stage="save_load_stage",
            cnn_label="loading",
            cnn_stage="transition_stage",
            cnn_confidence=0.42,
            cnn_latency_ms=0.5,
            prototype_stage="save_load_stage",
            prototype_confidence=0.8,
            prototype_latency_ms=0.1,
        ),
    ]

    summary = summarize_predictions(records, threshold=0.75)

    assert summary["tick_count"] == 2
    assert summary["cnn_stage_accuracy"] == 0.5
    assert summary["prototype_stage_accuracy"] == 1.0
    assert summary["cnn_vs_prototype_stage_agreement"] == 0.5
    assert summary["cnn_high_confidence_rate"] == 0.5
    assert summary["cnn_primary_with_prototype_fallback_stage_accuracy"] == 1.0
