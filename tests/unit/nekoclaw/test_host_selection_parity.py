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

"""The ported selection against the analyzer's, on generated plugin lists.

``test_contract_plugin_selection.py`` states the rules one by one. This file
compares the two implementations wholesale: for many generated catalogs and
requests the first stage keeps the same plugins, the description lines are the
same text, and the fine selection is shown the same candidates. It goes away
together with the analyzer.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from tests.unit.nekoclaw.plugin_server_fake import entry, plugin
from tests.unit.nekoclaw.selection_impls import AnalyzerSelection, NekoclawSelection, ScriptedSelectionModel

WORDS = [
    "weather", "lamp", "music", "mail", "calendar", "train", "timetable", "notes", "photo", "search",
    "天气", "台灯", "音乐", "邮件", "日历", "火车", "时刻表", "笔记", "照片", "搜索", "提醒", "下载",
]
REQUESTS = [
    "查一下上海明天的天气",
    "play some music and dim the lamp",
    "把照片发到邮箱",
    "when is the next train",
    "提醒我下午三点开会",
    "zzz qqq",
    "search my notes for the timetable",
    "下载这首音乐",
]


def _catalog(rng: random.Random, size: int, long: bool) -> list[dict[str, Any]]:
    plugins = []
    for number in range(size):
        words = rng.sample(WORDS, rng.randint(1, 4))
        filler = " ".join(f"pad{number}x{n}" for n in range(rng.randint(40, 70))) if long else ""
        entries = []
        for entry_number in range(rng.randint(0, 3)):
            metadata: dict[str, Any] = {}
            roll = rng.random()
            if roll < 0.1:
                metadata["agent_hidden"] = True
            elif roll < 0.2:
                metadata[rng.choice(["agent_auto", "agent_exposed", "llm_exposed"])] = False
            schema = None
            if rng.random() < 0.5:
                schema = {
                    "type": "object",
                    "properties": {
                        rng.choice(["query", "city", "count"]): {"type": rng.choice(["string", "integer"])},
                        "mode": {"type": "string", "enum": [f"m{n}" for n in range(rng.randint(1, 15))]},
                    },
                    "required": ["mode"] if rng.random() < 0.5 else [],
                }
            entries.append(
                entry(f"e{entry_number}", " ".join(rng.sample(WORDS, 2)), input_schema=schema, metadata=metadata)
            )
        described = plugin(
            f"p{number:02d}",
            f"{' '.join(words)} {filler}".strip(),
            entries,
            short_description=" ".join(rng.sample(WORDS, 2)) if rng.random() < 0.6 else "",
            keywords=rng.sample(WORDS, rng.randint(0, 2)) if rng.random() < 0.5 else [],
            passive=rng.random() < 0.1,
        )
        if rng.random() < 0.1:
            del described["entries"]
        plugins.append(described)
    return plugins


@pytest.mark.parametrize("seed", range(12))
def test_local_pieces_agree(seed, monkeypatch):
    rng = random.Random(seed)
    old, new = AnalyzerSelection(monkeypatch), NekoclawSelection(monkeypatch)
    plugins = _catalog(rng, rng.randint(1, 30), long=bool(seed % 2))

    assert new.desc_lines(plugins) == old.desc_lines(plugins)
    for item in plugins:
        assert new.visible_entries(item) == old.visible_entries(item)
        assert new.corpus_text(item) == old.corpus_text(item)
    for text in REQUESTS:
        assert new.stage1(text, plugins) == old.stage1(text, plugins)
        assert new.stage1(text, plugins, top_k=3) == old.stage1(text, plugins, top_k=3)


@pytest.mark.parametrize("seed", range(8))
async def test_fine_selection_is_shown_the_same_candidates(seed, monkeypatch):
    rng = random.Random(100 + seed)
    plugins = _catalog(rng, rng.randint(3, 35), long=bool(seed % 2))
    coarse = [f"p{rng.randrange(40):02d}" for _ in range(rng.randint(0, 4))]

    for text in REQUESTS:
        old_run = await AnalyzerSelection(monkeypatch).select(text, plugins, ScriptedSelectionModel(coarse=coarse))
        new_run = await NekoclawSelection(monkeypatch).select(text, plugins, ScriptedSelectionModel(coarse=coarse))

        # The same stages up to the fine selection, the same coarse-screen request...
        assert new_run.stages[:1] == old_run.stages[:1]
        old_coarse = [call for call in old_run.calls if call.stage == "coarse"]
        new_coarse = [call for call in new_run.calls if call.stage == "coarse"]
        assert [(call.system, call.user) for call in new_coarse] == [(call.system, call.user) for call in old_coarse]
        # ...and the same plugins shown, as the same text.
        if old_run.plugins_desc:
            assert new_run.plugins_desc == old_run.plugins_desc
        else:
            assert new_run.calls == []
