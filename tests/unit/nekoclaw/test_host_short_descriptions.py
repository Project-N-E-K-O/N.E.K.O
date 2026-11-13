"""Short descriptions written for plugins whose manifest has none.

Listing the plugins never waits for a model: what is already written is
applied, the rest is written in the background, once per plugin and
description, and kept on disk. A plugin is listed by its description until
its short description is there, and when writing it fails.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, make_host, wait_until
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, plugin

pytestmark = pytest.mark.unit


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


def _short(written: Any, path: Any = None) -> Any:
    from nekoclaw.host.short_descriptions import ShortDescriptions

    return ShortDescriptions(lambda: path, written)


# Longer than the coarse screen reads (150 tokens): only then is one written.
LONG = "Looks up the weather forecast of any city for the coming days, hour by hour. " * 12


def _weather(**fields: Any) -> dict[str, Any]:
    return plugin("weather", LONG, **fields)


async def _written(short: Any) -> None:
    await wait_until(lambda: not short._tasks, what="the short descriptions being written")


async def test_a_plugin_without_one_is_listed_by_its_description_until_one_is_written(tmp_path):
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts for a city."]
    short = _short(llm, tmp_path / "short.json")

    first = await short.apply([_weather()])
    # Nothing waited for the model.
    assert first[0]["short_description"] == ""
    await _written(short)
    second = await short.apply([_weather()])

    assert second[0]["short_description"] == "Weather forecasts for a city."
    assert len(llm.short_calls) == 1
    system, user = llm.short_calls[0]
    assert "automation assessment agent" in system["content"]
    assert "Plugin: weather" in user["content"] and "weather forecast of any city" in user["content"]


async def test_the_plugin_given_to_apply_is_not_changed(tmp_path):
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts for a city."]
    short = _short(llm, tmp_path / "short.json")
    await short.apply([_weather()])
    await _written(short)
    original = _weather()

    (listed,) = await short.apply([original])

    assert listed["short_description"] == "Weather forecasts for a city."
    assert original["short_description"] == ""


async def test_what_was_written_is_kept_on_disk_and_read_by_the_next_process(tmp_path):
    path = tmp_path / "short.json"
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts for a city."]
    short = _short(llm, path)
    await short.apply([_weather()])
    await _written(short)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["entries"]["weather"]["short"] == "Weather forecasts for a city."

    later = FakeDecisionLLM()
    (listed,) = await _short(later, path).apply([_weather()])
    assert listed["short_description"] == "Weather forecasts for a city."
    assert later.short_calls == []


async def test_a_description_the_coarse_screen_reads_whole_gets_none_written(tmp_path):
    llm = FakeDecisionLLM()
    llm.short = ["Weather."]
    short = _short(llm, tmp_path / "short.json")

    (listed,) = await short.apply([plugin("weather", "Looks up the weather forecast of any city.")])
    await _written(short)

    # Read whole already; a summary would cost a request and could be longer.
    assert listed["short_description"] == ""
    assert llm.short_calls == []


async def test_a_plugin_that_ships_one_is_left_as_it_is(tmp_path):
    llm = FakeDecisionLLM()
    short = _short(llm, tmp_path / "short.json")

    (listed,) = await short.apply([_weather(short_description="Weather.")])
    await _written(short)

    assert listed["short_description"] == "Weather."
    assert llm.short_calls == []


async def test_a_changed_description_is_written_again(tmp_path):
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts.", "Weather and air quality."]
    short = _short(llm, tmp_path / "short.json")
    await short.apply([_weather()])
    await _written(short)

    changed = plugin("weather", LONG + "It also reports the air quality.")
    (listed,) = await short.apply([changed])
    # The old one does not fit the new description.
    assert listed["short_description"] == ""
    await _written(short)

    (listed,) = await short.apply([changed])
    assert listed["short_description"] == "Weather and air quality."
    assert len(llm.short_calls) == 2


@pytest.mark.parametrize("answer", ["", RuntimeError("no model configured")])
async def test_a_failure_is_not_kept_and_tried_again_only_after_a_while(tmp_path, monkeypatch, answer):
    from config import nekoclaw_settings
    from nekoclaw.host.short_descriptions import ShortDescriptions

    now = [1000.0]
    llm = FakeDecisionLLM()
    llm.short = [answer, "Weather forecasts for a city."]
    path = tmp_path / "short.json"
    short = ShortDescriptions(lambda: path, llm, clock=lambda: now[0])

    (listed,) = await short.apply([_weather()])
    await _written(short)
    # Listed by its description; nothing kept.
    assert listed["short_description"] == "" and not path.exists()

    # Listing again soon after does not ask again.
    await short.apply([_weather()])
    await _written(short)
    assert len(llm.short_calls) == 1

    now[0] += nekoclaw_settings.PLUGIN_SHORT_DESCRIPTION_RETRY_SECONDS
    await short.apply([_weather()])
    await _written(short)
    (listed,) = await short.apply([_weather()])
    assert len(llm.short_calls) == 2
    assert listed["short_description"] == "Weather forecasts for a city."


async def test_listing_while_one_is_being_written_does_not_ask_twice(tmp_path):
    from nekoclaw.host.short_descriptions import ShortDescriptions

    gate = asyncio.Event()
    asked: list[int] = []

    class SlowLLM:
        async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
            asked.append(1)
            await gate.wait()
            return "Weather forecasts for a city."

    short = ShortDescriptions(lambda: tmp_path / "short.json", SlowLLM())

    # Neither listing waits for the model.
    await asyncio.wait_for(short.apply([_weather()]), 1.0)
    await asyncio.wait_for(short.apply([_weather()]), 1.0)
    gate.set()
    await _written(short)

    assert asked == [1]


async def test_without_a_model_only_what_is_on_disk_is_applied(tmp_path):
    from nekoclaw.host.short_descriptions import ShortDescriptions, description_key

    path = tmp_path / "short.json"
    description = _weather()["description"].strip()
    path.write_text(
        json.dumps({"version": 1, "entries": {"weather": {"key": description_key(description), "short": "Weather."}}}),
        encoding="utf-8",
    )
    short = ShortDescriptions(lambda: path, None)

    listed = await short.apply([_weather(), plugin("memo", "Keeps notes. " * 80)])

    assert [item["short_description"] for item in listed] == ["Weather.", ""]
    assert not short._tasks


async def test_an_unreadable_file_counts_as_empty(tmp_path):
    path = tmp_path / "short.json"
    path.write_text("{not json", encoding="utf-8")
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts for a city."]
    short = _short(llm, path)

    await short.apply([_weather()])
    await _written(short)

    assert json.loads(path.read_text(encoding="utf-8"))["entries"]["weather"]["short"] == "Weather forecasts for a city."


async def test_closing_stops_what_is_being_written(tmp_path):
    from nekoclaw.host.short_descriptions import ShortDescriptions

    started = asyncio.Event()

    class StuckLLM:
        async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
            started.set()
            await asyncio.Event().wait()
            return ""

    short = ShortDescriptions(lambda: tmp_path / "short.json", StuckLLM())
    await short.apply([_weather()])
    await asyncio.wait_for(started.wait(), 1.0)

    await asyncio.wait_for(short.aclose(), 1.0)

    assert not short._tasks
    # Nothing new is started once closed.
    await short.apply([_weather()])
    assert not short._tasks


async def test_the_host_lists_plugins_with_the_short_description_written_for_them(server, tmp_path):
    server.add_plugin(_weather())
    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts for a city."]
    host = make_host(tmp_path, server, decision_llm=llm)
    try:
        (first,) = await host.registry.plugins()
        assert first["short_description"] == ""
        await _written(host.short_descriptions)

        (listed,) = await host.registry.plugins()

        assert listed["short_description"] == "Weather forecasts for a city."
        assert (tmp_path / "nekoclaw" / "plugin_short_descriptions.json").exists()
        # A request of its own tier, not counted with the decisions.
        assert llm.calls == [] and len(llm.short_calls) == 1
    finally:
        await host.shutdown()


async def test_a_changed_description_is_not_written_twice_while_the_old_one_is_being_kept(tmp_path):
    import threading

    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts.", "Weather and air quality.", "spare"]
    short = _short(llm, tmp_path / "short.json")
    keeping = threading.Event()
    write = short._write

    def slow_write(written: Any) -> None:
        keeping.wait(5)
        write(written)

    short._write = slow_write
    changed = plugin("weather", LONG + "It also reports the air quality.")

    await short.apply([_weather()])
    await wait_until(lambda: len(llm.short_calls) == 1, what="the first one being asked for")
    # While the first one is being kept, the description changes; listing
    # then asks for nothing yet.
    await short.apply([changed])
    # Whatever it would start gets its turn to run.
    await asyncio.sleep(0.05)
    assert len(llm.short_calls) == 1
    keeping.set()
    await _written(short)

    await short.apply([changed])
    # Listed again while that one is being written: not asked for twice.
    await short.apply([changed])
    await _written(short)

    assert len(llm.short_calls) == 2
    (listed,) = await short.apply([changed])
    assert listed["short_description"] == "Weather and air quality."


async def test_an_answer_longer_than_the_coarse_screen_reads_is_kept_cut(tmp_path):
    from config import AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
    from utils.tokenize import count_tokens

    llm = FakeDecisionLLM()
    llm.short = ["Weather forecasts. " * 100]
    short = _short(llm, tmp_path / "short.json")

    await short.apply([_weather()])
    await _written(short)
    (listed,) = await short.apply([_weather()])

    # Asking again would cost a request each time and could come out as long.
    assert listed["short_description"].startswith("Weather forecasts.")
    assert count_tokens(listed["short_description"]) <= AGENT_PLUGIN_SHORTDESC_MAX_TOKENS
    assert len(llm.short_calls) == 1


async def test_a_failed_plugin_whose_description_changed_is_tried_again_at_once(tmp_path):
    llm = FakeDecisionLLM()
    llm.short = [RuntimeError("busy"), "Weather and air quality."]
    short = _short(llm, tmp_path / "short.json")
    await short.apply([_weather()])
    await _written(short)

    # The author fixed the description: no need to wait out the pause.
    changed = plugin("weather", LONG + "It also reports the air quality.")
    await short.apply([changed])
    await _written(short)
    (listed,) = await short.apply([changed])

    assert len(llm.short_calls) == 2
    assert listed["short_description"] == "Weather and air quality."
