"""Deployment pronunciations reach sherpa unchanged and share a wake label."""

import sys
from pathlib import Path
from types import SimpleNamespace

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
from main_logic.voice_input.wake_word import sherpa_backend as backend


def test_default_pronunciations_reach_model_with_shared_chinese_label(tmp_path, monkeypatch):
    expected = (
        "y ōu y í @悠宜",
        "Y UW1 IY0 @yui",
        "y ōu y ú @悠宜",
        "l iú y ú @悠宜",
    )
    captured = {}

    def constructor(**kwargs):
        captured.update(kwargs)
        captured["lines"] = Path(kwargs["keywords_file"]).read_text(encoding="utf-8").splitlines()
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        KeywordSpotter=constructor, __version__=backend.SUPPORTED_RUNTIME_VERSION,
        version=backend.SUPPORTED_RUNTIME_VERSION,
    ))
    paths = backend.model_files(str(tmp_path))
    for value in paths.values():
        Path(value).write_text("", encoding="utf-8")
    tokens = sorted({token for line in expected for token in line.split()[:-1]})
    Path(paths["tokens"]).write_text("\n".join(f"{token} {i}" for i, token in enumerate(tokens)), encoding="utf-8")

    config = backend.SherpaWakeWordConfig(str(tmp_path), DEFAULT_WAKE_WORD_KEYWORDS)
    spotter = backend._StreamingSpotter(config)

    assert captured["lines"] == list(expected)
    assert spotter.labels == {"悠宜", "yui"}
    assert captured["max_active_paths"] == 8
    assert captured["keywords_threshold"] == 0.25
    assert captured["keywords_score"] == 1.0
    assert not Path(captured["keywords_file"]).exists()
