"""验证小剧场作者校验工具的单文件、脱敏和零模型边界。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from scripts import validate_theater_story
from services.theater import story_loader


def _story_payload(
    *,
    with_slot: bool = False,
    with_catalog: bool = False,
) -> dict[str, Any]:
    """构造不依赖正式 Story 的最小合成作者包。"""  # noqa: DOCSTRING_CJK
    story: dict[str, Any] = {
        "id": "synthetic_story",
        "title": "Synthetic Story",
        "background": "Two participants verify a minimal authored route.",
        "initial_scene_id": "scene_setup",
        "scenes": [
            {
                "id": "scene_setup",
                "phase": "setup",
                "title": "Synthetic setup",
                "text": "Both participants remain in the authored setup scene.",
            }
        ],
        "narrative_nodes": [
            {
                "node_id": "node_start",
                "node_type": "seed",
                "belong_phase": "setup",
                "suggestions": [],
            },
            {
                "node_id": "node_end",
                "node_type": "ending",
                "belong_phase": "setup",
                "ending_id": "node_end",
                "scripted_dialogue": "The authored route is now complete.",
                "suggestions": [
                    {
                        "choice_id": "choice_finish",
                        "label": "Finish the authored route",
                        "choice_mode": "action",
                        "callback": "The authored route reaches its ending.",
                    }
                ],
            },
        ],
        "edges": [
            {
                "from_node": "node_start",
                "to_node": "node_end",
                "visibility": "recommended",
            }
        ],
    }
    if not with_slot:
        return story

    story["narrative_nodes"][1]["completes_goal_ids"] = ["goal_finish"]
    story["edges"][0]["goal_id"] = "goal_finish"
    story.update(
        {
            "story_revision": "synthetic-v1",
            "narrative_goals": [
                {
                    "goal_id": "goal_finish",
                    "summary": "Finish the synthetic route",
                    "completion_evidence": ["finish_evidence"],
                    "convergence_fact_roles": ["finish_evidence"],
                    "completion_fact_projections": [],
                    "converge_to_node_id": "node_end",
                    "fallback_convergence_callback": "Return to the authored ending",
                }
            ],
            "world_contract": {
                "speaking_roles": ["player", "active_catgirl"],
                "immutable_facts": [],
                "allowed_dynamic_fact_types": ["synthetic_item"],
                "dynamic_content_slots": [
                    {
                        "slot_id": "slot_synthetic_item",
                        "allowed_fact_type": "synthetic_item",
                        "allowed_traits": ["safe"],
                        "forbidden_traits": ["dangerous"],
                    }
                ],
                "forbidden_changes": ["speaking_roles"],
                "branch_turn_budget": {
                    "default": 1,
                    "max": 2,
                    "max_nonprogress_turns": 1,
                },
                "branch_abort_policy": {
                    "mode": "return_to_anchor",
                    "neutral_callback": "Return to the synthetic anchor",
                },
                "allowed_ending_domains": ["domain_finish"],
                "convergence_goal_ids": ["goal_finish"],
            },
            "ending_domains": [
                {
                    "ending_domain_id": "domain_finish",
                    "ending_id": "node_end",
                    "required_goal_ids": ["goal_finish"],
                    "required_fact_types": [],
                    "required_fact_roles": [],
                    "forbidden_fact_roles": [],
                }
            ],
        }
    )
    if with_catalog:
        story["world_contract"]["dynamic_content_slots"][0]["catalog_items"] = [
            {
                "content_id": "item_safe",
                "entity_kind": "prop",
                "label": "Safe synthetic item",
                "fact_object": "safe_synthetic_item_selected",
                "traits": ["safe"],
            }
        ]
    return story


def _write_json(path: Path, payload: Any) -> None:
    """写入测试专用 JSON，不接触正式 Story 目录。"""  # noqa: DOCSTRING_CJK
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _stdout_report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """读取 CLI 唯一的结构化标准输出。"""  # noqa: DOCSTRING_CJK
    captured = capsys.readouterr()
    assert not captured.err
    return json.loads(captured.out)


@pytest.mark.asyncio
async def test_validate_story_file_ignores_bad_sibling_and_returns_deep_copy(
    tmp_path: Path,
):
    """单文件校验不得被同目录坏文件影响，也不得把可变输入对象泄露给调用方。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    _write_json(story_path, _story_payload())
    (tmp_path / "broken-sibling.json").write_text("{broken", encoding="utf-8")
    original_bytes = story_path.read_bytes()

    first = await story_loader.validate_story_file(story_path)
    first["title"] = "mutated by caller"
    second = await story_loader.validate_story_file(story_path)

    assert second["title"] == "Synthetic Story"
    assert story_path.read_bytes() == original_bytes


def test_cli_warns_when_legacy_edge_uses_default_visibility(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """旧 Story 缺省边仍可加载，但作者报告必须提示其依赖兼容默认值。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "legacy-visibility-story.json"
    payload = _story_payload()
    payload["edges"][0].pop("visibility")
    _write_json(story_path, payload)
    original_bytes = story_path.read_bytes()

    exit_code = validate_theater_story.main(["--story", str(story_path)])
    report = _stdout_report(capsys)

    assert exit_code == 0
    assert report["valid"] is True
    assert report["warnings"] == [{"code": "edge_visibility_legacy_default"}]
    assert story_path.read_bytes() == original_bytes


def test_cli_valid_story_warns_for_declarative_slot_without_network_or_default_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """合法开放槽位允许通过，但必须警告其 traits 仍只是声明，且全程不联网。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    _write_json(story_path, _story_payload(with_slot=True))
    original_bytes = story_path.read_bytes()

    def _unexpected_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("作者校验工具不得建立网络连接")

    async def _unexpected_async_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("作者校验工具不得建立异步网络连接")

    monkeypatch.setattr(socket, "create_connection", _unexpected_network)
    monkeypatch.setattr(asyncio, "open_connection", _unexpected_async_network)

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--explain-slots"]
    )
    report = _stdout_report(capsys)

    assert exit_code == 0
    assert report["valid"] is True
    assert report["code"] == "valid"
    assert report["warnings"] == [
        {
            "code": "slot_traits_declarative_only",
            "slot_id": "slot_synthetic_item",
        }
    ]
    assert report["slots"] == [
        {
            "slot_id": "slot_synthetic_item",
            "allowed_fact_type": "synthetic_item",
            "required_traits": ["safe"],
            "forbidden_traits": ["dangerous"],
            "enforcement": "declarative_only",
        }
    ]
    assert story_path.read_bytes() == original_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == ["story.json"]


def test_cli_marks_loader_validated_catalog_as_verified(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """只有先通过 Loader 精确目录合同的 Story，才会报告目录槽位已确定性校验。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "catalog-story.json"
    _write_json(
        story_path,
        _story_payload(with_slot=True, with_catalog=True),
    )

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--explain-slots"]
    )
    report = _stdout_report(capsys)

    assert exit_code == 0
    assert report["warnings"] == []
    assert report["slots"] == [
        {
            "slot_id": "slot_synthetic_item",
            "allowed_fact_type": "synthetic_item",
            "required_traits": ["safe"],
            "forbidden_traits": ["dangerous"],
            "enforcement": "catalog_verified",
        }
    ]


def test_cli_does_not_mark_runtime_invalid_catalog_label_as_verified(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """机器式 Board 标签必须在 Loader 阶段失败，不能被作者工具误报为严格目录。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "invalid-catalog-label.json"
    payload = _story_payload(with_slot=True, with_catalog=True)
    payload["world_contract"]["dynamic_content_slots"][0]["catalog_items"][0][
        "label"
    ] = "safe_item"
    _write_json(story_path, payload)

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--explain-slots"]
    )
    report = _stdout_report(capsys)

    assert exit_code == 1
    assert report["code"] == "story_contract_invalid"
    assert "slots" not in report


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        ("[]", "story_root_not_object"),
        ("{broken\n", "story_json_invalid"),
    ],
)
def test_cli_classifies_json_and_root_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    raw: str,
    expected_code: str,
):
    """JSON 语法和顶层类型错误使用不同稳定码，并保持输入不变。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    story_path.write_text(raw, encoding="utf-8")
    original_bytes = story_path.read_bytes()

    exit_code = validate_theater_story.main(["--story", str(story_path)])
    report = _stdout_report(capsys)

    assert exit_code == 1
    assert report["valid"] is False
    assert report["code"] == expected_code
    assert story_path.read_bytes() == original_bytes
    if expected_code == "story_json_invalid":
        assert set(report["location"]) == {"line", "column"}


def test_cli_contract_error_redacts_path_story_text_and_exception(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """合同失败只输出稳定码，不泄露绝对路径、作者正文或 Loader 异常原文。"""  # noqa: DOCSTRING_CJK
    secret_text = "SECRET_STORY_BODY_DO_NOT_ECHO"
    story_path = tmp_path / "SECRET_ABSOLUTE_FILENAME.json"
    _write_json(story_path, {"id": secret_text})

    exit_code = validate_theater_story.main(["--story", str(story_path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert exit_code == 1
    assert report["code"] == "story_contract_invalid"
    assert str(story_path) not in captured.out
    assert story_path.name not in captured.out
    assert secret_text not in captured.out
    assert "missing fields" not in captured.out


@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        ("missing", "input_missing"),
        ("directory", "input_not_file"),
    ],
)
def test_cli_classifies_input_path_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    expected_code: str,
):
    """不存在的输入和目录输入均以工具错误结束，不尝试默认 Story。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "missing.json"
    if kind == "directory":
        story_path.mkdir()

    exit_code = validate_theater_story.main(["--story", str(story_path)])
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == expected_code


def test_cli_classifies_input_read_and_internal_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """文件读取失败与未预期内部失败必须保持不同稳定码。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    _write_json(story_path, _story_payload())

    async def _read_failed(_path: Path) -> dict[str, Any]:
        raise PermissionError("SECRET_PERMISSION_DETAIL")

    monkeypatch.setattr(story_loader, "validate_story_file", _read_failed)
    assert validate_theater_story.main(["--story", str(story_path)]) == 2
    assert _stdout_report(capsys)["code"] == "input_read_failed"

    async def _internal_failed(_path: Path) -> dict[str, Any]:
        raise RuntimeError("SECRET_INTERNAL_DETAIL")

    monkeypatch.setattr(story_loader, "validate_story_file", _internal_failed)
    assert validate_theater_story.main(["--story", str(story_path)]) == 2
    assert _stdout_report(capsys)["code"] == "internal_error"


def test_cli_rejects_output_conflict_without_touching_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """报告路径与 Story 相同时必须先拒绝，不能覆盖作者输入。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    _write_json(story_path, _story_payload())
    original_bytes = story_path.read_bytes()

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(story_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_input"
    assert story_path.read_bytes() == original_bytes


def test_cli_output_conflict_does_not_create_missing_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """输入尚不存在时，同路径报告也不能反向创建一个伪 Story 文件。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "missing-story.json"

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(story_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_input"
    assert not story_path.exists()


def test_cli_rejects_json_report_that_would_pollute_story_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """同目录 JSON 报告会被 Loader 当成 Story，工具必须在写入前拒绝。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    output_path = tmp_path / "validation.json"
    _write_json(story_path, _story_payload())

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_story_directory"
    assert not output_path.exists()
    stories = asyncio.run(story_loader.list_stories(story_dir=tmp_path))
    assert [story["id"] for story in stories] == ["synthetic_story"]


def test_cli_does_not_follow_output_symlink_around_story_directory_guard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """输出文件符号链接不能绕过父目录判断并被原子写入替换。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    target_path = reports_dir / "target.json"
    output_path = tmp_path / "validation.json"
    _write_json(story_path, _story_payload())
    target_path.write_text('{"existing": true}\n', encoding="utf-8")
    original_target = target_path.read_bytes()
    output_path.symlink_to(target_path)

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_story_directory"
    assert output_path.is_symlink()
    assert target_path.read_bytes() == original_target


def test_cli_uses_lexical_story_parent_when_input_is_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """输入 Story 指向别处时，报告仍不能写入链接所在的作者扫描目录。"""  # noqa: DOCSTRING_CJK
    author_dir = tmp_path / "author"
    source_dir = tmp_path / "sources"
    author_dir.mkdir()
    source_dir.mkdir()
    source_path = source_dir / "real-story.json"
    story_path = author_dir / "story.json"
    output_path = author_dir / "validation.json"
    _write_json(source_path, _story_payload())
    story_path.symlink_to(source_path)

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_story_directory"
    assert story_path.is_symlink()
    assert not output_path.exists()
    stories = asyncio.run(story_loader.list_stories(story_dir=author_dir))
    assert [story["id"] for story in stories] == ["synthetic_story"]


def test_cli_rejects_nested_output_below_json_named_story_entry(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """报告不能创建会被 Loader 的直接 glob 当成 Story 的 `.json` 目录。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    json_directory = tmp_path / "validation.json"
    output_path = json_directory / "report.txt"
    _write_json(story_path, _story_payload())

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_story_directory"
    assert not json_directory.exists()
    stories = asyncio.run(story_loader.list_stories(story_dir=tmp_path))
    assert [story["id"] for story in stories] == ["synthetic_story"]


def test_cli_rejects_all_outputs_below_formal_story_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """正式 Story 目录下即使使用普通子目录，也不能写入作者诊断产物。"""  # noqa: DOCSTRING_CJK
    formal_dir = tmp_path / "formal-stories"
    formal_dir.mkdir()
    story_path = formal_dir / "story.json"
    output_path = formal_dir / "reports" / "report.txt"
    _write_json(story_path, _story_payload())
    monkeypatch.setattr(story_loader, "CONFIG_STORY_DIR", formal_dir)

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_conflicts_story_directory"
    assert not output_path.parent.exists()


def test_cli_writes_only_explicit_output_and_stdout_matches_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """显式报告路径可以写入，落盘内容与脱敏标准输出保持一致。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    output_path = tmp_path / "reports" / "validation.json"
    _write_json(story_path, _story_payload())
    original_bytes = story_path.read_bytes()

    exit_code = validate_theater_story.main(
        ["--story", str(story_path), "--output", str(output_path)]
    )
    stdout_report = _stdout_report(capsys)
    written_report = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert stdout_report == written_report
    assert written_report["code"] == "valid"
    assert story_path.read_bytes() == original_bytes


def test_cli_classifies_output_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    """报告写入失败覆盖原校验结果并返回工具错误码。"""  # noqa: DOCSTRING_CJK
    story_path = tmp_path / "story.json"
    _write_json(story_path, _story_payload())

    def _write_failed(_path: Path, _report: dict[str, Any]) -> None:
        raise OSError("SECRET_OUTPUT_DETAIL")

    monkeypatch.setattr(validate_theater_story, "_write_report", _write_failed)
    exit_code = validate_theater_story.main(
        [
            "--story",
            str(story_path),
            "--output",
            str(tmp_path / "reports" / "report.json"),
        ]
    )
    report = _stdout_report(capsys)

    assert exit_code == 2
    assert report["code"] == "output_write_failed"
    assert "SECRET_OUTPUT_DETAIL" not in json.dumps(report)


def test_cli_requires_explicit_story_argument():
    """缺少单文件参数时使用 argparse 的参数错误退出，不扫描正式 Story 目录。"""  # noqa: DOCSTRING_CJK
    with pytest.raises(SystemExit) as exc_info:
        validate_theater_story.main([])

    assert exc_info.value.code == 2
