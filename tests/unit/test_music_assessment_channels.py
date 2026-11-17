"""Music guidance follows the NekoClaw plugin shortlist, not main-chat prompts."""
from copy import deepcopy
import pytest
from config.prompts.prompts_music_assessment import music_query_guidance
from tests.unit.nekoclaw.selection_impls import NekoclawSelection, ScriptedSelectionModel

@pytest.mark.parametrize("lang", ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"])
@pytest.mark.parametrize("plugin_id", ["netease_music", "weather"])
async def test_music_guidance_only_reaches_selected_plugin_catalog(monkeypatch, lang, plugin_id):
    catalog = [{"id": plugin_id, "description": "A plugin", "entries": [{"id": "run", "description": "Run"}]}]
    original = deepcopy(catalog)
    model = ScriptedSelectionModel()
    await NekoclawSelection(monkeypatch).select("help with this task", catalog, model, lang=lang)
    assert (music_query_guidance(lang) in model.calls[-1].system) is (plugin_id == "netease_music")
    assert catalog == original

@pytest.mark.parametrize("followup,query", [("你用网络搜索呢", "Mira Vale lyrics meaning"), ("查京都十一月天气", "京都 十一月 天气"), ("取消搜索也不要播放", None)])
async def test_music_catalog_preserves_other_tools_and_current_dialogue(monkeypatch, followup, query):
    catalog = [{"id": name, "entries": [{"id": "search", "description": "Search"}]} for name in ["netease_music", "web_search"]]
    model = ScriptedSelectionModel(fine=[{"has_task": bool(query), "can_execute": bool(query), "plugin_id": "web_search", "entry_id": "search", "plugin_args": {"query": query}}])
    result = await NekoclawSelection(monkeypatch).select("查歌词含义。当前要求：" + followup, catalog, model)
    assert followup in model.calls[-1].user and "web_search" in model.calls[-1].system
    assert result.executable is bool(query)
    if query: assert result.plugin_args == {"query": query}
