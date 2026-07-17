from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PIL import Image

from plugin.plugins.galgame_plugin.core.vision.vision_classifier import (
    GALGAME_VISION_LABELS,
    VisionScreenClassifier,
)
from plugin.plugins.galgame_plugin.core.vision.labels import vision_label_to_screen_type
from plugin.plugins.galgame_plugin.core.vision.vision_model_loader import VisionModelLoader
from plugin.plugins.galgame_plugin.models import (
    OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
    OCR_CAPTURE_PROFILE_STAGE_GALLERY,
    OCR_CAPTURE_PROFILE_STAGE_MENU,
)


class _FakeSession:
    def __init__(self, logits: list[float]) -> None:
        self.logits = np.asarray([logits], dtype=np.float32)
        self.seen_input: np.ndarray | None = None

    def get_inputs(self):
        return [SimpleNamespace(name="input")]

    def run(self, _outputs, feed):
        self.seen_input = feed["input"]
        return [self.logits]


class _FakeLoader:
    def __init__(self, session=None) -> None:
        self.session = session
        self.loaded_names: list[str] = []

    def load(self, model_name: str):
        self.loaded_names.append(model_name)
        return self.session


def _image() -> Image.Image:
    return Image.new("RGB", (320, 180), (32, 64, 96))


def test_vision_classifier_preprocesses_and_maps_dialogue_label() -> None:
    logits = [-2.0] * len(GALGAME_VISION_LABELS)
    logits[GALGAME_VISION_LABELS.index("dialogue")] = 6.0
    session = _FakeSession(logits)
    classifier = VisionScreenClassifier(_FakeLoader(session))

    classifier.load("v1_galgame")
    result = classifier.classify(_image())

    assert result is not None
    assert result["label"] == "dialogue"
    assert result["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_DIALOGUE
    assert result["confidence"] > 0.9
    assert set(result["all_scores"]) == set(GALGAME_VISION_LABELS)
    assert session.seen_input is not None
    assert session.seen_input.shape == (1, 3, 224, 224)
    assert session.seen_input.dtype == np.float32


def test_vision_classifier_maps_choice_menu_to_existing_menu_stage() -> None:
    logits = [-2.0] * len(GALGAME_VISION_LABELS)
    logits[GALGAME_VISION_LABELS.index("choice_menu")] = 6.0
    classifier = VisionScreenClassifier(_FakeLoader(_FakeSession(logits)))

    classifier.load("v1_galgame")
    result = classifier.classify(_image())

    assert result is not None
    assert result["label"] == "choice_menu"
    assert result["screen_type"] == OCR_CAPTURE_PROFILE_STAGE_MENU


def test_vision_classifier_uses_shared_label_to_screen_type_mapping() -> None:
    assert vision_label_to_screen_type("backlog") == OCR_CAPTURE_PROFILE_STAGE_GALLERY


def test_vision_classifier_degrades_when_session_unavailable() -> None:
    classifier = VisionScreenClassifier(_FakeLoader(None))

    classifier.load("missing")

    assert classifier.classify(_image()) is None


def test_vision_classifier_rejects_logits_label_mismatch() -> None:
    classifier = VisionScreenClassifier(
        _FakeLoader(_FakeSession([1.0])),
        labels=("dialogue", "choice_menu"),
    )

    classifier.load("v1_galgame")

    assert classifier.classify(_image()) is None
    assert classifier.last_error == "logits_label_mismatch: logits=1, labels=2"


def test_vision_model_loader_keeps_cpu_provider_as_fallback(tmp_path, monkeypatch) -> None:
    class _FakeOrt:
        @staticmethod
        def get_available_providers():
            return ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.core.vision.vision_model_loader.ort",
        _FakeOrt,
    )

    loader = VisionModelLoader(tmp_path)

    assert loader.providers == ["DmlExecutionProvider", "CPUExecutionProvider"]


def test_vision_model_loader_caches_and_reloads_sessions(tmp_path, monkeypatch) -> None:
    created: list[str] = []

    class _FakeOrt:
        GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")

        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider"]

        class SessionOptions:
            graph_optimization_level = None
            intra_op_num_threads = 0
            inter_op_num_threads = 0
            enable_mem_pattern = False

        class InferenceSession:
            def __init__(self, path, *, providers, sess_options):
                del providers, sess_options
                created.append(path)

            def get_inputs(self):
                return [SimpleNamespace(name="input")]

            def run(self, _outputs, _feed):
                return [np.zeros((1, 1), dtype=np.float32)]

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.core.vision.vision_model_loader.ort",
        _FakeOrt,
    )
    model_path = tmp_path / "v1_galgame.onnx"
    model_path.write_bytes(b"fake")
    loader = VisionModelLoader(tmp_path)

    first = loader.load("v1_galgame")
    second = loader.load("v1_galgame")
    reloaded = loader.reload("v1_galgame")

    assert first is second
    assert reloaded is not first
    assert created == [str(model_path), str(model_path)]

    loader.close()

    assert loader._sessions == {}


def test_vision_model_loader_rejects_path_traversal_model_names(tmp_path, monkeypatch) -> None:
    class _FakeOrt:
        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider"]

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.core.vision.vision_model_loader.ort",
        _FakeOrt,
    )
    loader = VisionModelLoader(tmp_path)

    assert loader.load("../escape") is None
    assert "invalid_model_name" in loader.last_error


def test_vision_model_loader_keeps_context_for_session_creation_failure(
    tmp_path,
    monkeypatch,
) -> None:
    class _FakeOrt:
        GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")

        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider"]

        class SessionOptions:
            graph_optimization_level = None
            intra_op_num_threads = 0
            inter_op_num_threads = 0
            enable_mem_pattern = False

        class InferenceSession:
            def __init__(self, path, *, providers, sess_options):
                del path, providers, sess_options
                raise RuntimeError("truncated model")

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.core.vision.vision_model_loader.ort",
        _FakeOrt,
    )
    (tmp_path / "v1_galgame.onnx").write_bytes(b"broken")
    loader = VisionModelLoader(tmp_path)

    assert loader.load("v1_galgame") is None
    assert "truncated model" in loader.last_error
    assert "v1_galgame.onnx" in loader.last_error


def test_vision_model_loader_caches_session_when_warmup_fails(tmp_path, monkeypatch) -> None:
    class _FakeOrt:
        GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")

        @staticmethod
        def get_available_providers():
            return ["CPUExecutionProvider"]

        class SessionOptions:
            graph_optimization_level = None
            intra_op_num_threads = 0
            inter_op_num_threads = 0
            enable_mem_pattern = False

        class InferenceSession:
            def __init__(self, path, *, providers, sess_options):
                del path, providers, sess_options

            def get_inputs(self):
                return [SimpleNamespace(name="input")]

            def run(self, _outputs, _feed):
                raise RuntimeError("warmup failed")

    monkeypatch.setattr(
        "plugin.plugins.galgame_plugin.core.vision.vision_model_loader.ort",
        _FakeOrt,
    )
    (tmp_path / "v1_galgame.onnx").write_bytes(b"fake")
    loader = VisionModelLoader(tmp_path)

    session = loader.load("v1_galgame")

    assert session is not None
    assert loader.load("v1_galgame") is session
    assert "warmup failed" in loader.last_warning
