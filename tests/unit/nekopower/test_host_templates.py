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

"""The presets of sessions: the built-in ones, and the user's template files."""

from __future__ import annotations

from pathlib import Path

import pytest

from nekopower.host.paths import DEFAULT_PROJECT, SpacePaths
from nekopower.host.templates import (
    NOT_FOR_CHILDREN,
    PRESETS,
    READ_ONLY_WITHHELD,
    SHADOW,
    Template,
    TemplateError,
    TemplateStore,
    builtin_templates,
    known_tools,
    model_name,
    parse_template,
)

FRONT = "---\nname: {name}\ndescription: {description}\n{more}---\n{body}"


def _text(name: str = "doc-writer", description: str = "When documents are to be written.", more: str = "", body: str = "") -> str:
    return FRONT.format(name=name, description=description, more=more, body=body)


def _parse(more: str = "", *, name: str = "doc-writer", file: str | None = None, **values: str) -> Template:
    return parse_template(_text(name=name, more=more, **values), name=file or name, source="global")


@pytest.fixture
def space(tmp_path: Path) -> SpacePaths:
    return SpacePaths(tmp_path / "task_agent_space")


def _put(space: SpacePaths, text: str, *, name: str = "doc-writer", project: str | None = None) -> Path:
    directory = space.templates_dir(project)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The built-in presets
# ---------------------------------------------------------------------------


def test_the_six_built_in_presets_are_there_under_the_keys_the_interface_draws_them_by():
    builtin = builtin_templates()

    assert tuple(builtin) == PRESETS == ("boss", "worker", "scout", "inspector", "planner", "general")
    assert all(item.source == "builtin" and item.avatar == name and item.label == "" for name, item in builtin.items())
    # A copy has a preset of its own, which is no template.
    assert SHADOW == "shadow" and SHADOW not in builtin


def test_a_session_of_its_own_and_a_worker_are_opened_with_everything():
    builtin = builtin_templates()

    for name in ("general", "worker"):
        item = builtin[name]
        assert item.withheld() == frozenset() and item.writes_files
        assert (item.read_only, item.writes, item.memory) == (False, "inherit", "project")
        assert item.delegates and item.remembers and not item.calls_nothing and item.max_requests is None
    # A session of its own is told nothing of a role: it is as it always was.
    assert builtin["general"].body == ""
    # Whose model each uses by default: the task agents', or that of whoever started it.
    assert [builtin[name].model for name in PRESETS] == ["agent", "inherit", "inherit", "inherit", "inherit", "agent"]


def test_the_lead_has_no_tool_to_do_the_work_with_and_whoever_it_starts_has():
    builtin = builtin_templates()
    boss = builtin["boss"]

    # Nothing that writes or edits a file or runs a command; what reads, searches, reaches other
    # sessions, starts task agents and remembers is its own as ever.
    assert boss.withheld() == frozenset({"write", "edit", "bash", "pwsh"}) and not boss.writes_files
    assert (boss.read_only, boss.writes, boss.memory) == (False, "inherit", "project")
    assert boss.delegates and boss.remembers and not boss.calls_nothing and boss.max_requests is None
    # Withheld from itself only: nothing of it is handed down to the task agents it starts.
    assert boss.handed_down() == frozenset()
    for name in ("worker", "scout", "inspector", "planner"):
        assert builtin[name].under(boss) is builtin[name]
    assert builtin["worker"].under(boss).writes_files


def test_what_a_template_withholds_from_itself_only_its_task_agents_keep():
    worker = builtin_templates()["worker"]
    both = _parse("withhold: [web_fetch]\nwithhold_own: [bash, write]\n")

    assert both.withhold == ("web_fetch",) and both.withhold_own == ("bash", "write")
    assert both.withheld() == frozenset({"web_fetch", "bash", "write"})
    assert both.handed_down() == frozenset({"web_fetch"})
    # A task agent it starts is without what it withholds, and has what it withholds from itself only.
    assert worker.under(both).withheld() == frozenset({"web_fetch"})
    # Said in one line or as a list; a tool named both ways is withheld from its task agents too.
    assert _parse("withhold_own: bash, edit\n").withhold_own == ("bash", "edit")
    twice = _parse("withhold: [bash]\nwithhold_own: [bash, edit]\n")
    assert (twice.withhold, twice.withhold_own) == (("bash",), ("edit",))
    assert worker.under(twice).withheld() == frozenset({"bash"})
    # A template that names nothing of the kind hands everything down, as before.
    plain = _parse("withhold: [bash]\n")
    assert plain.withhold_own == () and worker.under(plain).withheld() == frozenset({"bash"})
    assert _parse("withhold_own: [bash]\n").needs_builtin
    # What is handed down to a preset that withholds the same tool from itself only is handed down by it in turn.
    hands_out = _parse("withhold_own: [web_fetch, bash]\n")
    assert hands_out.handed_down() == frozenset()
    under = hands_out.under(both)
    assert under.handed_down() == frozenset({"web_fetch"}) and under.withheld() == frozenset({"web_fetch", "bash"})
    assert (under.withhold, under.withhold_own) == (("web_fetch",), ("bash",))
    assert worker.under(under).withheld() == frozenset({"web_fetch"})
    assert worker.under(hands_out).withheld() == frozenset()
    for wrong, why in (
        ("withhold_own: 3\n", "withhold_own is a list of tool names"),
        ("withhold_own: [no_such_tool]\n", "withhold_own names no tool: no_such_tool"),
        ("withhold_own: [report]\n", "report cannot be withheld"),
    ):
        with pytest.raises(TemplateError, match=why):
            _parse(wrong)


@pytest.mark.parametrize("name", ["scout", "planner"])
def test_a_scout_and_a_planner_read_only(name):
    item = builtin_templates()[name]

    assert item.read_only and item.writes == "none" and item.memory == "project"
    assert item.withheld() == frozenset(
        {"write", "edit", "bash", "pwsh", "capability_call", "note_lesson", "subagent"}
    ) == frozenset(READ_ONLY_WITHHELD)
    # The gateway calls nothing for it, and it starts no task agents.
    assert item.calls_nothing and not item.delegates
    # Word to another session is not a way to write: where it has those tools, it keeps them.
    assert not {"message_session", "ask_session", "schedule_wake"} & item.withheld()


def test_an_inspector_runs_commands_and_writes_nothing_and_has_no_memory():
    item = builtin_templates()["inspector"]

    assert item.withheld() == frozenset({"write", "edit", "capability_call", "note_lesson", "subagent"})
    # It has checks to run.
    assert not {"bash", "pwsh"} & item.withheld()
    assert (item.read_only, item.writes, item.memory) == (False, "none", "none")
    assert not item.remembers and item.calls_nothing and not item.delegates


def test_a_preset_that_takes_anything_away_is_held_to_in_the_built_in_runtime_only():
    builtin = builtin_templates()

    # dsh has file and command tools of its own, and makes its requests by itself.
    assert {name: item.needs_builtin for name, item in builtin.items()} == {
        "boss": True, "worker": False, "general": False, "scout": True, "inspector": True, "planner": True,
    }
    assert _parse("withhold: [web_fetch]\n").needs_builtin
    assert _parse("memory: none\n").needs_builtin
    assert _parse("max_requests: 5\n").needs_builtin
    assert not _parse("label: 文档猫\navatar: scout\nmodel: agent\n").needs_builtin


def test_what_the_built_in_presets_say_is_within_the_caps_of_a_template():
    from config import nekopower_settings as settings
    from utils.tokenize import count_tokens

    builtin = builtin_templates()

    for item in builtin.values():
        assert 0 < count_tokens(item.description) <= settings.TEMPLATE_DESCRIPTION_MAX_TOKENS
        assert count_tokens(item.body) <= settings.TEMPLATE_BODY_MAX_TOKENS
    assert sum(count_tokens(item.description) for item in builtin.values()) <= settings.TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS
    # Every tool a built-in preset withholds is a tool.
    assert all(item.withheld() <= known_tools() for item in builtin.values())


def test_each_role_says_what_it_is_for_what_it_hands_back_and_what_it_does_not_do():
    bodies = {name: item.body for name, item in builtin_templates().items()}

    for name in ("worker", "scout", "inspector", "planner"):
        assert bodies[name].startswith(f"Your role: {name}.")
        assert "Hand back" in bodies[name]
    assert bodies["boss"].startswith("Your role: lead of this project.")
    # The inspector gets the task and the change, checks by itself, and repairs nothing.
    inspector = bodies["inspector"]
    assert "what was to be done and what was changed" in inspector
    assert "run the checks yourself" in inspector and "do not repair" in inspector
    # The lead is told that it does none of the work, how a commission is handed over whole and
    # when a part is kept for a scout, a planner or an inspector, and what a brief has to say.
    boss = bodies["boss"]
    for word in (
        "you do none of the work yourself", "hand_over", "subagent", "scout", "planner", "inspector",
        "what it covers", "what it must not touch", "how to tell that it is done",
    ):
        assert word in boss
    # When work is split among several that change files (a team), and when it is not; what
    # the lead does while the team works, and that it reports once.
    for word in (
        "start_team", "only when it is wide and comes apart cleanly", "parts that share no files",
        "Do not split what is hard rather than wide", "what has to be done in order",
        "only one changes files in the project at a time, and splitting saves nothing",
        "wait for it with wait_agent", "neither do nor guess a part", "read the files where it matters",
        "(inspect) when a mistake would be costly", "Report to the user once, for the whole",
    ):
        assert word in boss
    # It no longer says that task agents which change files always take turns.
    assert "a second one waits its turn" not in boss


def test_a_model_reads_the_lead_and_a_copy_by_english_names():
    assert [model_name(name) for name in (*PRESETS, SHADOW)] == [
        "lead", "worker", "scout", "inspector", "planner", "general", "copy",
    ]


# ---------------------------------------------------------------------------
# A template file
# ---------------------------------------------------------------------------


def test_a_template_file_is_read_with_all_its_fields():
    made = _parse(
        "withhold: [bash, web_fetch]\nwrites: none\nmemory: none\nmodel: agent\nmax_requests: 12\n"
        "avatar: scout\nlabel: 文档猫\n",
        body="\nYou write documents.\n\nKeep them short.\n",
    )

    assert made == Template(
        name="doc-writer",
        description="When documents are to be written.",
        body="You write documents.\n\nKeep them short.",
        withhold=("bash", "web_fetch"),
        read_only=False,
        writes="none",
        memory="none",
        model="agent",
        max_requests=12,
        avatar="scout",
        label="文档猫",
        source="global",
    )
    assert made.withheld() == frozenset({"bash", "web_fetch", "write", "edit", "note_lesson"})


def test_a_template_that_says_no_more_than_it_must_is_like_a_worker():
    made = _parse()

    assert (made.withhold, made.read_only, made.writes, made.memory, made.model) == ((), False, "inherit", "project", "inherit")
    assert (made.max_requests, made.avatar, made.label, made.body) == (None, "general", "", "")
    assert made.withheld() == frozenset()


def test_withheld_tools_may_be_given_on_one_line_as_other_agents_files_have_them():
    assert _parse("withhold: bash, pwsh ,\n").withhold == ("bash", "pwsh")


def test_read_only_alone_means_it_writes_nowhere():
    made = _parse("read_only: true\n")

    assert (made.read_only, made.writes) == (True, "none")
    assert made.withheld() == frozenset(READ_ONLY_WITHHELD)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("name: doc-writer\ndescription: x\n", "does not begin with front matter"),
        ("---\nname: doc-writer\ndescription: x\n", "front matter is not closed"),
        ("---\nname: [unclosed\n---\n", "not valid YAML"),
        ("---\n- name\n- description\n---\n", "not a set of fields"),
    ],
)
def test_a_file_that_is_no_template_says_so(text, said):
    with pytest.raises(TemplateError, match=said):
        parse_template(text, name="doc-writer", source="global")


@pytest.mark.parametrize(
    ("more", "said"),
    [
        # A template only takes away: no model's name, address or key, nor anything unknown.
        ("base_url: http://127.0.0.1:1234/v1\n", "unknown fields: base_url"),
        ("api_key: sk-1\ntools: [bash]\n", "unknown fields: api_key, tools"),
        ("lifetime: lasting\n", "unknown fields: lifetime"),
        ("model: gpt-9\n", "model is one of agent, inherit, not 'gpt-9'"),
        ("model: [agent]\n", "model is one of agent, inherit"),
        ("withhold: [bsh]\n", "withhold names no tool: bsh"),
        ("withhold: 3\n", "withhold is a list of tool names"),
        ("withhold: [bash, 3]\n", "withhold is a list of tool names"),
        ("withhold: [report]\n", "report cannot be withheld"),
        ("read_only: yes please\n", "read_only is true or false"),
        ("read_only: 1\n", "read_only is true or false"),
        ("writes: tree\n", "writes: tree .* is not available yet"),
        ("writes: everywhere\n", "writes is one of inherit, none"),
        ("read_only: true\nwrites: inherit\n", "read_only is true and writes is not none"),
        ("memory: own\n", "memory is one of project, none"),
        ("max_requests: 0\n", "max_requests is a whole number from 1 to 500"),
        ("max_requests: 501\n", "max_requests is a whole number"),
        ("max_requests: true\n", "max_requests is a whole number"),
        ("max_requests: many\n", "max_requests is a whole number"),
        ("avatar: dragon\n", "avatar is one of boss, worker, scout, inspector, planner, general"),
        ("avatar: shadow\n", "avatar is one of"),
        ("label: [a, b]\n", "label is a text"),
        ("label: " + "很长的名字 " * 20 + "\n", "label is longer than 20 tokens"),
        # A field given twice: YAML would take the last without a word, and what the first withholds would be gone.
        ("withhold: [bash]\nwithhold: []\n", "the field withhold is given twice"),
        ("read_only: true\nlabel: x\nread_only: false\n", "the field read_only is given twice"),
        # A value the parser cannot make anything of is the file's mistake, like any other.
        ("label: 2026-13-01\n", r"not valid YAML \(ValueError\)"),
    ],
)
def test_a_field_that_is_wrong_is_an_error_and_never_passed_over(more, said):
    with pytest.raises(TemplateError, match=said):
        _parse(more)


def test_a_template_needs_a_name_that_is_its_files_and_a_description():
    with pytest.raises(TemplateError, match="name is 'other'; it has to be the name of the file, 'doc-writer'"):
        _parse(name="other", file="doc-writer")
    with pytest.raises(TemplateError, match="name is None"):
        parse_template("---\ndescription: x\n---\n", name="doc-writer", source="global")
    # Nor by what a model reads another preset called: "lead" is the lead of a project to a model,
    # and a template of that name would be taken for it wherever a model names a preset.
    for wrong in ("Doc", "1doc", "doc_writer", "d" * 33, SHADOW, "lead", "copy"):
        with pytest.raises(TemplateError, match="a template is named in lower-case letters.*not copy, lead, shadow"):
            _parse(name=wrong)
    with pytest.raises(TemplateError, match="description is missing"):
        parse_template("---\nname: doc-writer\n---\n", name="doc-writer", source="global")
    with pytest.raises(TemplateError, match="description is missing"):
        _parse(description="''")


def test_a_text_over_its_cap_is_an_error_and_not_cut():
    from config import nekopower_settings as settings

    with pytest.raises(TemplateError, match=f"description is longer than {settings.TEMPLATE_DESCRIPTION_MAX_TOKENS} tokens"):
        _parse(description="when " * 200)
    with pytest.raises(TemplateError, match=f"longer than {settings.TEMPLATE_BODY_MAX_TOKENS} tokens"):
        _parse(body="role " * 2000)
    # At the caps it is taken.
    assert _parse(description="when " * 99, body="role " * 790).body


# ---------------------------------------------------------------------------
# Which template a name means
# ---------------------------------------------------------------------------


def test_without_files_the_built_in_presets_are_all_there_is(space):
    store = TemplateStore(lambda: space)

    listed = store.listing()

    assert [(item.name, item.source, item.valid, item.error) for item in listed] == [
        (name, "builtin", True, "") for name in PRESETS
    ]
    assert store.resolve("scout") == builtin_templates()["scout"]
    with pytest.raises(TemplateError, match="no template of that name"):
        store.resolve("doc-writer")
    # A session of no project, and one of the project commissions without one are filed under.
    assert store.listing(DEFAULT_PROJECT) == listed


def test_the_projects_template_is_used_before_the_users_and_that_before_the_built_in_one(space):
    store = TemplateStore(lambda: space)
    _put(space, _text(name="scout", description="The user's scout."), name="scout")
    _put(space, _text(description="The user's writer."))
    _put(space, _text(description="The project's writer."), project="ledger-app")
    _put(space, _text(name="planner", description="The project's planner."), name="planner", project="ledger-app")

    everywhere = {item.name: item for item in store.listing()}
    here = {item.name: item for item in store.listing("ledger-app")}

    assert (everywhere["scout"].source, everywhere["scout"].template.description) == ("global", "The user's scout.")
    assert (everywhere["doc-writer"].source, everywhere["planner"].source) == ("global", "builtin")
    assert (here["doc-writer"].source, here["doc-writer"].template.description) == ("project", "The project's writer.")
    assert (here["planner"].source, here["scout"].source, here["worker"].source) == ("project", "global", "builtin")
    # The built-in ones first, in their order, then the others by name.
    assert [item.name for item in store.listing("ledger-app")] == [*PRESETS, "doc-writer"]
    # The user's scout is what a scout is from now on: it was not made read only.
    assert store.resolve("scout", "ledger-app").withheld() == frozenset()
    # Another project goes by the user's.
    assert store.resolve("doc-writer", "other").description == "The user's writer."


def test_a_wrong_file_is_listed_with_what_is_wrong_and_takes_nothing_else_down(space):
    store = TemplateStore(lambda: space)
    _put(space, _text(more="tools: [bash]\n"))
    _put(space, "no front matter", name="notes")
    _put(space, _text(name="reviewer"), name="reviewer")
    (space.templates_dir() / "bytes.md").write_bytes(b"---\nname: bytes\ndescription: \xff\xfe\n---\n")
    (space.templates_dir() / "README.txt").write_text("not a template", encoding="utf-8")

    listed = {item.name: item for item in store.listing()}

    assert set(listed) == {*PRESETS, "doc-writer", "notes", "reviewer", "bytes"}
    assert (listed["doc-writer"].valid, listed["doc-writer"].error) == (False, "unknown fields: tools")
    assert "does not begin with front matter" in listed["notes"].error
    assert "cannot be read (UnicodeDecodeError)" in listed["bytes"].error
    assert listed["reviewer"].valid and all(listed[name].valid for name in PRESETS)
    assert listed["doc-writer"].to_json() == {
        "name": "doc-writer", "label": "", "description": "", "avatar": "general", "source": "global",
        "read_only": None, "writes": None, "memory": None, "model": None, "valid": False,
        "error": "unknown fields: tools",
    }
    with pytest.raises(TemplateError, match="unknown fields: tools"):
        store.resolve("doc-writer")


def test_a_wrong_file_stands_in_the_way_of_the_template_it_would_have_replaced(space):
    # Its user meant to narrow the preset: a session of it is not opened with the built-in one instead.
    store = TemplateStore(lambda: space)
    _put(space, _text(name="scout", more="withhold: [bsh]\n"), name="scout", project="ledger-app")

    with pytest.raises(TemplateError, match="withhold names no tool: bsh"):
        store.resolve("scout", "ledger-app")
    # Elsewhere a scout is what it was.
    assert store.resolve("scout") == builtin_templates()["scout"]
    assert store.resolve("scout", "other") == builtin_templates()["scout"]


def test_a_folder_of_templates_that_cannot_be_read_is_not_taken_for_an_empty_one(space, monkeypatch):
    # A file in it may make a preset read only: with the folder taken for empty, a session of that
    # preset would be handed back the tools the file withholds.
    store = TemplateStore(lambda: space)
    assert store.resolve("general").withheld() == frozenset()
    _put(space, _text(name="general", more="read_only: true\n"), name="general")
    assert store.resolve("general").read_only
    real = Path.iterdir
    locked: set[Path] = {space.templates_dir()}

    def iterdir(self: Path) -> object:
        if self in locked:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    listed = store.listing()

    # No preset is used as if nothing narrowed it; each says why.
    assert [item.name for item in listed] == list(PRESETS)
    assert all(not item.valid and item.error == "the folder of templates templates cannot be read (PermissionError)" for item in listed)
    for name in ("general", "boss", "scout"):
        with pytest.raises(TemplateError, match="cannot be read"):
            store.resolve(name)
    assert store.roster() == [] and store.listing("ledger-app")[0].valid is False
    # Readable again, it is read again: nothing was kept of the time it was not.
    locked.clear()
    assert store.resolve("general").read_only and all(item.valid for item in store.listing())
    # A project's own folder that cannot be read stands in the way of that project's presets only.
    locked.add(space.templates_dir("ledger-app"))
    assert all(not item.valid for item in store.listing("ledger-app"))
    assert all(item.valid for item in store.listing()) and all(item.valid for item in store.listing("other"))


def test_what_is_named_templates_and_is_no_folder_is_not_taken_for_no_templates(space):
    store = TemplateStore(lambda: space)
    assert all(item.valid for item in store.listing())
    space.root.mkdir(parents=True)
    space.templates_dir().write_text("not a folder", encoding="utf-8")

    # It was listed while there was nothing there, and that is not what is gone by now.
    assert all(not item.valid and "cannot be read" in item.error for item in store.listing())
    space.templates_dir().unlink()
    assert all(item.valid for item in store.listing())


def test_a_template_that_is_a_folder_is_a_template_that_is_wrong(space):
    store = TemplateStore(lambda: space)
    (space.templates_dir() / "scout.md").mkdir(parents=True)

    listed = {item.name: item for item in store.listing()}

    # Not passed over for the built-in scout: what is named as a template is one.
    assert not listed["scout"].valid and "cannot be read" in listed["scout"].error
    assert listed["worker"].valid


def test_a_file_whose_values_cannot_be_read_is_one_wrong_template_and_no_more(space):
    store = TemplateStore(lambda: space)
    _put(space, _text(description="2026-13-01"), name="dated")
    _put(space, _text(name="twice", more="withhold: [bash]\nwithhold: []\n"), name="twice")
    _put(space, _text())

    listed = {item.name: item for item in store.listing()}

    assert listed["dated"].error == "the front matter is not valid YAML (ValueError)"
    assert listed["twice"].error == "the field withhold is given twice"
    assert listed["doc-writer"].valid and all(listed[name].valid for name in PRESETS)
    assert store.project_signature(DEFAULT_PROJECT) == () and store.project_signature(None) == ()


def test_a_file_too_large_and_files_past_the_most_a_folder_may_hold_are_errors(space, monkeypatch):
    monkeypatch.setattr("config.nekopower_settings.TEMPLATE_FILES_MAX", 2)
    monkeypatch.setattr("config.nekopower_settings.TEMPLATE_FILE_MAX_BYTES", 200)
    store = TemplateStore(lambda: space)
    _put(space, _text(name="a-one"), name="a-one")
    _put(space, _text(name="b-two", body="x" * 300), name="b-two")
    _put(space, _text(name="c-three"), name="c-three")

    listed = {item.name: item for item in store.listing()}

    assert listed["a-one"].valid
    assert listed["b-two"].error == "the file is larger than 200 bytes"
    assert listed["c-three"].error == "more than 2 template files in one folder"


def test_the_sentences_of_all_templates_together_have_a_cap_and_the_one_past_it_is_wrong(space, monkeypatch):
    from utils.tokenize import count_tokens

    builtin = sum(count_tokens(item.description) for item in builtin_templates().values())
    one = "Used when the thing in question is to be done as it says here."
    monkeypatch.setattr(
        "config.nekopower_settings.TEMPLATE_DESCRIPTIONS_TOTAL_MAX_TOKENS", builtin + 2 * count_tokens(one)
    )
    store = TemplateStore(lambda: space)
    for name in ("a-one", "b-two"):
        _put(space, _text(name=name, description=one), name=name)
    # The project's own, one of them in the place of a built-in preset (whose sentence then no longer counts).
    longer = " ".join([one] * 3)
    _put(space, _text(name="c-three", description=longer), name="c-three", project="ledger-app")
    _put(space, _text(name="worker", description=longer, more="withhold: [web_fetch]\n"), name="worker", project="ledger-app")

    listed = {item.name: item for item in store.listing("ledger-app")}

    # The built-in ones are counted first, then the user's, then the project's.
    assert all(listed[name].valid and listed[name].offered for name in ("boss", "scout", "a-one", "b-two"))
    # The cap is on the list a task agent chooses from, not on what may be used: the ones past it are
    # in effect all the same, and say why they are not offered.
    for name in ("c-three", "worker"):
        assert listed[name].valid and not listed[name].offered
        assert listed[name].error.startswith("not offered: the descriptions of all templates together are longer than")
        assert listed[name].to_json()["valid"] is True and listed[name].to_json()["description"] == longer
    assert "c-three" not in [item.name for item in store.roster("ledger-app")]
    assert [item.name for item in store.roster("ledger-app")][:2] == ["scout", "inspector"]
    # A session of the project's own worker is opened by it, with what it withholds.
    assert store.resolve("worker", "ledger-app").withheld() == frozenset({"web_fetch"})
    assert store.resolve("c-three", "ledger-app").description == longer
    # Without the project's the others fit.
    assert all(item.valid and item.offered for item in store.listing())


def test_what_was_read_is_kept_until_a_file_changes(space, monkeypatch):
    store = TemplateStore(lambda: space)
    path = _put(space, _text(description="First."))
    reads: list[str] = []

    def counted(text: str, **kwargs: str) -> Template:
        reads.append(kwargs["name"])
        return parse_template(text, **kwargs)

    monkeypatch.setattr("nekopower.host.templates.parse_template", counted)

    assert store.resolve("doc-writer").description == "First."
    assert store.resolve("doc-writer").description == "First."
    assert reads == ["doc-writer"]
    before = store.signature()
    path.write_text(_text(description="Second, and longer."), encoding="utf-8")

    assert store.signature() != before
    assert store.resolve("doc-writer").description == "Second, and longer."
    assert reads == ["doc-writer", "doc-writer"]
    path.unlink()
    with pytest.raises(TemplateError):
        store.resolve("doc-writer")


def test_a_file_that_could_not_be_read_for_a_moment_is_read_again_and_not_kept_as_wrong(space, monkeypatch):
    # Held by another program, say. Nothing about the file shows that it can be read again: its name,
    # date and size are as they were. Kept, the preset would be out of use until the file is edited.
    store = TemplateStore(lambda: space)
    path = _put(space, _text(name="general", more="withhold: [bash]\n"), name="general")
    _put(space, _text(name="wrong", more="nope: 1\n"), name="wrong")
    real = Path.read_text
    held = [True]
    reads: list[str] = []

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(self.name)
        if self == path and held[0]:
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    with pytest.raises(TemplateError, match=r"the file cannot be read \(PermissionError\)"):
        store.resolve("general")
    before = store.signature()
    held[0] = False

    assert store.signature() == before
    assert store.resolve("general").withhold == ("bash",)
    # From here on it is kept, as any listing is; so is a file that is wrong for what it says.
    read_so_far = len(reads)
    assert store.resolve("general").withhold == ("bash",) and not store.listing()[-1].valid
    assert len(reads) == read_so_far


def test_a_task_agent_is_started_with_any_template_but_the_lead_and_a_session_of_its_own(space):
    store = TemplateStore(lambda: space)
    _put(space, _text())
    _put(space, _text(name="broken", more="nope: 1\n"), name="broken")

    assert NOT_FOR_CHILDREN == {"boss", "general"}
    assert [item.name for item in store.roster()] == ["worker", "scout", "inspector", "planner", "doc-writer"]


def test_the_project_of_commissions_without_one_has_no_templates_of_its_own(space):
    store = TemplateStore(lambda: space)
    _put(space, _text())
    _put(space, _text(name="stray"), name="stray", project=DEFAULT_PROJECT)

    # A session of no project goes by the user's templates and nothing else, however it is asked for.
    assert [item.name for item in store.listing(DEFAULT_PROJECT)] == [*PRESETS, "doc-writer"]
    assert store.signature(DEFAULT_PROJECT) == store.signature(None)
    assert store.signature(None)[1] == () and store.signature("ledger-app")[1] == ()


def test_a_template_that_cannot_be_looked_at_is_seen_all_the_same(space, monkeypatch):
    # A link that leads nowhere, say, under the name of a preset: listed before it was there, the
    # preset was the built-in one, and that is not what is handed out once the file is there.
    store = TemplateStore(lambda: space)
    assert store.resolve("general").withheld() == frozenset()
    before = store.signature()
    broken = _put(space, _text(name="general", more="read_only: true\n"), name="general")
    real = Path.stat

    def stat(self: Path, **kwargs: object) -> object:
        if self.name == broken.name:
            raise FileNotFoundError(self)
        return real(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)

    assert store.signature() != before and store.signature()[0] == (("general.md", -1, -1),)
    with pytest.raises(TemplateError, match="cannot be read"):
        store.resolve("general")
    assert store.resolve("worker").withheld() == frozenset()


def test_what_was_listed_while_a_file_could_not_be_looked_at_is_not_kept(space, monkeypatch):
    # What stands for a file that cannot be looked at is the same whatever is in the file. Were the
    # file read all the same a moment later and the listing kept, the template read then would be
    # handed out for as long as the file cannot be looked at, whatever it has become since.
    store = TemplateStore(lambda: space)
    path = _put(space, _text(name="general", more="withhold: [bash]\n"), name="general")
    real = Path.stat
    failing = ["once"]
    looked: list[str] = []

    def stat(self: Path, **kwargs: object) -> object:
        if self.name == path.name:
            looked.append(failing[0])
            if failing[0] == "always" or len(looked) == 1:
                raise FileNotFoundError(self)
        return real(self, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)

    assert store.resolve("general").withhold == ("bash",)
    assert looked == ["once", "once"]
    failing[0] = "always"
    assert store.signature()[0] == (("general.md", -1, -1),)
    with pytest.raises(TemplateError, match="cannot be read"):
        store.resolve("general")


def test_a_project_whose_name_is_no_folder_name_goes_by_the_users_templates(space):
    store = TemplateStore(lambda: space)
    _put(space, _text())

    assert [item.name for item in store.listing("../elsewhere")] == [*PRESETS, "doc-writer"]
