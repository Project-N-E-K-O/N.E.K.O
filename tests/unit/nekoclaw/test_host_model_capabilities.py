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
