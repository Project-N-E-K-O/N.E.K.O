"""验证 Story Loader 拆分后仍保留原公开门面和唯一校验入口。"""  # noqa: DOCSTRING_CJK

from copy import deepcopy

import pytest

from services.theater import story_contracts, story_loader
from tests.utils.theater_story_fixture import THEATER_TEST_STORY_PATH
from utils.file_utils import atomic_write_json_async, read_json_async


def test_story_loader_reexports_contract_identity():
    """原模块路径必须继续暴露同一个异常类和 setup 节点查询函数。"""  # noqa: DOCSTRING_CJK
    assert (
        story_loader.StoryRootNotObjectError
        is story_contracts.StoryRootNotObjectError
    )
    assert story_loader.initial_node_id is story_contracts.initial_node_id


@pytest.mark.asyncio
async def test_single_file_and_directory_share_contract_validator(
    monkeypatch, tmp_path
):
    """显式文件和正式目录读取必须经过同一个同步合同入口。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "synthetic.json"
    payload = {"id": "synthetic", "title": "Synthetic"}
    await atomic_write_json_async(story_path, payload, ensure_ascii=False, indent=2)
    calls = []

    def _validate(story, path):
        """记录两个读取入口实际调用的合同函数。"""  # noqa: DOCSTRING_CJK
        calls.append(path.name)
        return deepcopy(story)

    monkeypatch.setattr(story_contracts, "validate_story_package", _validate)

    assert await story_loader.validate_story_file(story_path) == payload
    public = await story_loader.list_stories(story_dir=tmp_path)
    assert public[0]["id"] == "synthetic"
    assert calls == ["synthetic.json", "synthetic.json"]


@pytest.mark.asyncio
async def test_story_contract_returns_deep_copy():
    """校验结果不能与刚读取的作者 JSON 共用可变对象。"""  # noqa: DOCSTRING_CJK
    story_path = THEATER_TEST_STORY_PATH
    payload = await read_json_async(story_path)
    validated = story_contracts.validate_story_package(payload, story_path)

    original_title = payload["title"]
    validated["title"] = "修改后的测试标题"
    assert payload["title"] == original_title
