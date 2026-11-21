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

"""What the host remembers about whether a model takes pictures."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host.model_capabilities import ModelCapabilityStore, model_key

URL = "https://api.example.com/v1"


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "nekoclaw" / "model_capabilities.json"


def _write(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")


async def test_a_model_nothing_is_known_about_sees_pictures(path):
    store = ModelCapabilityStore(path)

    assert await store.sees_images(URL, "vision-9") is True
    # Asking writes nothing down.
    assert not path.exists()


async def test_a_refusal_is_new_the_first_time_and_known_after_that(path):
    store = ModelCapabilityStore(path)

    assert await store.note_images_refused(URL, "text-only") is True
    assert await store.note_images_refused(URL, "text-only") is False

    assert await store.sees_images(URL, "text-only") is False
    # Another model at that address, and that name at another address, are not touched.
    assert await store.sees_images(URL, "vision-9") is True
    assert await store.sees_images("https://other.example.com/v1", "text-only") is True
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert list(saved["models"]) == ["https://api.example.com/v1|text-only"]
    entry = saved["models"]["https://api.example.com/v1|text-only"]
    assert (entry["images"], entry["source"]) == (False, "refused")
    assert isinstance(entry["noted_at"], int)


async def test_two_refusals_at_once_are_one_piece_of_news(path):
    store = ModelCapabilityStore(path)

    answers = await asyncio.gather(
        store.note_images_refused(URL, "text-only"), store.note_images_refused(URL, "text-only")
    )

    assert sorted(answers) == [False, True]


async def test_what_was_noted_survives_a_new_store_on_the_same_file(path):
    await ModelCapabilityStore(path).note_images_refused(URL, "text-only")
    await ModelCapabilityStore(path).note_images_refused(URL, "older-text-only")

    later = ModelCapabilityStore(path)

    assert await later.sees_images(URL, "text-only") is False
    assert await later.sees_images(URL, "older-text-only") is False
    assert await later.note_images_refused(URL, "text-only") is False


async def test_an_entry_set_by_hand_to_true_is_not_overwritten_and_still_sees_pictures(path):
    _write(path, {"version": 1, "models": {model_key(URL, "vision-9"): {"images": True}}})
    before = path.read_bytes()
    store = ModelCapabilityStore(path)

    # One refusal may have had another cause; the user knows the model.
    assert await store.note_images_refused(URL, "vision-9") is False

    assert await store.sees_images(URL, "vision-9") is True
    assert path.read_bytes() == before


async def test_an_entry_set_by_hand_to_false_keeps_pictures_from_the_model(path):
    _write(path, {"version": 1, "models": {model_key(URL, "text-only"): {"images": False}}})
    store = ModelCapabilityStore(path)

    assert await store.sees_images(URL, "text-only") is False
    assert await store.note_images_refused(URL, "text-only") is False


async def test_an_entry_that_says_nothing_about_pictures_counts_as_seeing_them_until_a_refusal(path):
    _write(path, {"version": 1, "models": {model_key(URL, "new-model"): {"note": "added last week"}}})
    store = ModelCapabilityStore(path)

    assert await store.sees_images(URL, "new-model") is True
    assert await store.note_images_refused(URL, "new-model") is True
    assert await store.sees_images(URL, "new-model") is False


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        "[]",
        '"just a string"',
        '{"version": 1}',
        '{"models": ["https://api.example.com/v1|text-only"]}',
        # An entry has to be an object to say anything.
        '{"models": {"https://api.example.com/v1|text-only": false}}',
    ],
)
async def test_a_malformed_file_counts_as_empty(path, content):
    _write(path, content)
    store = ModelCapabilityStore(path)

    assert await store.sees_images(URL, "text-only") is True
    # What is learned next is written as a well-formed file again.
    assert await store.note_images_refused(URL, "text-only") is True
    assert await ModelCapabilityStore(path).sees_images(URL, "text-only") is False


async def test_a_file_that_cannot_be_read_counts_as_empty_and_what_is_learned_is_kept_in_memory(path):
    # A directory where the file should be: it can be neither read nor written.
    path.mkdir(parents=True)
    store = ModelCapabilityStore(path)

    assert await store.sees_images(URL, "text-only") is True
    assert await store.note_images_refused(URL, "text-only") is True

    assert await store.sees_images(URL, "text-only") is False
    assert await store.note_images_refused(URL, "text-only") is False
    assert path.is_dir()


def test_the_key_ignores_a_trailing_slash_and_the_case_of_the_address():
    assert model_key("https://API.Example.com/v1/", "Vision-9") == "https://api.example.com/v1|Vision-9"
    assert model_key("  https://api.example.com/v1  ", " Vision-9 ") == "https://api.example.com/v1|Vision-9"
    # The name is kept as it is written: two names may differ in case only.
    assert model_key(URL, "Vision-9") != model_key(URL, "vision-9")
    # A model without an address of its own is still a key.
    assert model_key(None, "fake-model") == "|fake-model"


async def test_a_refusal_noted_under_one_spelling_of_the_address_holds_for_the_others(path):
    store = ModelCapabilityStore(path)

    assert await store.note_images_refused("https://API.example.com/v1/", "text-only") is True

    assert await store.sees_images("https://api.example.com/v1", "text-only") is False
    assert await store.note_images_refused("https://api.EXAMPLE.com/v1", "text-only") is False
    assert await store.sees_images("https://api.example.com/v1", "Text-Only") is True


async def test_the_file_is_read_on_first_use_and_only_once(path):
    _write(path, {"version": 1, "models": {model_key(URL, "text-only"): {"images": False}}})
    asked: list[int] = []

    def where() -> Path:
        asked.append(1)
        return path

    store = ModelCapabilityStore(where)
    # Building the store looks at nothing.
    assert asked == []

    assert await store.sees_images(URL, "text-only") is False
    assert await store.sees_images(URL, "vision-9") is True

    assert len(asked) == 1


from nekoclaw.host import model_capabilities as capabilities_module  # noqa: E402
from tests.fake_clock import patch_module_clock  # noqa: E402

# ---------------------------------------------------------------------------
# Why, since when, and until when
# ---------------------------------------------------------------------------


async def test_what_the_provider_said_is_kept_with_the_entry_and_can_be_shown(path, monkeypatch):
    import config.nekoclaw_settings as settings

    patch_module_clock(monkeypatch, capabilities_module, time=lambda: 1_800_000_000)
    store = ModelCapabilityStore(path)
    said = "Error code: 400 - This model does not support\n   image input. " + "x" * 600
    assert await store.note_images_refused(URL, "text-only", said) is True

    told = await store.describe(URL, "text-only")
    assert told == {
        "sees": False,
        "source": "refused",
        # One line, and no longer than is of use.
        "reason": ("Error code: 400 - This model does not support image input. " + "x" * 600)[:300],
        "noted_at": 1_800_000_000,
        "lapses_at": 1_800_000_000 + settings.MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS,
    }
    assert await store.describe(URL, "vision-9") == {"sees": True}
    # Read back from the file by a store started later.
    assert await ModelCapabilityStore(path).describe(URL, "text-only") == told


async def test_a_refusal_is_believed_for_a_while_and_then_the_model_is_tried_again(path, monkeypatch):
    import config.nekoclaw_settings as settings

    now = [1_800_000_000]
    patch_module_clock(monkeypatch, capabilities_module, time=lambda: now[0])
    store = ModelCapabilityStore(path)
    await store.note_images_refused(URL, "text-only", "first refusal")
    assert settings.MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS == 7 * 24 * 3600

    now[0] += settings.MODEL_IMAGE_REFUSAL_REMEMBERED_SECONDS - 1
    assert await store.sees_images(URL, "text-only") is False
    # A second refusal within that time changes nothing.
    assert await store.note_images_refused(URL, "text-only", "again") is False

    now[0] += 2
    # Misjudged, or able to see by now: it gets its picture tools back by itself.
    assert await store.sees_images(URL, "text-only") is True
    assert await store.describe(URL, "text-only") == {"sees": True}
    # Still taking none, it is refused once more and written down anew.
    assert await store.note_images_refused(URL, "text-only", "refused again") is True
    assert (await store.describe(URL, "text-only"))["reason"] == "refused again"
    assert (await store.describe(URL, "text-only"))["noted_at"] == now[0]


async def test_what_a_hand_wrote_never_lapses(path, monkeypatch):
    now = [1_800_000_000]
    patch_module_clock(monkeypatch, capabilities_module, time=lambda: now[0])
    _write(
        path,
        {
            "version": 1,
            "models": {model_key(URL, "text-only"): {"images": False}, model_key(URL, "vision-9"): {"images": True}},
        },
    )
    store = ModelCapabilityStore(path)
    now[0] += 10 * 365 * 24 * 3600

    assert await store.sees_images(URL, "text-only") is False
    assert await store.describe(URL, "text-only") == {"sees": False, "source": "set by hand"}
    assert await store.describe(URL, "vision-9") == {"sees": True}
    assert await store.note_images_refused(URL, "vision-9", "refused") is False


def test_the_case_of_the_scheme_and_the_host_is_dropped_and_that_of_the_path_is_kept() -> None:
    assert model_key("HTTPS://API.Example.COM/v1/", "Vision-1") == "https://api.example.com/v1|Vision-1"
    # Two paths that differ by case may be two services: what one refused says nothing of the other.
    assert model_key("https://gateway.example.com/TeamA/v1", "m") != model_key("https://gateway.example.com/teama/v1", "m")
    assert model_key("https://gateway.example.com/TeamA/v1", "m") == "https://gateway.example.com/TeamA/v1|m"
    # A name and a password in the address are kept as written; the port goes with the host.
    assert model_key("http://Ada:SeCret@LocalHost:8080/V1", "m") == "http://Ada:SeCret@localhost:8080/V1|m"
    # An address without a scheme, or none at all.
    assert model_key("LocalHost:11434/Api", "m") == "localhost:11434/Api|m"
    assert model_key(None, None) == "|" and model_key("  ", " m ") == "|m"


def test_an_address_within_an_address_does_not_move_where_the_scheme_ends() -> None:
    # What follows the path is part of the address as written, upper case included.
    key = model_key("HTTPS://Gateway.Example.com/Route?Upstream=HTTPS://API.Example.com/V1", "m")
    assert key == "https://gateway.example.com/Route?Upstream=HTTPS://API.Example.com/V1|m"
    # Without a scheme of its own, one further on is not taken for it.
    assert model_key("Gateway.Example.com/Route?Upstream=https://API.Example.com", "m") == (
        "gateway.example.com/Route?Upstream=https://API.Example.com|m"
    )


def test_the_host_ends_where_the_query_or_the_fragment_begins_as_well() -> None:
    # A key in the address is part of it as written.
    assert model_key("HTTPS://API.Example.com?Token=AbC", "m") == "https://api.example.com?Token=AbC|m"
    assert model_key("https://api.example.com?token=AbC", "m") != model_key("https://api.example.com?token=abc", "m")
    assert model_key("https://API.example.com#Section", "m") == "https://api.example.com#Section|m"
    assert model_key("https://API.example.com:8443?A=B/C", "m") == "https://api.example.com:8443?A=B/C|m"


def test_only_what_is_a_scheme_is_taken_for_one() -> None:
    # No scheme was written; the address further on has one.
    assert model_key("Gateway:8080?Upstream=https://API.example.com", "m") == "gateway:8080?Upstream=https://API.example.com|m"
    # Schemes may hold digits, "+", "-" and "." after their first letter.
    assert model_key("Custom+V2.X-1://HOST/Path", "m") == "custom+v2.x-1://host/Path|m"


# ---------------------------------------------------------------------------
# The context window
# ---------------------------------------------------------------------------


async def test_a_model_nothing_is_known_about_gets_the_window_of_the_setting(path):
    import config.nekoclaw_settings as settings

    store = ModelCapabilityStore(path)

    assert settings.TASK_AGENT_CONTEXT_WINDOW == 200_000
    assert await store.context_window(URL, "any") == 200_000
    assert await store.describe_window(URL, "any") == {
        "window": 200_000,
        "default": 200_000,
        "set": None,
        # The owner's numbers (2026-11-18): a preset of 200,000, summed up from 170,000 on.
        "compact_at": 170_000,
        "compact_at_set": None,
    }
    assert settings.TASK_AGENT_COMPACT_AT_TOKENS == 170_000 and await store.compact_at(URL, "any") == 170_000
    assert not path.exists()


async def test_a_model_whose_window_is_written_down_is_managed_for_that(path):
    import config.nekoclaw_settings as settings

    store = ModelCapabilityStore(path)
    free = "https://free.example.com/text/v1"

    # The free tier's agent model cuts a long request off without saying so: nothing would
    # ever be learned of its window, and a session managed for the setting would lose part
    # of every request above what the model takes. It is the model of a user who set nothing.
    assert settings.TASK_AGENT_KNOWN_CONTEXT_WINDOWS == {"free-agent-model": 128_000}
    assert await store.context_window(free, "free-agent-model") == 128_000
    assert await store.describe_window(free, "free-agent-model") == {
        "window": 128_000,
        "default": 128_000,
        "set": None,
        # The same part of its window as the presets are of each other.
        "compact_at": 108_800,
        "compact_at_set": None,
    }
    # By name, whatever the address; and no other model.
    assert await store.context_window(URL, " free-agent-model ") == 128_000
    assert await store.context_window(free, "free-agent-model-2") == 200_000
    assert await store.context_window(free, None) == 200_000

    # What the user sets for it counts, as for any model; taken back, it is its own again.
    assert await store.set_context_window(free, "free-agent-model", 64_000) == 64_000
    assert await store.set_context_window(free, "free-agent-model", 200_000) == 200_000
    assert await store.set_context_window(free, "free-agent-model", None) == 128_000
    # And what a provider states for it still makes it smaller.
    assert await store.note_context_window(free, "free-agent-model", 100_000) is True
    assert await store.context_window(free, "free-agent-model") == 100_000


async def test_the_user_sets_where_a_models_conversations_are_summed_up_from_and_takes_it_back(path):
    store = ModelCapabilityStore(path)

    assert await store.set_compact_at(URL, "m", 150_000) == 150_000
    assert await store.compact_at(URL, "m") == 150_000
    assert (await store.describe_window(URL, "m"))["compact_at_set"] == 150_000
    # For that model only, and kept across a restart.
    assert await store.compact_at(URL, "other") == 170_000
    assert await ModelCapabilityStore(path).compact_at(URL, "m") == 150_000

    # Of the window a session is in fact started with, when the model's configuration names one.
    assert await store.compact_at(URL, "other", window=1_000_000) == 850_000
    assert await store.compact_at(URL, "m", window=1_000_000) == 150_000
    assert await store.compact_at(URL, "m", window=100_000) == 85_000

    # A point that does not lie inside the window is not used: the model's window turned out
    # smaller than the number was meant for, and it is summed up in proportion.
    assert await store.note_context_window(URL, "m", 128_000) is True
    assert await store.compact_at(URL, "m") == 108_800
    assert await store.set_compact_at(URL, "m", 100_000) == 100_000
    assert await store.set_compact_at(URL, "m", 128_000) == 108_800

    # Taken back, it is the preset part of the window again; the rest of what is known stays.
    assert await store.set_compact_at(URL, "m", None) == 108_800
    assert (await store.describe_window(URL, "m"))["compact_at_set"] is None
    assert await store.context_window(URL, "m") == 128_000


@pytest.mark.parametrize("tokens", [0, 999, -5, True, "150000", float("nan"), float("inf")])
async def test_a_point_that_is_no_number_of_tokens_is_not_set(path, tokens):
    store = ModelCapabilityStore(path)

    with pytest.raises(ValueError):
        await store.set_compact_at(URL, "m", tokens)

    assert await store.compact_at(URL, "m") == 170_000
    assert not path.exists()


async def test_the_user_is_to_be_told_of_a_models_smaller_window_once(path):
    store = ModelCapabilityStore(path)

    # Nothing learned: nothing to tell.
    assert await store.claim_window_reminder(URL, "m") is None
    assert await store.note_context_window(URL, "m", 131_072) is True

    # To whoever asks first; a second one while the first is telling them gets nothing.
    assert await store.claim_window_reminder(URL, "m") == 131_072
    assert await store.claim_window_reminder(URL, "m") is None
    # Not on record before they were told: a process that ends in between tells them the
    # next time, a second time at the worst, and never not at all.
    assert "reminded" not in await store.describe_window(URL, "m")
    assert await ModelCapabilityStore(path).claim_window_reminder(URL, "m") == 131_072

    await store.confirm_window_reminder(URL, "m", 131_072)
    # On record for whatever shows the model's settings later.
    described = await store.describe_window(URL, "m")
    assert described["reminded"] == 131_072 and described["reminded_at"] > 0
    # Once for the model: not again when a smaller one is learned, nor after a restart.
    assert await store.note_context_window(URL, "m", 64_000) is True
    assert await store.claim_window_reminder(URL, "m") is None
    assert await ModelCapabilityStore(path).claim_window_reminder(URL, "m") is None
    # For that model only.
    assert await store.note_context_window(URL, "other", 32_768) is True
    assert await store.claim_window_reminder(URL, "other") == 32_768

    # The user was not told after all (it could not be sent): told the next time.
    store.unclaim_window_reminder(URL, "other")
    assert "reminded" not in await store.describe_window(URL, "other")
    assert await store.claim_window_reminder(URL, "other") == 32_768
    await store.confirm_window_reminder(URL, "other", 32_768)
    assert await store.claim_window_reminder(URL, "other") is None

    # The user set the window themselves: what they were told is done with, and a model
    # that turns out smaller again after that is told of again.
    await store.set_context_window(URL, "m", 100_000)
    assert "reminded" not in await store.describe_window(URL, "m")
    assert await store.claim_window_reminder(URL, "m") == 64_000

    # They set it once more while they are being told: what is on its way is of a window
    # they have looked at since, and is not put on record as told.
    await store.set_context_window(URL, "m", 90_000)
    await store.confirm_window_reminder(URL, "m", 64_000)
    assert "reminded" not in await store.describe_window(URL, "m")
    assert await store.note_context_window(URL, "m", 48_000) is True
    assert await store.claim_window_reminder(URL, "m") == 48_000
    await store.confirm_window_reminder(URL, "m", 48_000)
    assert (await store.describe_window(URL, "m"))["reminded"] == 48_000


async def test_the_user_sets_a_window_for_one_model_and_takes_it_back(path):
    store = ModelCapabilityStore(path)

    assert await store.set_context_window(URL, "small-local", 32_000) == 32_000
    assert await store.context_window(URL, "small-local") == 32_000
    # Larger than the setting is the user's to choose as well.
    assert await store.set_context_window(URL, "huge", 1_000_000) == 1_000_000
    # Another model, and that name at another address, keep the setting.
    assert await store.context_window(URL, "other") == 200_000
    assert await store.context_window("http://192.168.1.5:8000/v1", "small-local") == 200_000
    # Read back from the file by a store started later.
    assert await ModelCapabilityStore(path).context_window(URL, "small-local") == 32_000
    assert (await store.describe_window(URL, "small-local"))["set"] == 32_000

    assert await store.set_context_window(URL, "small-local", None) == 200_000
    saved = json.loads(path.read_text(encoding="utf-8"))["models"]
    # Nothing is left of an entry that says nothing.
    assert model_key(URL, "small-local") not in saved and model_key(URL, "huge") in saved


@pytest.mark.parametrize("tokens", [0, -5, True, "32000", float("nan"), float("inf")])
async def test_a_window_that_is_no_number_of_tokens_is_not_set(path, tokens):
    store = ModelCapabilityStore(path)
    with pytest.raises(ValueError):
        await store.set_context_window(URL, "m", tokens)
    assert await store.context_window(URL, "m") == 200_000


async def test_no_window_is_smaller_than_a_session_can_be_managed_for(path):
    import config.nekoclaw_settings as settings

    store = ModelCapabilityStore(path)
    assert await store.set_context_window(URL, "tiny", 512) == settings.TASK_AGENT_CONTEXT_WINDOW_MIN == 4_000
    assert await store.note_context_window(URL, "tiny-too", 2_048) is True
    assert await store.context_window(URL, "tiny-too") == 4_000


async def test_the_window_a_provider_stated_counts_when_it_is_the_smaller_one(path, monkeypatch):
    import config.nekoclaw_settings as settings

    now = [1_800_000_000]
    patch_module_clock(monkeypatch, capabilities_module, time=lambda: now[0])
    store = ModelCapabilityStore(path)

    assert await store.note_context_window(URL, "local", 32_768) is True
    assert await store.context_window(URL, "local") == 32_768
    assert await store.describe_window(URL, "local") == {
        "window": 32_768,
        "default": 200_000,
        "set": None,
        "compact_at": 27_853,
        "compact_at_set": None,
        "learned": 32_768,
        "learned_at": 1_800_000_000,
        "lapses_at": 1_800_000_000 + settings.MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS,
    }
    # The same or a larger number told again changes nothing; a smaller one does.
    assert await store.note_context_window(URL, "local", 32_768) is False
    assert await store.note_context_window(URL, "local", 65_536) is False
    assert await store.note_context_window(URL, "local", 16_384) is True
    assert await store.context_window(URL, "local") == 16_384

    # What the user set counts where it is smaller, and the provider's where that is.
    await store.set_context_window(URL, "local", 8_000)
    assert await store.context_window(URL, "local") == 8_000
    await store.set_context_window(URL, "local", 100_000)
    assert await store.context_window(URL, "local") == 16_384
    # A provider that states a window larger than the setting does not widen it.
    assert await store.note_context_window(URL, "big", 1_000_000) is True
    assert await store.context_window(URL, "big") == 200_000
    # Read back from the file by a store started later.
    assert await ModelCapabilityStore(path).context_window(URL, "local") == 16_384


@pytest.mark.parametrize("stated", [None, "many", 0, -1, True, float("nan")])
async def test_a_statement_that_names_no_number_teaches_nothing(path, stated):
    store = ModelCapabilityStore(path)
    assert await store.note_context_window(URL, "local", stated) is False
    assert await store.context_window(URL, "local") == 200_000
    assert not path.exists()


async def test_a_stated_window_is_believed_for_a_while_and_then_the_setting_counts_again(path, monkeypatch):
    import config.nekoclaw_settings as settings

    now = [1_800_000_000]
    patch_module_clock(monkeypatch, capabilities_module, time=lambda: now[0])
    store = ModelCapabilityStore(path)
    await store.note_context_window(URL, "local", 32_768)
    await store.set_context_window(URL, "local", 100_000)

    now[0] += settings.MODEL_CONTEXT_WINDOW_REMEMBERED_SECONDS - 1
    assert await store.context_window(URL, "local") == 32_768
    now[0] += 2
    # What the user set does not lapse; what was learned does.
    assert await store.context_window(URL, "local") == 100_000
    assert await store.describe_window(URL, "local") == {
        "window": 100_000,
        "default": 200_000,
        "set": 100_000,
        "compact_at": 85_000,
        "compact_at_set": None,
    }
    # Stated again, a larger number is taken now: the old one is no longer believed.
    assert await store.note_context_window(URL, "local", 65_536) is True
    assert await store.context_window(URL, "local") == 65_536


async def test_what_is_known_of_the_window_and_of_pictures_do_not_overwrite_each_other(path):
    store = ModelCapabilityStore(path)
    await store.set_context_window(URL, "local", 32_000)
    await store.note_context_window(URL, "local", 16_000)

    assert await store.note_images_refused(URL, "local", "no pictures") is True
    assert await store.context_window(URL, "local") == 16_000
    assert (await store.describe_window(URL, "local"))["set"] == 32_000
    assert await store.sees_images(URL, "local") is False

    await store.set_context_window(URL, "local", None)
    assert await store.sees_images(URL, "local") is False
    assert await store.context_window(URL, "local") == 16_000
    # A model that is only known by its window is taken to see pictures.
    await store.set_context_window(URL, "other", 64_000)
    assert await store.sees_images(URL, "other") is True
    assert await store.describe(URL, "other") == {"sees": True}
