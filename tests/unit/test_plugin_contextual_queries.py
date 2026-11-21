"""Contextual plugin queries on the active TASK / USER_SAID decision path."""

import asyncio
import json

import pytest

from config.prompts.prompts_nekoclaw_brain import TASK_BRAIN_COARSE_SCREEN_PROMPT
from nekoclaw.host.decision import DecisionRequest, LexicalDecisionModel, Triage
from nekoclaw.host.selection import routable_plugins
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, make_host, pick_plugin, wait_status
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, finish, plugin

pytestmark = pytest.mark.unit

LANGUAGES = list(TASK_BRAIN_COARSE_SCREEN_PROMPT)
QUERY = "Mira Vale Paper Lanterns themes"
TASK = "Search for the themes of Mira Vale's Paper Lanterns"
FIELD_DESCRIPTION = "Standalone keywords for the requested subject"


def reference_plugin():
    return plugin(
        "reference_lookup",
        "Search books, songs and other references",
        [entry("search", input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": FIELD_DESCRIPTION}},
            "required": ["query"],
        })],
    )


class CoarseAnswerLLM(FakeDecisionLLM):
    """The real coarse caller sees provider errors and raw malformed replies."""

    def __init__(self, answer, **kwargs):
        super().__init__(**kwargs)
        self.answer = answer

    async def complete(self, tier, messages):
        if tier != "coarse":
            return await super().complete(tier, messages)
        self.calls.append((tier, [dict(message) for message in messages]))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


@pytest.mark.parametrize("lang", LANGUAGES)
@pytest.mark.parametrize("followup", ["你帮我查一下这本书的主题吧", "还没搜到吗", "你用网络搜索呢"])
async def test_resolved_query_survives_assessment_and_dispatch(monkeypatch, tmp_path, lang, followup):
    server = FakePluginServer.install(monkeypatch)

    async def search(args):
        return finish({"reply": "Found the requested subject"})

    server.add_plugin(reference_plugin(), search=search)
    llm = FakeDecisionLLM(fine=[pick_plugin("reference_lookup", "search", {"query": QUERY})])
    host = make_host(tmp_path, server, decision_llm=llm)
    try:
        receipt = await host.dispatch(TASK, user_text=followup, lanlan_name="Lan", lang=lang)
        assert receipt.kind == "accepted"
        done = await wait_status(host, receipt.commission_id, "done")
        assert done.route == "plugin"
        system, user = (message["content"] for message in llm.last("fine"))
        assert FIELD_DESCRIPTION in system
        assert "command/message/query/objective" not in system
        assert user == f"TASK: {TASK}\nUSER_SAID: {followup}"
        (sent,) = server.run_requests
        assert sent["args"]["query"] == QUERY
        assert sent["args"]["_ctx"]["latest_user_request"] == followup
        assert llm.fine[0]["plugin_args"] == {"query": QUERY}
    finally:
        await host.shutdown()


async def test_raw_message_parameter_keeps_its_words_and_newlines(monkeypatch, tmp_path):
    server = FakePluginServer.install(monkeypatch)
    original = "Keep My Words\n包括换行，不要翻译。"
    server.add_plugin(plugin(
        "raw_message",
        "Send a message",
        [entry("send", input_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Original USER_SAID verbatim"}},
            "required": ["message"],
        })],
    ), send=lambda args: _finished())
    llm = FakeDecisionLLM(fine=[pick_plugin("raw_message", "send", {"message": original})])
    host = make_host(tmp_path, server, decision_llm=llm)
    try:
        receipt = await host.dispatch("Send the original message", user_text=original, lanlan_name="Lan")
        await wait_status(host, receipt.commission_id, "done")
        (sent,) = server.run_requests
        assert sent["args"]["message"] == original
        assert sent["args"]["_ctx"]["latest_user_request"] == original
        assert llm.fine[0]["plugin_args"] == {"message": original}
    finally:
        await host.shutdown()


async def _finished():
    return finish({"reply": "sent"})


@pytest.mark.parametrize("lang", LANGUAGES)
async def test_coarse_screen_sends_the_task_and_user_words_only_once(lang):
    llm = FakeDecisionLLM(coarse=["reference_lookup"])
    request = DecisionRequest(
        dispatch_text=TASK, user_text="FOLLOWUP_SENTINEL: 你用网络搜索呢", lang=lang,
        plugins=[reference_plugin()],
    )

    selected = await LexicalDecisionModel(llm)._coarse_screen(request.match_text, request.plugins, lang)

    assert selected == ["reference_lookup"]
    messages = llm.last("coarse")
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1]["content"] == request.match_text
    assert request.match_text not in messages[0]["content"]
    assert sum(message["content"].count("FOLLOWUP_SENTINEL") for message in messages) == 1
    assert "- reference_lookup:" in messages[0]["content"]


def visible_catalog():
    return routable_plugins([
        reference_plugin(),
        plugin("other_visible", "A different reference source"),
        plugin("passive_only", "Not an automatic tool", passive=True),
        plugin("hidden_only", "Not visible", [entry("hidden", metadata={"agent_hidden": True})]),
    ])


@pytest.mark.parametrize("path", ["probe", "select"])
@pytest.mark.parametrize("answer", [
    TimeoutError("offline"), "invalid JSON", "{}", '[{"id": "reference_lookup"}]',
    '["reference_lookup", {}]', "[true]", '["reference_lookup", true]',
])
async def test_failed_screen_keeps_visible_catalog_and_is_not_paid_for_twice(monkeypatch, path, answer):
    monkeypatch.setattr("config.AGENT_PLUGIN_DESC_BM25_THRESHOLD", 1)
    monkeypatch.setattr("nekoclaw.host.decision.stage1_filter", lambda *args, **kwargs: ([], []))
    catalog = visible_catalog()
    llm = CoarseAnswerLLM(answer, fine=[pick_plugin("reference_lookup", "search", {"query": QUERY})])
    model = LexicalDecisionModel(llm)
    request = DecisionRequest(dispatch_text=TASK, user_text="还没搜到吗", plugins=catalog)
    triage = await model.probe(request) if path == "probe" else Triage(kind="new")
    assert triage.kind == "new"
    if path == "probe":
        assert triage.candidate_ids == triage.coarse_ids == [item["id"] for item in catalog]

    selection = await model.select(request, triage)

    assert selection.route == "plugin"
    assert selection.shown_plugin_ids == [item["id"] for item in catalog]
    assert selection.plugin_args == {"query": QUERY}
    assert llm.tiers() == ["coarse", "fine"]
    assert triage.model_calls + selection.model_calls == 2
    assert "passive_only" not in str(llm.calls) and "hidden_only" not in str(llm.calls)


async def test_coarse_screen_preserves_string_and_integer_ids():
    llm = CoarseAnswerLLM('[7, "reference_lookup"]')
    selected = await LexicalDecisionModel(llm)._coarse_screen(
        TASK, [plugin("7", "Numeric identifier"), reference_plugin()], "en",
    )
    assert selected == ["7", "reference_lookup"]


@pytest.mark.parametrize("path", ["probe", "select"])
@pytest.mark.parametrize("ids", [[], ["reference_lookup"]])
async def test_valid_coarse_result_remains_distinct_from_failure(monkeypatch, path, ids):
    monkeypatch.setattr("config.AGENT_PLUGIN_DESC_BM25_THRESHOLD", 1)
    monkeypatch.setattr("nekoclaw.host.decision.stage1_filter", lambda *args, **kwargs: ([], []))
    llm = CoarseAnswerLLM(json.dumps(ids), fine=[pick_plugin("reference_lookup", "search")])
    model = LexicalDecisionModel(llm)
    request = DecisionRequest(dispatch_text="qqqq", plugins=visible_catalog())
    triage = await model.probe(request) if path == "probe" else Triage(kind="new")
    if path == "probe" and not ids:
        assert (triage.kind, triage.coarse_ids, triage.model_calls) == ("none", [], 1)
    else:
        selection = await model.select(request, triage)
        assert selection.shown_plugin_ids == ids
        assert selection.route == ("plugin" if ids else "none")
        assert triage.model_calls + selection.model_calls == (2 if ids else 1)
    assert llm.tiers() == (["coarse", "fine"] if ids else ["coarse"])


@pytest.mark.parametrize("path", ["probe", "select"])
async def test_cancelled_screen_never_starts_fine_selection(monkeypatch, path):
    monkeypatch.setattr("config.AGENT_PLUGIN_DESC_BM25_THRESHOLD", 1)
    monkeypatch.setattr("nekoclaw.host.decision.stage1_filter", lambda *args, **kwargs: ([], []))
    llm = CoarseAnswerLLM(asyncio.CancelledError())
    model = LexicalDecisionModel(llm)
    request = DecisionRequest(dispatch_text="qqqq", plugins=visible_catalog())

    with pytest.raises(asyncio.CancelledError):
        if path == "probe":
            await model.probe(request)
        else:
            await model.select(request, Triage(kind="new"))
    assert llm.tiers() == ["coarse"]


async def test_bounded_task_and_user_fields_never_splice_in_old_ledger_text(monkeypatch, tmp_path):
    from config.nekoclaw_settings import DISPATCH_TEXT_MAX_TOKENS, DISPATCH_USER_TEXT_MAX_TOKENS
    from utils.tokenize import count_tokens, truncate_to_tokens

    monkeypatch.setattr("config.AGENT_PLUGIN_DESC_BM25_THRESHOLD", 1)
    matched = []

    def local_candidates(text, plugins, **kwargs):
        matched.append(text)
        return [plugins[0]], []

    monkeypatch.setattr("nekoclaw.host.decision.stage1_filter", local_candidates)
    server = FakePluginServer.install(monkeypatch)
    server.add_plugin(reference_plugin(), search=lambda args: _finished())
    llm = FakeDecisionLLM(
        coarse=["reference_lookup"], fine=[pick_plugin("reference_lookup", "search", {"query": QUERY})],
    )
    host = make_host(tmp_path, server, decision_llm=llm)
    task = "CURRENT_TASK " + "task_detail " * 10000
    said = "CURRENT_USER " + "user_detail " * 10000
    expected_task = truncate_to_tokens(task.strip(), DISPATCH_TEXT_MAX_TOKENS)
    expected_said = truncate_to_tokens(said.strip(), DISPATCH_USER_TEXT_MAX_TOKENS)
    old = "OLD_LEDGER_TOPIC"
    try:
        await host.ledger.create(
            lanlan_name="Lan", title=old, dispatch_text=old, user_text=old,
            source="dispatch", status="working", route="plugin",
        )
        receipt = await host.dispatch(task, user_text=said, lanlan_name="Lan")
        assert receipt.kind == "accepted"
        await wait_status(host, receipt.commission_id, "done")

        coarse = llm.last("coarse")[1]["content"]
        fine = llm.last("fine")[1]["content"]
        assert matched and all(text == f"{expected_said}\n{expected_task}" for text in matched)
        assert coarse == f"{expected_said}\n{expected_task}"
        assert fine.split("\nACTIVE_TASKS:", 1)[0] == f"TASK: {expected_task}\nUSER_SAID: {expected_said}"
        assert old in fine.split("\nACTIVE_TASKS:", 1)[1]
        assert old not in coarse and all(old not in text for text in matched)
        assert count_tokens(expected_task) <= DISPATCH_TEXT_MAX_TOKENS == 300
        assert count_tokens(expected_said) <= DISPATCH_USER_TEXT_MAX_TOKENS == 300
        assert "LATEST_USER_REQUEST" not in coarse
        assert server.run_requests[0]["args"]["_ctx"]["latest_user_request"] == expected_said
    finally:
        await host.shutdown()
