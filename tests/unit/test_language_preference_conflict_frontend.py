"""The card manager must only treat this endpoint's own 409 as superseded.

Both servers answer 409 for storage-limited startup and for the cloudsave
maintenance fence too. Those persisted nothing, so announcing "a newer
preference won" and re-hydrating would leave an unsaved selection on screen.

Driven through the real ``_saveCharacterLanguagePreference`` source rather than
asserted statically: the distinction lives in a branch, and a static check
cannot tell which path a given payload actually takes.
"""

import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    PROJECT_ROOT
    / "static"
    / "js"
    / "character_card_manager"
    / "card-form-and-actions.js"
)


@pytest.fixture(scope="module")
def node_path():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("node is required for browser runtime harnesses")
    return executable


def _extract_function(name: str) -> str:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.find(f"async function {name}(")
    assert start >= 0, f"{name} 已改名，请同步更新测试"
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{name} 括号不平衡")


def _harness(status: int, payload: dict) -> str:
    return textwrap.dedent(
        """
        const calls = [];
        let hydrated = 0;

        function _characterLanguageT(_key, fallback) { return fallback; }
        function showMessage(text, level) { calls.push(['message', level]); }
        async function showAlert(text) { calls.push(['alert', String(text)]); }
        function _distrustCachedLanguageBeforeRehydration() { calls.push(['distrust']); }
        async function _hydrateCharacterLanguagePreference() {
          calls.push(['hydrate']);
          hydrated += 1;
        }
        async function _characterLanguageMutationFetch() {
          return {
            status: __STATUS__,
            ok: __STATUS__ >= 200 && __STATUS__ < 300,
            async json() { return __PAYLOAD__; },
          };
        }
        function _cacheCharacterLanguagePreference() { calls.push(['cache']); }

        __FUNCTION__

        const select = {
          value: 'ja',
          dataset: { previousValue: 'en' },
          disabled: false,
        };

        _saveCharacterLanguagePreference('Mimi', select, null).then(() => {
          console.log(JSON.stringify({
            hydrated,
            value: select.value,
            previousValue: select.dataset.previousValue,
            calls,
          }));
        });
        """
    ).replace("__FUNCTION__", _extract_function("_saveCharacterLanguagePreference")) \
     .replace("__STATUS__", str(status)) \
     .replace("__PAYLOAD__", json.dumps(payload))


def _kinds(outcome: dict) -> list:
    return [call[0] for call in outcome["calls"]]


def _run(node_path: str, status: int, payload: dict) -> dict:
    result = run_node_script(
        node_path, _harness(status, payload), capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_superseded_conflict_rehydrates_without_rolling_back(node_path):
    outcome = _run(
        node_path,
        409,
        {"success": False, "error_code": "language_preference_superseded"},
    )

    assert outcome["hydrated"] == 1
    # The stale local value must not be restored; hydration owns the control.
    assert outcome["value"] == "ja"
    assert _kinds(outcome).count("distrust") == 1
    assert _kinds(outcome).index("distrust") < _kinds(outcome).index("hydrate"), (
        "必须先把缓存标为不可信，再去做可能失败的权威读取"
    )
    assert not any(call[0] == "alert" for call in outcome["calls"]), (
        "被取代不是保存失败，不该弹错误"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "error_code": "storage_startup_blocked", "limited_mode": True},
        {"success": False, "code": "cloudsave_maintenance", "retryable": True},
        {"success": False},
    ],
)
def test_unrelated_409_rolls_back_and_reports_failure(node_path, payload):
    outcome = _run(node_path, 409, payload)

    assert outcome["hydrated"] == 0, "非 superseded 的 409 不该触发重新水合"
    assert outcome["value"] == "en", "保存失败必须回滚到先前的值"
    assert any(call[0] == "alert" for call in outcome["calls"]), "必须报告保存失败"


def test_unverified_freshness_rehydrates_instead_of_caching(node_path):
    """An unverified write must not be published to the cross-window cache.

    The value did land durably, but the server could not confirm it is still the
    current one; caching it could pin a stale preference that a later websocket
    session re-persists.
    """
    outcome = _run(
        node_path,
        200,
        {
            "success": False,
            "partial_success": True,
            "freshness_unverified": True,
            "language": "ja",
        },
    )

    assert outcome["hydrated"] == 1
    assert not any(call[0] == "cache" for call in outcome["calls"]), (
        "未确认的写入不得进入跨窗口缓存"
    )
    assert _kinds(outcome).index("distrust") < _kinds(outcome).index("hydrate"), (
        "必须先把缓存标为不可信，再去做可能失败的权威读取"
    )
    assert not any(call[0] == "alert" for call in outcome["calls"])


def test_successful_save_still_caches_and_reports(node_path):
    outcome = _run(node_path, 200, {"success": True, "language": "ja"})

    assert outcome["hydrated"] == 0
    assert outcome["value"] == "ja"
    assert outcome["previousValue"] == "ja"
    assert any(call[0] == "cache" for call in outcome["calls"])


def test_frontend_conflict_code_matches_the_backend_constant():
    """Pin the two ends of the wire contract to the same literal."""
    frontend = SOURCE.read_text(encoding="utf-8")
    backend = (
        PROJECT_ROOT / "main_routers" / "characters_router" / "language_preference.py"
    ).read_text(encoding="utf-8")
    memory_server = (
        PROJECT_ROOT / "app" / "memory_server" / "routes.py"
    ).read_text(encoding="utf-8")

    code = "language_preference_superseded"
    assert re.search(rf"error_code\s*===\s*'{code}'", frontend)
    assert f'"{code}"' in backend
    assert f'"{code}"' in memory_server
