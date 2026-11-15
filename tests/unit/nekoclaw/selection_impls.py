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

"""One face for every implementation of plugin selection.

The contract tests in ``test_contract_plugin_selection.py`` state what plugin
selection does and run against each implementation listed in
``SELECTION_IMPLS``. An implementation adapts its own code to the methods of
``NekoclawSelection`` below; the tests never look behind them. Until the
analyzer was removed the same tests ran against it as well, which is how the
rules were carried over unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_SENTINEL = "\x00PLUGINS_DESC\x00"


@dataclass
class ModelCall:
    """One model request made while selecting."""

    stage: str  # "coarse" or "fine"
    system: str
    user: str


@dataclass
class SelectionRun:
    """What one pass through selection did."""

    # The plugin descriptions put into the fine-selection prompt.
    plugins_desc: str
    calls: list[ModelCall] = field(default_factory=list)
    # The outcome, reduced to what every implementation reports.
    plugin_id: str | None = None
    entry_id: str | None = None
    plugin_args: dict[str, Any] | None = None
    executable: bool = False
    reason: str = ""

    @property
    def stages(self) -> list[str]:
        return [call.stage for call in self.calls]


def _between(text: str, template: str) -> str:
    """Return what ``template`` holds in place of the sentinel inside ``text``."""
    prefix, suffix = template.split(_SENTINEL)
    assert text.startswith(prefix), "the prompt does not start like its template"
    assert text.endswith(suffix), "the prompt does not end like its template"
    return text[len(prefix) : len(text) - len(suffix)]


class _Reply:
    def __init__(self, content: str) -> None:
        self.content = content
        self.response_metadata: dict[str, Any] = {}


class ScriptedSelectionModel:
    """Stands in for both model tiers and records every request."""

    def __init__(self, *, coarse: list[str] | None = None, fine: list[dict[str, Any]] | None = None) -> None:
        self._coarse = list(coarse or [])
        self._fine = list(fine or [{"has_task": False, "can_execute": False, "reason": "none"}])
        self.calls: list[ModelCall] = []

    def reply(self, stage: str, messages: list[dict[str, Any]]) -> _Reply:
        system = str(messages[0]["content"])
        user = str(messages[-1]["content"])
        self.calls.append(ModelCall(stage=stage, system=system, user=user))
        if stage == "coarse":
            return _Reply(json.dumps(self._coarse))
        index = min(sum(1 for call in self.calls if call.stage == "fine") - 1, len(self._fine) - 1)
        return _Reply(json.dumps(self._fine[index], ensure_ascii=False))


class NekoclawSelection:
    """The new selection: ``nekoclaw.host.selection`` and ``nekoclaw.host.decision``."""

    name = "nekoclaw"

    def __init__(self, monkeypatch: Any) -> None:
        del monkeypatch

    # -- pure pieces -------------------------------------------------------

    def entry_hidden(self, entry: Any) -> bool:
        from nekoclaw.host.selection import is_entry_hidden

        return is_entry_hidden(entry)

    def visible_entries(self, plugin: Any) -> list[dict[str, Any]]:
        from nekoclaw.host.selection import visible_entries

        return visible_entries(plugin)

    def desc_lines(self, plugins: list[dict[str, Any]]) -> list[str]:
        from nekoclaw.host.selection import build_desc_lines

        return build_desc_lines(plugins)

    def keyword_match(self, text: str, keywords: list[str]) -> bool:
        from nekoclaw.host.selection import match_keywords

        return match_keywords(text, keywords)

    def stage1(self, text: str, plugins: list[dict[str, Any]], top_k: int = 10) -> tuple[list[str], list[str]]:
        from nekoclaw.host.selection import stage1_filter

        filtered, keyword_ids = stage1_filter(text, plugins, bm25_top_k=top_k)
        return [str(item.get("id")) for item in filtered], keyword_ids

    def corpus_text(self, plugin: dict[str, Any]) -> str:
        from nekoclaw.host.selection import plugin_corpus_text

        return plugin_corpus_text(plugin)

    def annotate(self, plugins_desc: str, keyword_ids: list[str]) -> str:
        from nekoclaw.host.selection import annotate_keyword_hits

        return annotate_keyword_hits(plugins_desc, keyword_ids)

    # -- the whole pass ----------------------------------------------------

    async def select(
        self,
        user_text: str,
        plugins: list[dict[str, Any]],
        model: ScriptedSelectionModel,
        *,
        lang: str = "en",
    ) -> SelectionRun:
        from config.prompts.prompts_nekoclaw_brain import NO_PLUGINS_DESC, TASK_BRAIN_SELECT_PROMPT
        from config.prompts.prompts_sys import _loc
        from nekoclaw.host.decision import DecisionRequest, LexicalDecisionModel, Triage
        from nekoclaw.host.selection import routable_plugins

        class _LLM:
            async def complete(self, tier: str, messages: list[dict[str, Any]]) -> str:
                return model.reply(tier, messages).content

        request = DecisionRequest(
            dispatch_text=user_text,
            user_text=user_text,
            lang=lang,
            plugins=routable_plugins(plugins),
            task_agent_available=False,
        )
        selection = await LexicalDecisionModel(_LLM()).select(request, Triage(kind="new"))

        fine_calls = [call for call in model.calls if call.stage == "fine"]
        if fine_calls:
            template = _loc(TASK_BRAIN_SELECT_PROMPT, lang).format(plugins_desc=_SENTINEL)
            plugins_desc = _between(fine_calls[0].system, template)
        else:
            # No fine selection is made when the first stage kept nothing.
            plugins_desc = NO_PLUGINS_DESC if model.calls else ""
        return SelectionRun(
            plugins_desc=plugins_desc,
            calls=list(model.calls),
            plugin_id=selection.plugin_id,
            entry_id=selection.entry_id,
            plugin_args=selection.plugin_args,
            executable=selection.route == "plugin",
            reason=selection.reason,
        )


SELECTION_IMPLS: dict[str, Any] = {
    NekoclawSelection.name: NekoclawSelection,
}
