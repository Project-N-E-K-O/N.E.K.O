# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Plugin contract, part 2: how a plugin entry gets selected.

Promised unchanged (RFC #3683, section 4.2): which plugins and entries are
visible, what ``short_description`` and ``keywords`` do, and the width of the
two-stage filter. Every test runs once per implementation in
``SELECTION_IMPLS``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.nekoclaw.plugin_server_fake import entry, plugin
from tests.unit.nekoclaw.selection_impls import SELECTION_IMPLS, ScriptedSelectionModel


@pytest.fixture(params=sorted(SELECTION_IMPLS))
def impl(request: Any, monkeypatch: Any) -> Any:
    return SELECTION_IMPLS[request.param](monkeypatch)


def _filler(index: int) -> str:
    # About 150 tokens of text unrelated to any request used below.
    return " ".join(f"module{index}word{n}" for n in range(60))


def _large_catalog(count: int = 40) -> list[dict[str, Any]]:
    """Plugins whose descriptions together exceed the stage-1 threshold."""
    return [
        plugin(f"bulk{index:02d}", f"Bulk plugin {index}. {_filler(index)}", [entry("run", "Run it")])
        for index in range(count)
    ]


def _desc_ids(plugins_desc: str) -> list[str]:
    ids = []
    for line in plugins_desc.splitlines():
        if line.startswith("- "):
            ids.append(line[2:].split(":", 1)[0].replace(" [KEYWORD MATCH]", ""))
    return ids


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metadata", "hidden"),
    [
        ({"agent_auto": False}, True),
        ({"agent_exposed": False}, True),
        ({"llm_exposed": False}, True),
        ({"agent_hidden": True}, True),
        ({}, False),
        ({"agent_auto": True, "agent_exposed": True, "llm_exposed": True}, False),
        ({"agent_hidden": False}, False),
        # Only the exact values hide an entry.
        ({"agent_auto": None}, False),
        ({"agent_auto": 0}, False),
        ({"agent_auto": "false"}, False),
        ({"agent_hidden": 1}, False),
        ({"agent_hidden": "true"}, False),
    ],
)
def test_entry_visibility_flags(impl, metadata, hidden):
    assert impl.entry_hidden(entry("x", metadata=metadata)) is hidden


def test_entry_without_metadata_is_visible(impl):
    assert impl.entry_hidden({"id": "x"}) is False
    assert impl.entry_hidden({"id": "x", "metadata": None}) is False
    assert impl.entry_hidden("not a dict") is False


def test_visible_entries_drop_hidden_and_nameless_ones(impl):
    listed = plugin(
        "notes",
        "Notes",
        [
            entry("save", "Save"),
            entry("purge", "Purge", metadata={"agent_hidden": True}),
            {"description": "no id"},
            "not a dict",
            entry("list", "List", metadata={"agent_auto": False}),
            entry("find", "Find"),
        ],
    )

    assert [item["id"] for item in impl.visible_entries(listed)] == ["save", "find"]


def test_plugin_without_an_entries_field_gets_the_default_entry(impl):
    assert impl.visible_entries({"id": "legacy"}) == [
        {"id": "run", "description": "Default plugin entry"}
    ]


def test_plugin_whose_entries_are_not_a_list_has_none(impl):
    assert impl.visible_entries({"id": "odd", "entries": "run"}) == []


@pytest.mark.asyncio
async def test_passive_plugin_never_reaches_the_prompt(impl):
    plugins = [
        plugin("radio", "Plays radio", keywords=["radio"], passive=True),
        plugin("lamp", "Controls the lamp"),
    ]
    model = ScriptedSelectionModel()

    run = await impl.select("turn on the radio", plugins, model)

    assert _desc_ids(run.plugins_desc) == ["lamp"]
    assert "radio" not in run.plugins_desc


@pytest.mark.asyncio
async def test_plugin_with_only_hidden_entries_never_reaches_the_prompt(impl):
    plugins = [
        plugin("vault", "Secrets", [entry("dump", "Dump", metadata={"llm_exposed": False})], keywords=["secret"]),
        plugin("lamp", "Controls the lamp"),
    ]
    model = ScriptedSelectionModel()

    run = await impl.select("show me the secret", plugins, model)

    assert _desc_ids(run.plugins_desc) == ["lamp"]


@pytest.mark.asyncio
async def test_hidden_entry_is_left_out_of_a_visible_plugin(impl):
    plugins = [
        plugin(
            "notes",
            "Notes",
            [entry("save", "Save a note"), entry("purge", "Delete all notes", metadata={"agent_auto": False})],
        )
    ]
    model = ScriptedSelectionModel()

    run = await impl.select("save a note", plugins, model)

    assert run.plugins_desc == "- notes: Notes | entries: [save: Save a note]"


@pytest.mark.asyncio
async def test_no_visible_plugin_means_no_model_call(impl):
    plugins = [plugin("radio", "Plays radio", passive=True)]
    model = ScriptedSelectionModel()

    run = await impl.select("turn on the radio", plugins, model)

    assert run.calls == []
    assert run.executable is False


# ---------------------------------------------------------------------------
# What the model is shown about a plugin
# ---------------------------------------------------------------------------


def test_description_line_lists_entries_with_their_arguments(impl):
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "unit": {"type": "string", "enum": ["c", "f"]},
            "days": {"type": "integer"},
            "raw": {},
        },
        "required": ["city"],
    }
    plugins = [
        plugin(
            "weather",
            "Weather lookups",
            [entry("today", "Today's weather", input_schema=schema), entry("ping")],
        )
    ]

    assert impl.desc_lines(plugins) == [
        "- weather: Weather lookups | entries: [today: Today's weather "
        "args(city:string, unit:string enum=[c|f], days:integer, raw:any required=['city']); ping]"
    ]


def test_description_line_shows_eight_fields_and_twelve_enum_values(impl):
    properties = {f"f{n}": {"type": "string"} for n in range(10)}
    properties["f0"] = {"type": "string", "enum": [f"v{n}" for n in range(15)]}
    plugins = [
        plugin("wide", "Wide", [entry("go", "Go", input_schema={"type": "object", "properties": properties})])
    ]

    (line,) = impl.desc_lines(plugins)

    shown = "|".join(f"v{n}" for n in range(12))
    assert f"f0:string enum=[{shown}|... +3 more]" in line
    assert "f7:string" in line
    assert "f8:string" not in line


def test_description_lines_skip_plugins_without_id_or_visible_entries(impl):
    plugins = [
        {"description": "no id", "entries": [entry("run")]},
        plugin("hidden", "Hidden", [entry("x", metadata={"agent_hidden": True})]),
        plugin("ok", "Fine", [entry("run", "Run")]),
    ]

    assert impl.desc_lines(plugins) == ["- ok: Fine | entries: [run: Run]"]


# ---------------------------------------------------------------------------
# keywords
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "keywords", "expected"),
    [
        ("Play some MUSIC please", ["music"], True),
        ("play a song", ["music"], False),
        ("查一下明天的天气", ["天气|气温"], True),
        ("remind me at 5", [r"remind\s+me"], True),
        ("remindme at 5", [r"remind\s+me"], False),
        # A pattern that does not compile is matched as plain text.
        ("price is 5 (usd", ["(usd"], True),
        ("PRICE IS 5 (USD", ["(usd"], True),
        ("price is 5 usd", ["(usd"], False),
        ("anything", [], False),
    ],
)
def test_keyword_patterns_are_case_insensitive_regexes(impl, text, keywords, expected):
    assert impl.keyword_match(text, keywords) is expected


@pytest.mark.asyncio
async def test_keyword_hit_is_marked_in_the_prompt(impl):
    plugins = [
        plugin("lamp", "Controls the lamp", keywords=["lamp", "light"]),
        plugin("fan", "Controls the fan", keywords=["fan"]),
    ]
    model = ScriptedSelectionModel()

    run = await impl.select("turn off the LIGHT", plugins, model)

    assert run.plugins_desc.splitlines() == [
        "- lamp [KEYWORD MATCH]: Controls the lamp | entries: [run: Default plugin entry]",
        "- fan: Controls the fan | entries: [run: Default plugin entry]",
    ]


def test_annotation_marks_only_the_named_plugins(impl):
    desc = "- lamp: a | entries: [run]\n- lamp2: b | entries: [run]"

    assert impl.annotate(desc, ["lamp"]) == (
        "- lamp [KEYWORD MATCH]: a | entries: [run]\n- lamp2: b | entries: [run]"
    )
    assert impl.annotate(desc, []) == desc


# ---------------------------------------------------------------------------
# The two-stage filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_small_catalog_goes_straight_to_fine_selection(impl):
    plugins = [plugin(f"p{n}", f"Plugin number {n}") for n in range(5)]
    model = ScriptedSelectionModel()

    run = await impl.select("do something", plugins, model)

    assert run.stages == ["fine"]
    assert _desc_ids(run.plugins_desc) == [f"p{n}" for n in range(5)]


@pytest.mark.asyncio
async def test_threshold_is_three_thousand_tokens_of_descriptions(impl):
    from config import AGENT_PLUGIN_DESC_BM25_THRESHOLD
    from utils.tokenize import count_tokens

    assert AGENT_PLUGIN_DESC_BM25_THRESHOLD == 3000

    def tokens(count: int) -> int:
        return count_tokens(chr(10).join(impl.desc_lines(_large_catalog(count))))

    # The largest catalog still at or under the threshold, and the next one.
    size = next(n for n in range(1, 40) if tokens(n + 1) > 3000)
    below = _large_catalog(size)
    above = _large_catalog(size + 1)
    assert tokens(size) <= 3000 < tokens(size + 1)

    run_below = await impl.select("bulk plugin", below, ScriptedSelectionModel())
    run_above = await impl.select("bulk plugin", above, ScriptedSelectionModel())

    assert run_below.stages == ["fine"]
    assert run_above.stages == ["coarse", "fine"]


@pytest.mark.asyncio
async def test_large_catalog_shows_the_union_of_bm25_keywords_and_coarse_screen(impl):
    plugins = _large_catalog()
    plugins[3]["description"] = "Looks up train timetables. " + _filler(3)
    plugins[7]["keywords"] = ["timetable"]
    # bulk11 shares no word with the request: only the coarse screen can bring it in.
    model = ScriptedSelectionModel(coarse=["bulk11", "not-a-plugin"])

    run = await impl.select("when is the next train, check the timetable", plugins, model)

    assert run.stages == ["coarse", "fine"]
    # bulk03 by text match, bulk07 by keyword, bulk11 by the coarse screen; in
    # catalog order, and nothing else.
    assert _desc_ids(run.plugins_desc) == ["bulk03", "bulk07", "bulk11"]
    assert "- bulk07 [KEYWORD MATCH]:" in run.plugins_desc


@pytest.mark.asyncio
async def test_large_catalog_shows_the_ten_best_text_matches(impl):
    plugins = _large_catalog()
    for number in range(14):
        plugins[number * 2]["description"] = "Looks up train timetables. " + _filler(number * 2)
    model = ScriptedSelectionModel(coarse=[])

    run = await impl.select("train timetables", plugins, model)

    # Fourteen plugins match by their text; ten of them are shown, no fewer.
    shown = _desc_ids(run.plugins_desc)
    assert len(shown) == 10
    assert set(shown) <= {f"bulk{number * 2:02d}" for number in range(14)}


@pytest.mark.asyncio
async def test_large_catalog_with_no_match_shows_no_plugins(impl):
    plugins = _large_catalog()
    model = ScriptedSelectionModel(coarse=[])

    run = await impl.select("qqqq", plugins, model)

    # The full list is not shown instead. (The analyzer still asks the fine
    # selection, with an empty list; the task brain does not ask at all.)
    assert run.stages[0] == "coarse"
    assert run.plugins_desc == "No plugins available."
    assert run.executable is False


@pytest.mark.asyncio
async def test_large_catalog_rejects_a_plugin_outside_the_shown_set(impl):
    plugins = _large_catalog()
    plugins[3]["description"] = "Looks up train timetables. " + _filler(3)
    pick_hidden = {"has_task": True, "can_execute": True, "plugin_id": "bulk20", "entry_id": "run", "plugin_args": {}}
    model = ScriptedSelectionModel(coarse=[], fine=[pick_hidden, pick_hidden])

    run = await impl.select("train timetables", plugins, model)

    assert _desc_ids(run.plugins_desc) == ["bulk03"]
    assert run.executable is False


def test_text_match_takes_at_most_ten_plugins(impl):
    plugins = [plugin(f"t{n:02d}", f"train helper {n}") for n in range(15)]

    selected, keyword_ids = impl.stage1("train", plugins)

    assert len(selected) == 10
    assert keyword_ids == []


def test_text_match_needs_a_shared_word(impl):
    plugins = [plugin("lamp", "Controls the lamp"), plugin("fan", "Controls the fan")]

    assert impl.stage1("weather tomorrow", plugins) == ([], [])
    assert impl.stage1("lamp", plugins) == (["lamp"], [])


def test_text_match_reads_ids_names_descriptions_short_descriptions_and_entries(impl):
    described = plugin(
        "pid",
        "long description",
        [entry("entryid", "entry description")],
        name="Display Name",
        short_description="short blurb",
    )

    assert impl.corpus_text(described) == (
        "pid Display Name long description short blurb entryid entry description"
    )


def test_keyword_hits_join_the_first_stage_whatever_their_text_score(impl):
    plugins = [
        plugin("lamp", "Controls the lamp", keywords=["illuminat"]),
        plugin("fan", "Controls the fan"),
    ]

    # No word of the request occurs in either description.
    assert impl.stage1("illuminate everything", plugins) == (["lamp"], ["lamp"])


@pytest.mark.asyncio
async def test_coarse_screen_reads_short_descriptions(impl):
    from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
    from utils.tokenize import count_tokens

    plugins = _large_catalog()
    plugins[0]["short_description"] = "Tells jokes"
    plugins[1]["short_description"] = " ".join(f"blurb{n}" for n in range(400))
    model = ScriptedSelectionModel(coarse=[])

    run = await impl.select("make me laugh", plugins, model)

    coarse = run.calls[0]
    assert coarse.stage == "coarse"
    assert "- bulk00: Tells jokes" in coarse.system
    # No short description: the description stands in, cut to the same budget.
    line_two = next(line for line in coarse.system.splitlines() if line.startswith("- bulk02: "))
    assert line_two.startswith("- bulk02: Bulk plugin 2.")
    long_line = next(line for line in coarse.system.splitlines() if line.startswith("- bulk01: "))
    assert long_line.endswith("...")
    assert count_tokens(long_line[len("- bulk01: ") :]) <= AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
    assert "make me laugh" in coarse.system


# ---------------------------------------------------------------------------
# The fine selection's answer
# ---------------------------------------------------------------------------


def _weather_catalog() -> list[dict[str, Any]]:
    return [
        plugin(
            "weather",
            "Weather lookups",
            [entry("today", "Today's weather"), entry("week", "Seven day forecast")],
        ),
        plugin("lamp", "Controls the lamp"),
    ]


@pytest.mark.asyncio
async def test_valid_answer_names_plugin_entry_and_arguments(impl):
    pick = {
        "has_task": True,
        "can_execute": True,
        "plugin_id": "weather",
        "entry_id": "today",
        "plugin_args": {"city": "Paris"},
        "reason": "",
    }
    model = ScriptedSelectionModel(fine=[pick])

    run = await impl.select("weather in Paris", _weather_catalog(), model)

    assert run.stages == ["fine"]
    assert (run.executable, run.plugin_id, run.entry_id, run.plugin_args) == (
        True,
        "weather",
        "today",
        {"city": "Paris"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrong",
    [
        {"has_task": True, "can_execute": True, "plugin_id": "weather", "entry_id": "now", "plugin_args": {}},
        {"has_task": True, "can_execute": True, "plugin_id": "wetter", "entry_id": "today", "plugin_args": {}},
    ],
    ids=["unknown-entry", "unknown-plugin"],
)
async def test_an_entry_or_plugin_that_does_not_exist_is_never_called(impl, wrong):
    right = {**wrong, "plugin_id": "weather", "entry_id": "today"}
    model = ScriptedSelectionModel(fine=[wrong, right])

    run = await impl.select("weather in Paris", _weather_catalog(), model)

    # Asked once; the answer is not acted on and not asked about again.
    assert run.stages == ["fine"]
    assert (run.executable, run.entry_id) == (False, None)


@pytest.mark.asyncio
async def test_answer_without_a_task_is_not_executable(impl):
    model = ScriptedSelectionModel(fine=[{"has_task": False, "can_execute": False, "reason": "chat"}])

    run = await impl.select("how are you", _weather_catalog(), model)

    assert run.stages == ["fine"]
    assert run.executable is False
