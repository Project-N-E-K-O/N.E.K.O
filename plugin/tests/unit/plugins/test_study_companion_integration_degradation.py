from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.fsrs_bridge import FSRSBridge
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.study_ocr_pipeline import StudyOcrPipeline
from plugin.plugins.study_companion.tutor_llm_agent import TutorLLMAgent

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


class _FailingBackend:
    def extract_text(self, image: object) -> str:
        raise RuntimeError("ocr offline")


def test_integration_degradation_paths_for_ocr_llm_and_fsrs(tmp_path: Path) -> None:
    pipeline = StudyOcrPipeline(
        logger=_Logger(),
        config=StudyConfig(),
        ocr_backend=_FailingBackend(),
    )
    fsrs = FSRSBridge(retention_target=float("nan"))

    ocr = pipeline.snapshot_from_image("image")

    assert ocr.status == "ocr_failed"
    # NaN retention targets must fall back to the bridge's conservative minimum.
    assert fsrs.retention_target == 0.1


@pytest.mark.asyncio
async def test_integration_llm_timeout_degrades_to_local_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = TutorLLMAgent(config=StudyConfig(language="en"), logger=_Logger())

    async def fail_call(messages: list[dict[str, str]]) -> str:
        raise TimeoutError("llm timeout")

    monkeypatch.setattr(agent, "_call_model", fail_call)

    reply = await agent.concept_explain("Photosynthesis converts light.", mode="teaching")

    assert reply.degraded is True
    assert "local fallback" in reply.reply
    assert reply.diagnostic == "timeout"
