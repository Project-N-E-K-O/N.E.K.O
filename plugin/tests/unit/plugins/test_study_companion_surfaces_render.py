from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

pytestmark = pytest.mark.unit


PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
SURFACES_DIR = PLUGIN_DIR / "surfaces"


def _javascript_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    end = source.find("\nfunction ", start + 1)
    assert end > start, f"could not find the end of JavaScript function {name}"
    return source[start:end]


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


def test_study_explain_surfaces_expose_solution_narration_outcomes() -> None:
    hosted = _read("study_panel.tsx")
    fallback = "\n".join(
        (PLUGIN_DIR / "static" / filename).read_text(encoding="utf-8")
        for filename in ("outcome-formatters.js", "main.js")
    )

    for source in (hosted, fallback):
        assert "solution_narration_scheduled" in source
        assert "solution_narration_status" in source
        assert "solution_narration_reason" in source
        assert "solution_repair_attempted" in source
        assert "solution_narration_missing_sections" in source
        assert "diagnostic" in source
        assert "ui.error.solution_narration_missing_answer" in source
        assert "ui.error.solution_narration_truncated_repair_timeout" in source
        assert "ui.error.solution_narration_incomplete" in source
        assert "ui.error.solution_narration_repair_failed" in source
        assert "ui.status.solution_narration_scheduled" in source
        assert "status === 'not_applicable'" in source
        assert source.index("status === 'repair_failed'") < source.index(
            "reason === 'missing_answer'"
        )
        assert source.index("status === 'degraded'") < source.index(
            "reason === 'missing_answer'"
        )
        truncated_check = source.index("diagnostic === 'output_truncated'")
        assert truncated_check < source.index("reason === 'missing_answer'")
        assert truncated_check < source.index("missingSections.includes('answer')")


def test_study_explain_surfaces_expose_general_narration_outcomes() -> None:
    hosted = _read("study_panel.tsx")
    fallback_main = (PLUGIN_DIR / "static" / "main.js").read_text(encoding="utf-8")
    fallback = "\n".join(
        (PLUGIN_DIR / "static" / filename).read_text(encoding="utf-8")
        for filename in ("outcome-formatters.js", "main.js")
    )

    required_contract_fields = {
        "general_narration_scheduled",
        "general_narration_status",
        "general_narration_reason",
        "general_narration_response_mode",
    }
    for source in (hosted, fallback):
        assert all(field in source for field in required_contract_fields)
        assert "formatGeneralNarrationNotice" in source
        assert "ui.status.general_narration_scheduled" in source
        assert "ui.status.general_narration_disabled" in source
        assert "ui.error.general_narration_degraded" in source
        assert "ui.error.general_narration_runtime_unavailable" in source
        assert "ui.error.general_narration_delivery_failed" in source
        formatter = _javascript_function(source, "formatGeneralNarrationNotice")
        assert "data.reply" not in formatter

    assert "formatGeneralNarrationNotice(data, t)" in hosted
    assert "outcomeFormatters.formatGeneralNarrationNotice(data, t)" in fallback_main


def test_study_explain_surfaces_warn_when_history_is_not_persisted() -> None:
    hosted = _read("study_panel.tsx")
    fallback = (PLUGIN_DIR / "static" / "main.js").read_text(encoding="utf-8")

    for source in (hosted, fallback):
        assert "history_persisted === false" in source
        assert "ui.error.history_not_saved" in source


def test_general_narration_messages_and_setting_exist_in_all_locales() -> None:
    required_keys = {
        "ui.error.history_not_saved",
        "ui.status.general_narration_scheduled",
        "ui.status.general_narration_disabled",
        "ui.error.general_narration_degraded",
        "ui.error.general_narration_runtime_unavailable",
        "ui.error.general_narration_delivery_failed",
        "ui.settings.general_narration_enabled.label",
        "ui.settings.general_narration_enabled.help",
    }
    locale_paths = sorted((PLUGIN_DIR / "i18n").glob("*.json"))

    assert len(locale_paths) == 8
    for locale_path in locale_paths:
        bundle = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required_keys <= bundle.keys(), locale_path.stem
        assert all(bundle[key].strip() for key in required_keys), locale_path.stem

    zh_cn = json.loads((PLUGIN_DIR / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    assert zh_cn["ui.status.general_narration_scheduled"] == "已安排通用讲述。"
    assert zh_cn["ui.settings.general_narration_enabled.label"] == "通用回答讲述"


def test_memory_deck_surfaces_expose_pagination_and_localized_load_more() -> None:
    hosted = _read("memory_deck_list.tsx")
    fallback = (PLUGIN_DIR / "static" / "surface-panels.js").read_text(
        encoding="utf-8"
    )

    for source in (hosted, fallback):
        assert "next_offset" in source
        assert "has_more" in source
        assert "offset" in source
        assert "ui.button.load_more_cards" in source

    assert "const loadingByDeck = new Map();" in fallback
    assert "if (loadingByDeck.get(deckId)) return;" in fallback
    assert "loadMore.disabled = loadingByDeck.get(deck.id) === true;" in fallback

    locale_paths = sorted((PLUGIN_DIR / "i18n").glob("*.json"))
    assert len(locale_paths) == 8
    for locale_path in locale_paths:
        bundle = json.loads(locale_path.read_text(encoding="utf-8"))
        assert bundle["ui.button.load_more_cards"].strip(), locale_path.stem


def test_memory_deck_surfaces_load_every_deck_page() -> None:
    shared = _read("memory_shared.ts")
    hosted_list = _read("memory_deck_list.tsx")
    hosted_importer = _read("memory_importer.tsx")
    fallback = (PLUGIN_DIR / "static" / "surface-panels.js").read_text(
        encoding="utf-8"
    )

    assert "export async function listAllMemoryDecks" in shared
    assert "while (hasMore)" in shared
    assert "next_offset" in shared
    for source in (hosted_list, hosted_importer):
        assert "listAllMemoryDecks" in source
        assert "study_memory_list_decks" not in source
    assert "async function listAllMemoryDecks(ctx)" in fallback
    assert fallback.count("decks = await listAllMemoryDecks(ctx);") == 2


def test_solution_narration_outcome_messages_exist_in_all_locales() -> None:
    expected_truncated_zh_cn = (
        "解答因达到输出长度上限而被截断，自动补全未能在时限内完成，因此未安排朗读。"
        "请重新生成精简解答。"
    )
    expected_zh_cn = (
        "讲解生成不完整：缺少“答案”部分，因此未安排朗读。请重新解析。"
    )
    required_keys = {
        "ui.status.solution_narration_scheduled",
        "ui.status.solution_narration_disabled",
        "ui.error.solution_narration_missing_answer",
        "ui.error.solution_narration_truncated_repair_timeout",
        "ui.error.solution_narration_incomplete",
        "ui.error.solution_narration_repair_failed",
        "ui.error.solution_narration_runtime_unavailable",
        "ui.error.solution_narration_delivery_failed",
        "ui.error.solution_narration_degraded",
        "ui.error.solution_narration_not_scheduled",
    }
    locale_paths = sorted((PLUGIN_DIR / "i18n").glob("*.json"))

    assert [path.stem for path in locale_paths] == [
        "en",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    ]
    for locale_path in locale_paths:
        bundle = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required_keys <= bundle.keys(), locale_path.stem
        assert all(bundle[key].strip() for key in required_keys), locale_path.stem

    zh_cn = json.loads((PLUGIN_DIR / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    assert zh_cn["ui.error.solution_narration_missing_answer"] == expected_zh_cn
    assert (
        zh_cn["ui.error.solution_narration_truncated_repair_timeout"]
        == expected_truncated_zh_cn
    )


def test_study_explain_surfaces_render_backend_knowledge_guidance_evidence() -> None:
    hosted = _read("study_panel.tsx")
    fallback = "\n".join(
        (PLUGIN_DIR / "static" / filename).read_text(encoding="utf-8")
        for filename in ("outcome-formatters.js", "main.js")
    )

    required_contract_fields = {
        "knowledge_guidance_applied",
        "knowledge_guidance_status",
        "knowledge_guidance_subject",
        "knowledge_guidance_content_type",
        "knowledge_guidance_entity",
        "knowledge_guidance_focus_topic",
        "knowledge_guidance_related_topics",
        "knowledge_guidance_source",
    }
    for source in (hosted, fallback):
        assert all(field in source for field in required_contract_fields)
        assert "formatKnowledgeGuidanceEvidence" in source
        assert "ui.knowledge_guidance.applied" in source
        assert "ui.knowledge_guidance.not_matched" in source
        assert "ui.knowledge_guidance.low_confidence" in source
        assert "ui.knowledge_guidance.routing_unavailable" in source
        assert "status === 'not_applicable'" in source
        assert "knowledge_guidance_focus_topic" in source
        assert "knowledge_guidance_related_topics" in source
        formatter = _javascript_function(source, "formatKnowledgeGuidanceEvidence")
        assert "data.reply" not in formatter


def test_outcome_formatters_are_pure_frozen_and_cover_all_outcomes() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source_path = PLUGIN_DIR / "static" / "outcome-formatters.js"
    script = r"""
const fs = require('node:fs');
global.window = {};
global.t = () => { throw new Error('implicit global translator used'); };
global.fetch = () => { throw new Error('fetch used'); };
Object.defineProperty(global, 'document', {
  configurable: true,
  get() { throw new Error('document used'); },
});
eval(fs.readFileSync(process.env.OUTCOME_FORMATTERS_JS, 'utf8'));

const api = window.StudyOutcomeFormatters;
const calls = [];
const translate = (key, fallback) => {
  calls.push([key, fallback]);
  return `translated:${key}`;
};
const narrationCases = {
  empty: {},
  not_applicable: { solution_narration_status: 'not_applicable' },
  scheduled_flag: { solution_narration_scheduled: true },
  scheduled_status: { solution_narration_status: 'scheduled' },
  disabled: { solution_narration_status: 'disabled' },
  degraded: { solution_narration_status: 'degraded' },
  repair_failed: { solution_narration_status: 'repair_failed' },
  invalid_repair: { solution_narration_reason: 'invalid_repair_response' },
  attempted_repair: {
    solution_repair_attempted: true,
    solution_narration_scheduled: false,
  },
  truncated_repair_timeout: {
    diagnostic: 'output_truncated',
    solution_narration_status: 'incomplete',
    solution_narration_reason: 'insufficient_time_budget',
    solution_narration_missing_sections: ['answer', 'transfer'],
  },
  missing_answer_reason: { solution_narration_reason: 'missing_answer' },
  missing_answer_section: {
    solution_narration_status: 'incomplete',
    solution_narration_missing_sections: ['Answer'],
  },
  incomplete: { solution_narration_status: 'incomplete' },
  runtime_unavailable: { solution_narration_status: 'runtime_unavailable' },
  event_bus_unavailable: { solution_narration_reason: 'event_bus_unavailable' },
  delivery_failed: { solution_narration_status: 'delivery_failed' },
  event_delivery_failed: { solution_narration_reason: 'event_delivery_failed' },
  not_scheduled: { solution_narration_scheduled: false },
  unknown: { solution_narration_status: 'future_status' },
};
const narration = Object.fromEntries(
  Object.entries(narrationCases).map(([name, outcome]) => [
    name,
    api.formatSolutionNarrationNotice(outcome, translate),
  ]),
);
const generalNarrationCases = {
  empty: {},
  not_applicable: { general_narration_status: 'not_applicable' },
  scheduled_flag: { general_narration_scheduled: true },
  scheduled_status: { general_narration_status: 'scheduled' },
  disabled: { general_narration_status: 'disabled' },
  degraded: { general_narration_status: 'degraded' },
  degraded_reason: { general_narration_reason: 'degraded_reply' },
  empty_reply: { general_narration_reason: 'empty_reply' },
  runtime_unavailable: { general_narration_status: 'runtime_unavailable' },
  event_bus_unavailable: { general_narration_reason: 'event_bus_unavailable' },
  delivery_failed: { general_narration_status: 'delivery_failed' },
  event_delivery_failed: { general_narration_reason: 'event_delivery_failed' },
  communication_disabled: { general_narration_reason: 'communication_disabled' },
  general_narration_disabled: { general_narration_reason: 'general_narration_disabled' },
  unsupported: { general_narration_reason: 'unsupported_response_mode' },
  false_only: { general_narration_scheduled: false },
  unknown: { general_narration_status: 'future_status' },
  status_over_flag: {
    general_narration_status: 'delivery_failed',
    general_narration_scheduled: true,
  },
  status_over_reason: {
    general_narration_status: 'scheduled',
    general_narration_reason: 'event_delivery_failed',
  },
};
const generalNarration = Object.fromEntries(
  Object.entries(generalNarrationCases).map(([name, outcome]) => [
    name,
    api.formatGeneralNarrationNotice(outcome, translate),
  ]),
);
const knowledgeCases = {
  not_applicable: { knowledge_guidance_status: 'not_applicable' },
  not_matched: { knowledge_guidance_status: 'not_matched' },
  low_confidence: { knowledge_guidance_status: 'low_confidence' },
  routing_unavailable: { knowledge_guidance_status: 'routing_unavailable' },
  applied: {
    knowledge_guidance_status: 'applied',
    knowledge_guidance_applied: true,
    knowledge_guidance_subject: 'math',
    knowledge_guidance_content_type: 'concept_map',
    knowledge_guidance_entity: 'Triangle',
    knowledge_guidance_focus_topic: { label: 'Pythagoras' },
    knowledge_guidance_related_topics: [{ name: 'Distance' }, { label: 'Vectors' }],
    knowledge_guidance_source: 'semantic_route',
  },
  missing_focus: {
    knowledge_guidance_status: 'applied',
    knowledge_guidance_applied: true,
  },
  unknown: {
    knowledge_guidance_status: 'future_status',
    knowledge_guidance_applied: false,
  },
};
const knowledge = Object.fromEntries(
  Object.entries(knowledgeCases).map(([name, outcome]) => [
    name,
    api.formatKnowledgeGuidanceEvidence(outcome, translate),
  ]),
);
console.log(JSON.stringify({
  frozen: Object.isFrozen(api),
  keys: Object.keys(api).sort(),
  calls,
  narration,
  generalNarration,
  knowledge,
}));
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "OUTCOME_FORMATTERS_JS": str(source_path)},
        # CI Windows runners occasionally stall node.exe startup well past 10s.
        timeout=60,
    )
    payload = json.loads(result.stdout)

    assert payload["frozen"] is True
    assert payload["keys"] == [
        "formatGeneralNarrationNotice",
        "formatKnowledgeGuidanceEvidence",
        "formatSolutionNarrationNotice",
    ]
    assert payload["narration"] == {
        "empty": "",
        "not_applicable": "",
        "scheduled_flag": "translated:ui.status.solution_narration_scheduled",
        "scheduled_status": "translated:ui.status.solution_narration_scheduled",
        "disabled": "translated:ui.status.solution_narration_disabled",
        "degraded": "translated:ui.error.solution_narration_degraded",
        "repair_failed": "translated:ui.error.solution_narration_repair_failed",
        "invalid_repair": "translated:ui.error.solution_narration_repair_failed",
        "attempted_repair": "translated:ui.error.solution_narration_repair_failed",
        "truncated_repair_timeout": "translated:ui.error.solution_narration_truncated_repair_timeout",
        "missing_answer_reason": "translated:ui.error.solution_narration_missing_answer",
        "missing_answer_section": "translated:ui.error.solution_narration_missing_answer",
        "incomplete": "translated:ui.error.solution_narration_incomplete",
        "runtime_unavailable": "translated:ui.error.solution_narration_runtime_unavailable",
        "event_bus_unavailable": "translated:ui.error.solution_narration_runtime_unavailable",
        "delivery_failed": "translated:ui.error.solution_narration_delivery_failed",
        "event_delivery_failed": "translated:ui.error.solution_narration_delivery_failed",
        "not_scheduled": "translated:ui.error.solution_narration_not_scheduled",
        "unknown": "",
    }
    assert payload["generalNarration"] == {
        "empty": "",
        "not_applicable": "",
        "scheduled_flag": "translated:ui.status.general_narration_scheduled",
        "scheduled_status": "translated:ui.status.general_narration_scheduled",
        "disabled": "translated:ui.status.general_narration_disabled",
        "degraded": "translated:ui.error.general_narration_degraded",
        "degraded_reason": "translated:ui.error.general_narration_degraded",
        "empty_reply": "translated:ui.error.general_narration_degraded",
        "runtime_unavailable": "translated:ui.error.general_narration_runtime_unavailable",
        "event_bus_unavailable": "translated:ui.error.general_narration_runtime_unavailable",
        "delivery_failed": "translated:ui.error.general_narration_delivery_failed",
        "event_delivery_failed": "translated:ui.error.general_narration_delivery_failed",
        "communication_disabled": "translated:ui.status.general_narration_disabled",
        "general_narration_disabled": "translated:ui.status.general_narration_disabled",
        "unsupported": "",
        "false_only": "",
        "unknown": "",
        "status_over_flag": "translated:ui.error.general_narration_delivery_failed",
        "status_over_reason": "translated:ui.status.general_narration_scheduled",
    }
    assert payload["knowledge"]["not_applicable"] == ""
    assert payload["knowledge"]["not_matched"] == (
        "translated:ui.knowledge_guidance.not_matched"
    )
    assert payload["knowledge"]["low_confidence"] == (
        "translated:ui.knowledge_guidance.low_confidence"
    )
    assert payload["knowledge"]["routing_unavailable"] == (
        "translated:ui.knowledge_guidance.routing_unavailable"
    )
    assert payload["knowledge"]["missing_focus"] == (
        "translated:ui.knowledge_guidance.not_matched"
    )
    assert payload["knowledge"]["unknown"] == ""
    assert payload["knowledge"]["applied"].splitlines() == [
        "translated:ui.knowledge_guidance.applied",
        "translated:ui.knowledge_guidance.subject: translated:ui.knowledge_guidance.subject.math",
        "translated:ui.knowledge_guidance.content_type: translated:ui.knowledge_guidance.content_type.concept_map",
        "translated:ui.knowledge_guidance.entity: Triangle",
        "translated:ui.knowledge_guidance.focus_topic: Pythagoras",
        "translated:ui.knowledge_guidance.related_topics: Distance, Vectors",
        "translated:ui.knowledge_guidance.source: translated:ui.knowledge_guidance.source.semantic_route",
    ]
    assert payload["calls"]


def test_knowledge_guidance_evidence_messages_exist_in_all_locales() -> None:
    subject_keys = {
        f"ui.knowledge_guidance.subject.{subject}"
        for subject in (
            "math", "chinese", "english", "physics", "chemistry", "biology",
            "history", "geography", "politics", "economics", "computer_science",
            "unknown",
        )
    }
    required_keys = {
        "ui.knowledge_guidance.applied",
        "ui.knowledge_guidance.not_matched",
        "ui.knowledge_guidance.low_confidence",
        "ui.knowledge_guidance.routing_unavailable",
        "ui.knowledge_guidance.subject",
        "ui.knowledge_guidance.content_type",
        "ui.knowledge_guidance.entity",
        "ui.knowledge_guidance.focus_topic",
        "ui.knowledge_guidance.related_topics",
        "ui.knowledge_guidance.source",
        "ui.knowledge_guidance.subject.chinese",
        "ui.knowledge_guidance.content_type.literary_work",
        "ui.knowledge_guidance.source.semantic_route",
        "ui.knowledge_guidance.source.selected_topic",
    } | subject_keys
    locale_paths = sorted((PLUGIN_DIR / "i18n").glob("*.json"))

    assert len(locale_paths) == 8
    for locale_path in locale_paths:
        bundle = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required_keys <= bundle.keys(), locale_path.stem
        assert all(bundle[key].strip() for key in required_keys), locale_path.stem

    zh_cn = json.loads((PLUGIN_DIR / "i18n" / "zh-CN.json").read_text(encoding="utf-8"))
    assert zh_cn["ui.knowledge_guidance.applied"] == "已应用知识图谱"
    assert zh_cn["ui.knowledge_guidance.not_matched"] == (
        "本次未匹配到可信的相关知识图谱，回答未使用其他学科节点。"
    )


def test_study_companion_hosted_surface_files_are_brand_renderable() -> None:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as handle:
        config = tomllib.load(handle)

    assert config["plugin"]["ui"].get("panel", []) == []
    assert config["plugin"]["ui"]["expose_legacy_static_panel"] is False
    assert (SURFACES_DIR / "quickstart.tsx").is_file()

    for surface_id, filename in SURFACE_FILES.items():
        source = _read(filename)
        assert "export default function" in source, surface_id
        assert "ensureBrandCSS" in source, surface_id
        assert "ensureBrandCSS();" in source, surface_id
        assert 'className="study-panel surface-shell"' in source, surface_id
        assert "style={{" not in source, surface_id
        assert "ui.surface." in source, surface_id


def test_study_companion_hosted_calls_forward_abort_signal_to_sdk() -> None:
    surface_utils = _read("study_surface_utils.ts")
    hosted_sdk = (PLUGIN_DIR.parents[1] / "sdk" / "hosted-ui" / "index.d.ts").read_text(
        encoding="utf-8"
    )

    assert "api.call(entryId, args, { timeoutMs, signal })" in surface_utils
    assert (
        "options?: { timeoutMs?: number; signal?: AbortSignal; userInitiated?: boolean }"
        in hosted_sdk
    )


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
    assert ".pomodoro-ring[data-mode=\"short_break\"]" in surface_utils
    assert ".pomodoro-ring[data-mode=\"long_break\"]" in surface_utils
    assert "modeKey === 'short_break'" in pomodoro
    assert "modeKey === 'long_break'" in pomodoro
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
    assert "study_memory_list_deck_items" in memory_decks
    assert "ui.button.view_cards" in memory_decks
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
    assert "KNOWLEDGE_MAP_ZOOM_LEVELS" not in knowledge_map
    assert "knowledge-map-zoom" not in knowledge_map
    assert "knowledge-map-viewport" not in knowledge_map
    assert "knowledge-node-detail-dialog" in knowledge_map
    assert "setSelectedNode(null)" in knowledge_map
    assert "ui.button.close" in knowledge_map
    assert 'className="pomodoro-ring"' in pomodoro
    assert 'className="pomodoro-ring__time"' in pomodoro
    assert 'data-action="start"' in pomodoro
    assert 'disabled={!isFocusing}' in pomodoro
    assert "const allowCustomDuration = status.config?.allow_custom_duration !== false;" in pomodoro
    assert "disabled={isRunning || !allowCustomDuration}" in pomodoro
    assert "allowCustomDuration ? { focus_minutes: normalizedFocusMinutes() } : {}" in pomodoro
    assert 'className="pomodoro-duration"' in pomodoro
    assert 'className="pomodoro-ring__value"' in pomodoro
    assert "strokeDashoffset={progressOffset}" in pomodoro
    assert "useRef<AbortController | null>(null)" in study_panel
    assert "event.key !== 'Escape'" in study_panel
    assert "explainControllerRef.current?.abort()" in study_panel
    assert "panel.addEventListener('keydown', closeOrCancelOnEscape, true)" in study_panel
    assert "panel.removeEventListener('keydown', closeOrCancelOnEscape, true)" in study_panel


def test_study_panel_locale_change_releases_aborted_request_busy_state() -> None:
    source = _read("study_panel.tsx")
    bootstrap = source.index("void resumeDocumentJob(documentController.signal)")
    effect_start = source.rfind("useEffect(() => {", 0, bootstrap)
    effect_end = source.index("}, [props.locale]);", bootstrap)
    locale_effect = source[effect_start:effect_end]

    assert "explainControllerRef.current?.abort();" in locale_effect
    assert locale_effect.index("setBusy(false);") < locale_effect.index(
        "const controller = beginStudyRequest();"
    )


def test_hosted_document_start_cancellation_keeps_pending_job_recoverable() -> None:
    source = _read("study_panel.tsx")
    analyze_start = source.index("async function analyzeDocument")
    analyze_end = source.index("async function refresh(", analyze_start)
    analyze = source[analyze_start:analyze_end]
    resume_start = source.index("async function resumeDocumentJob")
    resume_end = source.index("async function cancelKnownDocumentJob", resume_start)
    resume = source[resume_start:resume_end]

    assert "const documentPendingStartTokenRef = useRef('');" in source
    assert "documentPendingStartTokenRef.current = startToken;" in source
    assert "documentPendingStartTokenRef.current" in source[
        source.index("function savedDocumentJobId"):source.index(
            "function rememberPendingDocumentJob"
        )
    ]
    assert "}, controller.signal);" in analyze
    assert "if (controller.signal.aborted) return;" in analyze
    assert (
        "const pendingStartRecoveryDeadline = documentPendingStartDeadlineRef.current"
        in resume
    )
    assert "|| Date.now() + timeoutForEntry('study_start_document_analysis');" in resume
    assert "pendingStart" in resume
    assert "&& data?.status === 'idle'" in resume
    assert "Date.now() < pendingStartRecoveryDeadline" in resume
    assert "await waitForDocumentPoll(1_000, signal);" in resume


def test_hosted_document_recovery_polling_uses_its_own_controller() -> None:
    source = _read("study_panel.tsx")
    bootstrap = source.index("void resumeDocumentJob")
    effect_start = source.rfind("useEffect(() => {", 0, bootstrap)
    effect_end = source.index("}, [props.locale]);", bootstrap)
    locale_effect = source[effect_start:effect_end]

    assert "const documentController = documentPollingController();" in locale_effect
    assert "void resumeDocumentJob(documentController.signal)" in locale_effect
    assert "resumeDocumentJob(controller.signal)" not in locale_effect


def test_hosted_document_recovery_restores_safe_card_metadata() -> None:
    source = _read("study_panel.tsx")
    resume_start = source.index("async function resumeDocumentJob")
    resume_end = source.index("async function cancelKnownDocumentJob", resume_start)
    resume = source[resume_start:resume_end]
    analyze_start = source.index("async function analyzeDocument")
    analyze_end = source.index("async function refresh(", analyze_start)
    analyze = source[analyze_start:analyze_end]

    assert "document?: DocumentJobMetadata;" in source
    assert "function restoredStudyDocument" in source
    assert "truncated: metadata?.truncated === true" in source
    assert "setStudyDocument((current) => current || restoredDocument);" in resume
    assert "document_truncated: currentDocument.truncated" in analyze


def test_memory_deck_surface_refetches_items_when_reopened() -> None:
    source = _read("memory_deck_list.tsx")
    loader_start = source.index("async function loadDeckItems")
    toggle_start = source.index("async function toggleDeckItems")
    toggle_end = source.index("async function saveDeckGoal", toggle_start)
    loader = source[loader_start:toggle_start]
    toggle = source[toggle_start:toggle_end]

    assert "if (itemsByDeck[deckId]) return;" not in toggle
    assert "await loadDeckItems(deckId);" in toggle
    assert "study_memory_list_deck_items" in loader
    assert "append = false" in loader
    assert ": 0;" in loader


def test_hosted_document_completion_refresh_is_best_effort() -> None:
    source = _read("study_panel.tsx")
    helper_start = source.index("async function refreshAfterDocumentCompletion")
    helper_end = source.index("async function pollDocumentJob", helper_start)
    helper = source[helper_start:helper_end]
    completed_start = source.index("if (['completed', 'succeeded'].includes")
    completed_end = source.index(
        "if (['failed', 'canceled', 'timeout'].includes", completed_start
    )
    completed = source[completed_start:completed_end]

    assert "try {" in helper
    assert "await refresh(signal, { updateReply: false });" in helper
    assert "catch" in helper
    assert "setReply" not in helper
    assert "await refreshAfterDocumentCompletion(controller.signal);" in completed


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
    assert "setZoomIndex" not in hosted

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
    assert "const KNOWLEDGE_MAP_ZOOM_LEVELS = [60, 75, 90, 100];" in fallback
    assert "let knowledgeMapZoomIndex = 3;" in fallback
    assert "renderKnowledgeZoomControls" in fallback
    assert "surfaceDrawer.dataset.windowScale = String(level)" in fallback
    assert "function syncZoomControls()" in fallback
    assert "status.textContent = tf('ui.knowledge.zoom_status'" in fallback
    assert "surfaceDrawerBody.replaceChildren(renderKnowledgePanel" not in fallback.split("function renderKnowledgeZoomControls()", 1)[1].split("function visibleKnowledgeNodes", 1)[0]
