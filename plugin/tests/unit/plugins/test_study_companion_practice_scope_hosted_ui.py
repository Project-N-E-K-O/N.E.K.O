from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

SURFACES = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "study_companion"
    / "surfaces"
)


def _source(name: str) -> str:
    return (SURFACES / name).read_text(encoding="utf-8")


def test_hosted_knowledge_map_sets_canonical_scope_before_opening_study_panel() -> None:
    source = _source("knowledge_map.tsx")

    assert "stage?: string;" in source
    assert "course_family?: string;" in source
    assert "selectedStage" in source
    assert "selectedChapter" in source
    assert "selectedUnit" in source
    assert "study_set_practice_scope" in source
    assert "activatePracticeScope('explicit_topic')" in source
    assert "activatePracticeScope('explicit_scope')" in source
    assert "eligible_topic_ids" not in source
    assert "canonicalScope.display_path" in source
    assert "activationRevision" in source
    assert "Number.isSafeInteger" in source
    assert "surfaceId: 'study-panel'" in source
    assert "props.host?.origin" in source
    assert "study_generate_targeted_question" not in source

    set_scope = source.index("study_set_practice_scope")
    open_surface = source.index("STUDY_SURFACE_MESSAGE_TYPES.openSurface", set_scope)
    assert set_scope < open_surface


def test_hosted_knowledge_map_restores_and_clears_persisted_scope() -> None:
    source = _source("knowledge_map.tsx")

    assert "study_get_practice_scope" in source
    assert "study_clear_practice_scope" in source
    assert "async function clearPracticeScope()" in source
    assert "setCanonicalScope(nextScope?.display_path?.length ? nextScope : null);" in source
    assert "setCanonicalScope(null);" in source
    assert "ui.button.clear_practice_scope" in source

    load_start = source.index("useEffect(() =>")
    load_end = source.index("}, [props.api]);", load_start)
    load = source[load_start:load_end]
    assert "study_get_practice_scope" in load
    assert "setSelectedNode(null);\n        setCanonicalScope(null);" not in load


def test_hosted_knowledge_map_keeps_map_available_when_scope_recovery_fails() -> None:
    source = _source("knowledge_map.tsx")
    load_start = source.index("useEffect(() =>")
    load_end = source.index("}, [props.api]);", load_start)
    load = source[load_start:load_end]

    assert "Promise.all([" not in load
    assert "setScopeRecoveryFailed(true);" in load
    assert "study_knowledge_map" in load
    assert "study_get_practice_scope" in load
    assert "canonicalScope?.display_path?.length || scopeRecoveryFailed" in source


def test_hosted_topic_scope_derives_its_identity_from_the_selected_node() -> None:
    source = _source("knowledge_map.tsx")
    activation_start = source.index("async function activatePracticeScope")
    activation_end = source.index("const subjectCounts", activation_start)
    activation = source[activation_start:activation_end]

    assert "if (scopeBusy) return;" in activation
    assert "mode === 'explicit_topic' ? selectedNode" in activation
    assert "requestedStage" in activation
    assert "topicNode.chapter" in activation
    assert "topicNode.unit" in activation
    assert "scopeBusy || selectedStage === 'all'" not in activation


def test_hosted_node_dialog_restores_focus_on_every_close_path() -> None:
    source = _source("knowledge_map.tsx")

    assert "nodeTriggerRef" in source
    assert "function closeNodeDetail" in source
    assert "nodeTriggerRef.current?.focus()" in source
    assert "onClick={closeNodeDetail}" in source
    assert "if (event.target === event.currentTarget) closeNodeDetail();" in source

    escape_start = source.index("const closeNodeDialog")
    escape_end = source.index("document.addEventListener('keydown'", escape_start)
    assert "closeNodeDetail()" in source[escape_start:escape_end]


def test_hosted_study_panel_refreshes_scope_and_context_for_every_generate() -> None:
    source = _source("study_panel.tsx")

    assert "type PracticeScope" in source
    assert "study_get_practice_scope" in source
    assert "activePracticeScope" in source
    assert "activePracticeScope?.display_path" in source
    assert "generateButtonRef" in source
    assert "const freshScope = await loadPracticeScope(controller.signal);" in source
    assert "const context = await loadQuestionContext(controller.signal);" in source
    assert "questionContext?.selection_context_id" not in source

    generate_start = source.index("async function generateQuestion()")
    generate_end = source.index("async function evaluateAnswer()", generate_start)
    generate = source[generate_start:generate_end]
    assert "contextRefreshControllerRef.current?.abort()" in generate
    assert "contextRefreshControllerRef.current = null" in generate
    assert generate.index("setQuestionContext(null)") < generate.index("loadPracticeScope")
    assert generate.index("loadPracticeScope") < generate.index("loadQuestionContext")
    assert generate.index("loadQuestionContext") < generate.index("study_generate_targeted_question")


def test_hosted_study_panel_activation_message_is_strict_and_keeps_current_question() -> None:
    source = _source("study_panel.tsx")

    assert "neko-hosted-surface-activated" in source
    assert "event.source !== window.parent" in source
    assert "event.origin !== expectedHostOrigin" in source
    assert "surfaceId !== 'study-panel'" in source
    assert "payload?.revision ?? payload?.activationRevision" in source
    assert "Number.isSafeInteger(activationRevision)" in source
    assert "setQuestionContext(null)" in source
    assert "contextRefreshControllerRef" in source
    assert "generateButtonRef.current?.focus()" in source

    activation_start = source.index("function handleHostedSurfaceActivated")
    activation_end = source.index("window.addEventListener('message'", activation_start)
    activation = source[activation_start:activation_end]
    assert "setCurrentQuestion" not in activation
    assert "beginStudyRequest" not in activation
    assert "contextRefreshControllerRef.current?.abort()" in activation
    assert activation.index("loadPracticeScope") < activation.index("loadQuestionContext")


def test_surface_message_target_origin_accepts_host_prop_without_weakening_fallback() -> None:
    source = _source("study_surface_utils.ts")

    assert "hostOrigin?: unknown" in source
    assert "host?: {" in source
    assert "payload.host.origin" in source
    assert "postStudySurfaceMessage(" in source
    assert "studySurfaceTargetOrigin(hostOrigin)" in source
    assert "window.parent?.postMessage?.(message, studySurfaceTargetOrigin(hostOrigin))" in source


def test_hosted_scope_read_failure_clears_the_stale_visible_scope() -> None:
    source = _source("study_panel.tsx")
    load_start = source.index("async function loadPracticeScope")
    load_end = source.index("async function setMode", load_start)
    load_scope = source[load_start:load_end]

    assert "catch (error)" in load_scope
    assert "setActivePracticeScope(null)" in load_scope
    assert "throw error" in load_scope
