from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

pytestmark = pytest.mark.unit


PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
SURFACES_DIR = PLUGIN_DIR / "surfaces"


SURFACE_FILES = {
    "daily-goal-editor": "daily_goal_editor.tsx",
    "due-review-panel": "due_review_panel.tsx",
    "habit-dashboard": "habit_dashboard.tsx",
    "knowledge-contribution-settings": "knowledge_contribution_settings.tsx",
    "knowledge-map": "knowledge_map.tsx",
    "memory-deck-list": "memory_deck_list.tsx",
    "memory-importer": "memory_importer.tsx",
    "note-editor": "note_editor.tsx",
    "note-exporter": "note_exporter.tsx",
    "note-search": "note_search.tsx",
    "notebook-panel": "notebook_panel.tsx",
    "passage-recitation": "passage_recitation.tsx",
    "pomodoro-panel": "pomodoro_panel.tsx",
    "quickstart": "quickstart.tsx",
    "session-summary": "session_summary.tsx",
    "study-panel": "study_panel.tsx",
    "word-review": "word_review.tsx",
}


def _read(filename: str) -> str:
    return (SURFACES_DIR / filename).read_text(encoding="utf-8")


def test_study_companion_registered_surfaces_are_brand_renderable() -> None:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)

    registered = {
        item["id"]: Path(item["entry"]).name
        for item in config["plugin"]["ui"]["panel"]
    }
    assert "quickstart" not in registered
    assert (SURFACES_DIR / "quickstart.tsx").is_file()

    expected_registered = {
        surface_id: filename
        for surface_id, filename in SURFACE_FILES.items()
        if surface_id != "quickstart"
    }
    assert registered == expected_registered

    for surface_id, filename in SURFACE_FILES.items():
        source = _read(filename)
        assert "export default function" in source, surface_id
        assert "ensureBrandCSS" in source, surface_id
        assert "ensureBrandCSS();" in source, surface_id
        assert 'className="study-panel surface-shell"' in source, surface_id
        assert "style={{" not in source, surface_id
        assert "ui.surface." in source, surface_id


def test_study_companion_surfaces_share_ui8_interaction_styles_and_messages() -> None:
    surface_utils = _read("study_surface_utils.ts")
    word_review = _read("word_review.tsx")
    due_review = _read("due_review_panel.tsx")
    memory_decks = _read("memory_deck_list.tsx")
    knowledge_map = _read("knowledge_map.tsx")
    pomodoro = _read("pomodoro_panel.tsx")
    study_panel = _read("study_panel.tsx")

    assert "export const STUDY_SURFACE_MESSAGE_TYPES" in surface_utils
    assert "openSurface: 'neko-study-open-surface'" in surface_utils
    assert "reviewCompleted: 'neko-study-review-completed'" in surface_utils
    assert "refreshSummary: 'neko-study-refresh-summary'" in surface_utils
    assert "memoryDeckUpdated: 'neko-study-memory-deck-updated'" in surface_utils
    assert ".surface-shell" in surface_utils
    assert ".study-panel button:focus-visible" in surface_utils
    assert "@media (prefers-reduced-motion: reduce)" in surface_utils
    assert ".knowledge-node[data-mastery=\"weak\"]" in surface_utils
    assert ".pomodoro-ring[data-mode=\"break_short\"]" in surface_utils
    assert ".study-panel button[data-rating=\"again\"]" in surface_utils

    assert "data-rating={rating}" in word_review
    assert "STUDY_SURFACE_MESSAGE_TYPES.reviewCompleted" in word_review
    assert "reviewed_count: 1" in word_review
    assert "data-rating={rating}" in due_review
    assert "study_memory_review_item" in due_review
    assert "STUDY_SURFACE_MESSAGE_TYPES.reviewCompleted" in due_review
    assert "reviewed_count: 1" in due_review
    assert "STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated" in word_review
    assert "STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated" in due_review
    assert "STUDY_SURFACE_MESSAGE_TYPES.memoryDeckUpdated" in memory_decks
    assert "data-mastery={nodeMasteryLevel(node)}" in knowledge_map
    assert "Number.isFinite(mastery)" in knowledge_map
    assert "masteryText" in knowledge_map
    assert "selectedSubject" in knowledge_map
    assert "knowledge-subject-selector" in knowledge_map
    assert "KNOWLEDGE_SUBJECT_OPTIONS" in knowledge_map
    assert "'math'" in knowledge_map
    assert "'computer_science'" in knowledge_map
    assert "ui.knowledge.subject.${normalized}" in knowledge_map
    assert "study_knowledge_map', { limit: 1000 })" in knowledge_map
    assert "study_knowledge_map', { limit: 1000, subject" not in knowledge_map
    assert "const knownSubjects = KNOWLEDGE_SUBJECT_OPTIONS.filter((subject) => subjectCounts.has(subject));" in knowledge_map
    assert "visibleNodes.slice(0, 60)" in knowledge_map
    assert "edgeGroups(props, visibleNodes, visibleEdges)" in knowledge_map
    assert "edgeGraph(props, visibleNodes, visibleEdges)" in knowledge_map
    assert "edgeGroups(props, nodes, edges)" in knowledge_map
    assert ".flatMap((group) => group.items.slice(0, 6)" in knowledge_map
    assert "knowledge-edge-graph__svg" in knowledge_map
    assert "knowledge-edge-arrow-surface" in knowledge_map
    assert "knowledge-node-detail-dialog" in knowledge_map
    assert "setSelectedNode(null)" in knowledge_map
    assert "ui.button.close" in knowledge_map
    assert 'className="pomodoro-ring"' in pomodoro
    assert "useRef<AbortController | null>(null)" in study_panel
    assert "event.key !== 'Escape'" in study_panel
    assert "explainControllerRef.current?.abort()" in study_panel
    assert "panel.addEventListener('keydown', closeOrCancelOnEscape, true)" in study_panel
    assert "panel.removeEventListener('keydown', closeOrCancelOnEscape, true)" in study_panel


def test_knowledge_map_graph_and_dialog_regressions_are_guarded() -> None:
    hosted = _read("knowledge_map.tsx")
    fallback = (PLUGIN_DIR / "static" / "knowledge-map.js").read_text(encoding="utf-8")
    main = (PLUGIN_DIR / "static" / "main.js").read_text(encoding="utf-8")

    assert "toId: string" in hosted
    assert "from: String(group.fromId || '').trim()" in hosted
    assert "to: String(item.toId || '').trim()" in hosted
    assert "event.key === 'Escape'" in hosted
    assert "event.stopPropagation()" in hosted
    assert "document.addEventListener('keydown', closeNodeDialog)" in hosted
    assert "document.removeEventListener('keydown', closeNodeDialog)" in hosted
    assert "visibleNodes.length - 60" in hosted
    assert "dialogRef" in hosted
    assert "closeButtonRef" in hosted
    assert "event.key === 'Tab'" in hosted
    assert ".trim().toLowerCase()" in hosted

    assert "fromId: groupKey" in fallback
    assert "toId," in fallback
    assert "from: String(group.fromId || '').trim()" in fallback
    assert "to: String(item.toId || '').trim()" in fallback
    assert "String(edge.from || '') === nodeId && ['application', 'procedure_step', 'extends'].includes" in fallback
    assert "event.key === 'Escape'" in fallback
    assert "event.key === 'Tab'" in fallback
    assert "event.stopPropagation()" in fallback
    assert "const cappedNodes = nodes.slice(0, 80)" in fallback
    assert "nodes.length - cappedNodes.length" in fallback
    assert "const UNCATEGORIZED_SUBJECT = '__uncategorized__'" in fallback
    assert "subject === UNCATEGORIZED_SUBJECT ? '' : subject" in fallback
    assert "let knowledgeMapSubject = '';" in fallback
    assert "let knowledgeMapSubject = '';" not in main
    assert fallback.count("renderKnowledgePanel(lastKnowledgeMapPayload || lastStatusPayload)") >= 2
    assert "loadKnowledgeMapIntoDrawer('knowledge-map', requestId)" not in fallback
    assert "const displayedEdgeCount = visibleGroups.reduce" in fallback
    assert "(count, group) => count + group.items.length" in fallback
    assert "count + Math.min(group.items.length, 6)" not in fallback
    assert "edgeCount - displayedEdgeCount" in fallback
