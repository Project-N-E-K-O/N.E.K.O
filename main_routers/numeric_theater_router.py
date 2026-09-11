"""Numeric v2 独立剧本 HTTP 接口。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
import json
import logging
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from weakref import WeakValueDictionary

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from services.theater.numeric_v2_usage import numeric_v2_usage_scope, with_numeric_v2_usage
from services.theater.numeric_v2 import NumericV2CompileError
from services.theater.numeric_v2_actor import (
    NumericV2Actor,
    NumericV2ActorError,
    NumericV2ActorUnavailableError,
)
from services.theater.numeric_v2_budget import (
    NUMERIC_V2_ACTOR_BUDGET_PROFILES,
    NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
)
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_identity import (
    numeric_v2_catgirl_binding,
    numeric_v2_character_ids,
)
from services.theater.numeric_v2_maintenance import (
    delete_numeric_v2_story_transactionally,
    maintain_numeric_v2_storage_once,
)
from services.theater.numeric_v2_performance import MAX_CONTENT_BLOCKS, performance_content_blocks
from services.theater.numeric_v2_archive import (
    NumericV2ArchiveError,
    NumericV2ArchiveStore,
    build_numeric_v2_memory_messages,
)
from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluatorError,
    NumericV2EvaluatorUnavailableError,
    NumericV2MetricEvaluator,
)
from services.theater.numeric_v2_registry import (
    NumericV2PackageError,
    NumericV2PackageExistsError,
    NumericV2PackageNotFoundError,
    NumericV2PackageRegistry,
)
from services.theater.numeric_v2_runtime import (
    NumericV2DuplicateTurnError,
    NumericV2RevisionConflictError,
    NumericV2Runtime,
    NumericV2RuntimeError,
    TurnRequestV2,
)
from services.theater.paths import theater_root
from services.theater.numeric_v2_storage_transaction import run_storage_mutation
from services.theater.numeric_v2_store import (
    list_numeric_v2_sessions,
    numeric_v2_story_session_guard,
    NumericV2SessionExistsError,
    NumericV2SessionNotFoundError,
    NumericV2StoreError,
    NumericV2StoreRevisionConflictError,
)
from services.theater.numeric_v2_workflow import (
    execute_numeric_v2_turn,
    generate_validated_opening,
)
from services.theater.tts_bridge import speak_committed_line
from utils.cloudsave_runtime import (
    MaintenanceModeError,
    assert_cloudsave_writable,
    cloudsave_writable_transaction,
)
from utils.character_memory import character_config_mutation_lock


router = APIRouter(prefix="/api/theater-numeric", tags=["theater-numeric-v2"])
logger = logging.getLogger(__name__)
# 请求执行期间由局部变量强持有锁；完成后弱引用表可自动回收不同请求 ID，避免长期运行持续增长。
_speak_request_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_speak_request_results: dict[str, dict[str, Any]] = {}
# 归档幂等事实保存在回执文件中，进程内锁只负责并发窗口，不应永久保留每个回执 ID。
_archive_request_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _request_lock(
    locks: WeakValueDictionary[str, asyncio.Lock],
    request_id: str,
) -> asyncio.Lock:
    """返回请求粒度的共享锁，并在返回前用局部变量保持强引用。"""  # noqa: DOCSTRING_CJK

    lock = locks.get(request_id)
    if lock is None:
        # 同一事件循环内没有跨线程写入；局部强引用可覆盖弱引用登记到调用方接管之间的空隙。
        lock = asyncio.Lock()
        locks[request_id] = lock
    return lock


def _performance_block_group(
    performance: Mapping[str, Any],
    block_index: int,
) -> tuple[list[dict[str, str]], int] | None:
    """定位内容块所属演绎段，避免把换场前后的对白合并成同一条语音。"""  # noqa: DOCSTRING_CJK

    raw_segments = performance.get("segments")
    containers = raw_segments if isinstance(raw_segments, list) else [performance]
    offset = 0
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        blocks = performance_content_blocks(container)
        if offset <= block_index < offset + len(blocks):
            return blocks, offset
        offset += len(blocks)
    return None


def _current_suggested_inputs(session: Any) -> tuple[str, ...]:
    """返回玩家此刻真正可见的推荐，供推荐点击来源复验。"""  # noqa: DOCSTRING_CJK

    if session.revision == 0:
        performance = session.opening_performance
    elif session.performance_history:
        performance = session.performance_history[-1]
    else:
        return ()
    return tuple(
        str(item).strip()
        for item in performance.get("suggested_inputs") or []
        if str(item).strip()
    )


def _numeric_root(config_manager: Any) -> Path:
    """v2 复用统一剧场根目录，但只访问 numeric_v2 私有子目录。"""  # noqa: DOCSTRING_CJK
    return theater_root(config_manager)


def _numeric_write_transaction(config_manager: Any, root: Path):
    expected_root = Path(root).resolve()

    @contextmanager
    def transaction():
        with cloudsave_writable_transaction(config_manager, operation="save", target="theater/numeric_v2"):
            if _numeric_root(config_manager).resolve() != expected_root:
                raise NumericV2StoreRevisionConflictError("numeric_storage_root_changed")
            yield

    return transaction


def _archive_store(config_manager: Any) -> NumericV2ArchiveStore:
    root = _numeric_root(config_manager)
    return NumericV2ArchiveStore(root, write_transaction=_numeric_write_transaction(config_manager, root))


async def _assert_numeric_writable(config_manager: Any, target: str) -> None:
    """Reject maintenance early; final disk operations also hold a write transaction."""

    await asyncio.to_thread(
        assert_cloudsave_writable,
        config_manager,
        operation="save",
        target=f"theater/numeric_v2/{target}",
    )


async def _registry(config_manager: Any) -> NumericV2PackageRegistry:
    numeric_root = _numeric_root(config_manager)
    registry = NumericV2PackageRegistry(numeric_root / "numeric_v2" / "packages")
    character_ids_by_name = await asyncio.to_thread(
        numeric_v2_character_ids,
        config_manager,
    )
    await run_storage_mutation(
        nullcontext,
        maintain_numeric_v2_storage_once,
        numeric_root,
        registry,
        character_ids_by_name=character_ids_by_name,
        write_transaction=_numeric_write_transaction(config_manager, numeric_root),
        assert_writable=lambda: assert_cloudsave_writable(
            config_manager,
            operation="repair",
            target="theater/numeric_v2",
        ),
    )
    return registry


async def _create_ended_receipt(config_manager: Any, session: Any) -> dict[str, Any] | None:
    """兼容旧 Session 补建结束回执前，先通过共享写栅栏。"""  # noqa: DOCSTRING_CJK

    await _assert_numeric_writable(config_manager, "end_receipts")
    if await asyncio.to_thread(
        _archive_store(config_manager).pending_forget,
        session.story_package_id, str(session.catgirl_binding.get("character_id") or ""),
    ):
        # A pending explicit forget decision must not become a new archival invitation.
        return None
    return await _archive_store(config_manager).acreate_or_get(session)


async def _create_receipt_for_existing_ended_session(
    config_manager: Any,
    runtime: NumericV2Runtime,
    session: Any,
) -> dict[str, Any]:
    """持有生命周期锁复验已结束 Session，再创建或补建回执。"""  # noqa: DOCSTRING_CJK

    # 固定角色锁先于故事锁，避免与删除、改名事务形成反向等待。
    async with character_config_mutation_lock, runtime.story_session_guard():
        current = await runtime.restore_session(str(session.session_id))
        if current is None or current.session.status != "ended":
            raise NumericV2SessionNotFoundError("numeric_session_not_found")
        return await _create_ended_receipt(config_manager, current.session)


async def _runtime_for_story(config_manager: Any, story_id: str) -> NumericV2Runtime:
    registry = await _registry(config_manager)
    engine = await asyncio.to_thread(registry.load_engine, story_id)
    root = registry.root.parent.parent
    return NumericV2Runtime(engine, root, write_transaction=_numeric_write_transaction(config_manager, root))


def _current_catgirl_binding(config_manager: Any) -> dict[str, str]:
    """Session 只绑定服务端当前猫娘，客户端不能伪造人格。"""  # noqa: DOCSTRING_CJK

    return numeric_v2_catgirl_binding(config_manager)


def _surface_player_name(binding: Mapping[str, str], *, known: bool) -> str:
    """用户可见投影只在 Session 确认后使用真实称呼。"""  # noqa: DOCSTRING_CJK

    if not known:
        return "你"
    return str(binding.get("player_address") or "你").strip() or "你"


def _ensure_current_catgirl(session: Any, config_manager: Any) -> dict[str, str]:
    """只用不可变角色 ID 校验归属，允许同一角色改名或更新角色卡。"""  # noqa: DOCSTRING_CJK

    current_binding = _current_catgirl_binding(config_manager)
    if str(session.catgirl_binding.get("character_id") or "") != str(
        current_binding.get("character_id") or ""
    ):
        raise ValueError("catgirl_changed_requires_new_session")
    return current_binding


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error(reason: str, status_code: int) -> JSONResponse:
    return JSONResponse({"ok": False, "reason": reason}, status_code=status_code)


def _package_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, NumericV2PackageNotFoundError):
        return _error("numeric_story_not_found", 404)
    if isinstance(exc, NumericV2PackageError):
        return _error(str(exc), 422)
    return _error("numeric_story_load_failed", 500)


def _scene_projection(
    runtime: NumericV2Runtime,
    stored: Any,
    display_binding: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    node = runtime.engine.nodes[stored.session.current_node_id]
    binding = display_binding or stored.session.catgirl_binding
    cast = NumericV2CastProjection.from_story(
        runtime.engine.story,
        player_name=_surface_player_name(
            binding,
            known=bool(stored.session.player_address_known),
        ),
        catgirl_name=str(binding.get("catgirl_name") or "当前猫娘"),
    )
    ending = None
    if node.get("type") == "ending" or node.get("terminal") is True:
        ending_id = str(node.get("ending_id") or "")
        raw = next((item for item in runtime.engine.story["endings"] if item["id"] == ending_id), None)
        if raw:
            ending = {
                "id": raw["id"],
                "title": cast.text(raw["title"]),
                "summary": cast.text(raw["summary"]),
            }
    return {
        "chapter": node["chapter"],
        "summary": cast.text(node["story_beat"]["summary"]),
        "terminal": bool(ending),
        "ending": ending,
        "node_turn_count": stored.session.node_turn_count,
        "min_turns": int(node.get("min_turns") or 0),
    }


_INTERNAL_PERFORMANCE_FIELDS = frozenset({
    "from_node_id",
    "to_node_id",
    "visible_node_id",
    "suggestion_candidates",
})


def _public_performance(performance: Mapping[str, Any]) -> dict[str, Any]:
    """移除只供 Runtime 重放和校验使用的路线标识。"""  # noqa: DOCSTRING_CJK

    return {
        str(key): value
        for key, value in performance.items()
        if key not in _INTERNAL_PERFORMANCE_FIELDS
    }


def _public_session(session: Any) -> dict[str, Any]:
    """隐藏 metric 原始值，只向页面提供恢复和回放需要的字段。"""  # noqa: DOCSTRING_CJK

    return {
        "schema": session.to_dict()["schema"],
        "session_id": session.session_id,
        "story_package_id": session.story_package_id,
        "story_package_revision": session.story_package_revision,
        "story_package_hash": session.story_package_hash,
        "node_turn_count": session.node_turn_count,
        "revision": session.revision,
        "lifecycle_revision": session.lifecycle_revision,
        "status": session.status,
        "player_address_known": session.player_address_known,
        # 候选用途和目标 ID 只服务 Runtime/压测选择；开场与历史都必须经过同一公开投影。
        "opening_performance": _public_performance(session.opening_performance),
        "performance_history": [
            _public_performance(record)
            for record in session.performance_history
        ],
        "actor_budget_profile": session.actor_budget_profile,
        "ended_reason": session.ended_reason,
    }


def _numeric_payload(
    runtime: NumericV2Runtime,
    stored: Any,
    *,
    end_receipt: Mapping[str, Any] | None = None,
    display_binding: Mapping[str, str] | None = None,
    story_projection_compatible: bool = True,
) -> dict[str, Any]:
    binding = display_binding or stored.session.catgirl_binding
    if not story_projection_compatible:
        # 旧 Session 可以在剧本升级后结束，但不能把新剧本的场景投影伪装成旧剧情。
        payload = {
            "session": {**_public_session(stored.session), "continuation_allowed": False},
            "story_title": str(runtime.engine.story["meta"]["title"]),
            "participants": {
                "player_name": _surface_player_name(
                    binding,
                    known=bool(stored.session.player_address_known),
                ),
                "catgirl_name": str(binding.get("catgirl_name") or "当前猫娘"),
            },
            "story_intro": {},
            "scene": None,
            "suggested_inputs": [],
        }
        if end_receipt:
            payload["end_receipt_id"] = str(end_receipt.get("receipt_id") or "")
            payload["archive_status"] = str(end_receipt.get("status") or "pending")
            payload["archive_request_id"] = str(end_receipt.get("archive_request_id") or "")
        return payload

    latest = stored.session.performance_history[-1] if stored.session.performance_history else stored.session.opening_performance
    cast = NumericV2CastProjection.from_story(
        runtime.engine.story,
        player_name=_surface_player_name(
            binding,
            known=bool(stored.session.player_address_known),
        ),
        catgirl_name=str(binding.get("catgirl_name") or "当前猫娘"),
    )
    payload = {
        "session": _public_session(stored.session),
        "story_title": str(runtime.engine.story["meta"]["title"]),
        # 历史区署名使用当前展示绑定；不要让前端从本地文案猜玩家或猫娘名称。
        "participants": {
            "player_name": _surface_player_name(
                binding,
                known=bool(stored.session.player_address_known),
            ),
            "catgirl_name": str(binding.get("catgirl_name") or "当前猫娘"),
        },
        "story_intro": cast.intro(runtime.engine.story),
        "scene": _scene_projection(runtime, stored, binding),
        "suggested_inputs": list(latest.get("suggested_inputs") or []),
    }
    if end_receipt:
        payload["end_receipt_id"] = str(end_receipt.get("receipt_id") or "")
        payload["archive_status"] = str(end_receipt.get("status") or "pending")
        payload["archive_request_id"] = str(end_receipt.get("archive_request_id") or "")
    return payload


def _list_story_summaries(
    registry: NumericV2PackageRegistry,
    binding: Mapping[str, str],
) -> list[dict[str, Any]]:
    """在线程中读取一次剧本列表，并直接投影已经校验过的简介。"""  # noqa: DOCSTRING_CJK

    stories: list[dict[str, Any]] = []
    for summary in registry.list_packages():
        # list_packages 已经读取并编译过整包；这里只需要简介中的作者角色名，不能再次加载同一文件。
        projection_story = {"intro": dict(summary.get("intro") or {})}
        cast = NumericV2CastProjection.from_story(
            projection_story,
            player_name=_surface_player_name(binding, known=False),
            catgirl_name=binding.get("catgirl_name") or "当前猫娘",
        )
        stories.append({**summary, "display_intro": cast.intro(projection_story)})
    return stories


@router.get("/stories")
async def list_numeric_stories():
    try:
        config_manager = get_config_manager()
        registry = await _registry(config_manager)
        binding = _current_catgirl_binding(config_manager)
        stories = await asyncio.to_thread(_list_story_summaries, registry, binding)
        return {
            "ok": True,
            "stories": stories,
            # 破坏性操作必须绑定选剧页当前展示的角色，不能只依赖请求到达时的全局当前角色。
            "character_id": binding["character_id"],
        }
    except MaintenanceModeError:
        raise
    except Exception:
        return _error("numeric_story_list_failed", 500)


@router.post("/packages/import")
async def import_numeric_story(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload, error_defaults={"ok": False, "reason": "csrf_validation_failed"})
    if validation_error is not None:
        return validation_error
    try:
        config_manager = get_config_manager()
        registry = await _registry(config_manager)
        compiled = await asyncio.to_thread(
            registry.compile_for_import,
            payload,
        )
        # 导入与同 story_id 的删除事务共用生命周期锁，避免成功导入被迟到回滚覆盖。
        async with numeric_v2_story_session_guard(
            _numeric_root(config_manager),
            compiled.story_id,
        ):
            await _assert_numeric_writable(config_manager, "packages")
            summary = await run_storage_mutation(
                _numeric_write_transaction(config_manager, registry.root.parent.parent),
                registry.import_package,
                compiled.story,
            )
    except NumericV2CompileError:
        return _error("numeric_v2_contract_invalid", 422)
    except NumericV2PackageExistsError:
        return _error("numeric_story_exists", 409)
    except NumericV2PackageError as exc:
        return _package_error(exc)
    except (OSError, UnicodeError):
        return _error("numeric_story_import_failed", 500)
    return {"ok": True, "package": summary}


@router.get("/packages/{story_id}/delete-preview")
async def preview_numeric_story_delete(story_id: str):
    config_manager = get_config_manager()
    normalized_story_id = str(story_id or "").strip()
    try:
        registry = await _registry(config_manager)
        await asyncio.to_thread(registry.load_engine, normalized_story_id)
        sessions = await asyncio.to_thread(
            list_numeric_v2_sessions,
            _numeric_root(config_manager),
            story_id=normalized_story_id,
        )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    active_catgirl_names = sorted(
        {
            item["catgirl_name"]
            for item in sessions
            if item["status"] != "ended" and item["catgirl_name"]
        }
    )
    return {
        "ok": True,
        "active_catgirl_names": active_catgirl_names,
        "session_count": len(sessions),
    }


@router.delete("/packages/{story_id}")
async def delete_numeric_story(story_id: str, request: Request):
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    config_manager = get_config_manager()
    normalized_story_id = str(story_id or "").strip()
    try:
        registry = await _registry(config_manager)
        # 删除恢复入口只校验安全路径与文件存在性；损坏包无法编译时也必须允许原子清理。
        package_path = registry.package_path(normalized_story_id)
        # 剧本删除与角色改名/删除会读写同一批 Session、回执和归档，统一按角色锁→故事锁串行。
        async with character_config_mutation_lock, numeric_v2_story_session_guard(
            _numeric_root(config_manager),
            normalized_story_id,
        ):
            if not await asyncio.to_thread(package_path.is_file):
                raise NumericV2PackageNotFoundError("numeric_story_not_found")
            await _assert_numeric_writable(config_manager, "packages")
            deleted_session_count = await delete_numeric_v2_story_transactionally(
                _numeric_root(config_manager),
                registry,
                normalized_story_id,
                write_transaction=_numeric_write_transaction(config_manager, registry.root.parent.parent),
            )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2StoreError as exc:
        return _error(str(exc), 500)
    return {"ok": True, "deleted_session_count": deleted_session_count}


@router.post("/session/start")
async def start_numeric_session(request: Request):
    # 请求级统计包含失败尝试与争议复查；不会写入 Session，也不污染普通聊天。
    with numeric_v2_usage_scope() as calls:
        response = await _start_numeric_session(request)
    return with_numeric_v2_usage(response, calls)


async def _restore_selected_session(runtime, binding):
    """Expose incompatible saves for explicit ending/replacement, never for generation."""

    if await asyncio.to_thread(
        NumericV2ArchiveStore(runtime.store.root.parent.parent).pending_forget,
        runtime.engine.story_id, binding["character_id"],
    ):
        raise NumericV2RuntimeError("numeric_theater_memory_forget_pending")
    try:
        return await runtime.restore_story_session_unlocked(binding), True
    except NumericV2RuntimeError as exc:
        if str(exc) not in {"story_package_revision_mismatch", "story_package_hash_mismatch"}:
            raise
        session_id = await runtime.store.get_story_session_id(
            runtime.engine.story_id, binding["character_id"],
        )
        stored = await runtime.restore_session_for_lifecycle(session_id) if session_id else None
        if stored is not None and stored.session.story_package_id != runtime.engine.story_id:
            raise NumericV2RuntimeError("story_package_id_mismatch")
        return stored, False


async def _start_numeric_session(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload, error_defaults={"ok": False, "reason": "csrf_validation_failed"})
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    expected_character_id = str(payload.get("character_id") or "").strip()
    if not story_id or not session_id or not expected_character_id:
        return _error("story_id_session_id_and_character_id_required", 400)
    config_manager = get_config_manager()
    replace_existing = payload.get("replace_existing") is True
    actor_budget_profile = str(
        payload.get(
            "actor_budget_profile",
            NUMERIC_V2_DEFAULT_ACTOR_BUDGET_PROFILE,
        )
        or ""
    )
    if actor_budget_profile not in NUMERIC_V2_ACTOR_BUDGET_PROFILES:
        return _error("numeric_actor_budget_profile_invalid", 400)
    try:
        runtime = await _runtime_for_story(config_manager, story_id)
        # 先在统一锁顺序下取得角色与恢复槽位快照；已有进度可直接恢复，不浪费一次开场生成。
        async with character_config_mutation_lock, runtime.story_session_guard():
            binding = _current_catgirl_binding(config_manager)
            if binding["character_id"] != expected_character_id:
                raise ValueError("catgirl_changed_requires_new_session")
            existing, compatible = await _restore_selected_session(runtime, binding)
            if existing is not None:
                _ensure_current_catgirl(existing.session, config_manager)
                # 同一 character_id 的改名或角色卡更新只能刷新演绎上下文，不能替换进度。
                if not replace_existing:
                    return {
                        "ok": True,
                        "resumed": True,
                        **_numeric_payload(runtime, existing, display_binding=binding, story_projection_compatible=compatible),
                    }
                if session_id == existing.session.session_id:
                    return _error("numeric_replacement_session_id_must_differ", 400)
                if existing.session.status != "ended":
                    return _error("numeric_active_session_cannot_restart", 409)

        # 开场模型调用不占用角色或剧本锁；最终提交前会重新读取并验证全部可变事实。
        opening = await generate_validated_opening(
            engine=runtime.engine,
            config_manager=config_manager,
            session_id=session_id,
            catgirl_binding=binding,
            actor_budget_profile=actor_budget_profile,
        )
        async with character_config_mutation_lock, runtime.story_session_guard():
            # Actor 调用期间剧本可能已被删除或同 ID 重装；提交前必须复验原包仍是当前事实。
            current_engine = await asyncio.to_thread(
                NumericV2PackageRegistry(
                    _numeric_root(config_manager) / "numeric_v2" / "packages"
                ).load_engine,
                story_id,
            )
            if current_engine.compiled.package_hash != runtime.engine.compiled.package_hash:
                raise NumericV2PackageError("numeric_story_changed_during_start")
            if _current_catgirl_binding(config_manager) != binding:
                raise ValueError("catgirl_changed_requires_new_session")
            existing, compatible = await _restore_selected_session(runtime, binding)
            if existing is not None:
                _ensure_current_catgirl(existing.session, config_manager)
                # 等待 Actor 期间另一个开始请求可能已经提交；沿用原有“继续已有进度”语义。
                if not replace_existing:
                    return {
                        "ok": True,
                        "resumed": True,
                        **_numeric_payload(runtime, existing, display_binding=binding, story_projection_compatible=compatible),
                    }
                if session_id == existing.session.session_id:
                    return _error("numeric_replacement_session_id_must_differ", 400)
                if existing.session.status != "ended":
                    return _error("numeric_active_session_cannot_restart", 409)

            await _assert_numeric_writable(config_manager, "sessions")
            if existing is not None:
                archive_store = _archive_store(config_manager)
                previous_receipt = await archive_store.aload_for_session(
                    existing.session.session_id
                )
                needs_legacy_archive = (
                    previous_receipt is not None
                    and previous_receipt.get("status") == "written"
                ) or await asyncio.to_thread(
                    archive_store.has_written_receipt_for_session,
                    existing.session.session_id,
                )
                if needs_legacy_archive:
                    previous_public = _numeric_payload(
                        runtime,
                        existing,
                        display_binding=binding,
                        story_projection_compatible=compatible,
                    )
                    # 兼容升级前已经写入记忆、但尚未生成冷档案的 Session；
                    # 冷档案落盘失败时不能继续删除旧恢复槽位。
                    await archive_store.awrite_public_archive(
                        title=str(runtime.engine.story["meta"]["title"]),
                        session=existing.session,
                        ending=(previous_public["scene"] or {}).get("ending"),
                    )
                stored = await runtime.replace_active_session(
                    previous_session_id=existing.session.session_id,
                    session_id=session_id,
                    catgirl_binding=binding,
                    opening_performance=opening,
                    actor_budget_profile=actor_budget_profile,
                )
                # 新 Session 已原子接管恢复槽位，旧 Session 回执不再有任何合法消费者。
                try:
                    await archive_store.mutate(
                        archive_store.delete_session_receipts,
                        existing.session.session_id,
                    )
                except (NumericV2ArchiveError, OSError):
                    # 新 Session 和恢复槽位已经提交；旧回执清理失败只能延后维护，不能把成功重开报成失败。
                    logger.warning(
                        "Numeric v2 重新开始后清理旧回执失败: %s",
                        existing.session.session_id,
                        exc_info=True,
                    )
            else:
                # 开场 Actor 成功后才创建 Session，避免空壳 Session 污染恢复指针。
                stored = await runtime.start_session(
                    session_id=session_id,
                    catgirl_binding=binding,
                    opening_performance=opening,
                    actor_budget_profile=actor_budget_profile,
                )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2SessionExistsError:
        return _error("numeric_session_exists", 409)
    except NumericV2ActorUnavailableError:
        return _error("numeric_v2_actor_unavailable", 503)
    except NumericV2ActorError:
        return _error("numeric_v2_actor_failed", 502)
    except ValueError as exc:
        if str(exc) == "catgirl_changed_requires_new_session":
            return _error(str(exc), 409)
        return _error(str(exc), 400)
    except (NumericV2StoreError, NumericV2RuntimeError) as exc:
        return _error(str(exc), 400)
    return {"ok": True, **_numeric_payload(runtime, stored, display_binding=binding)}


@router.get("/session/active")
async def get_active_numeric_session(story_id: str):
    config_manager = get_config_manager()
    try:
        runtime = await _runtime_for_story(config_manager, str(story_id or "").strip())
        # 恢复、身份复验与补建回执必须共用角色锁→故事锁，避免删除完成后重新写回孤立回执。
        async with character_config_mutation_lock, runtime.story_session_guard():
            binding = _current_catgirl_binding(config_manager)
            stored, compatible = await _restore_selected_session(runtime, binding)
            if stored is None:
                return _error("numeric_session_not_found", 404)
            binding = _ensure_current_catgirl(stored.session, config_manager)
            receipt = (
                await _create_ended_receipt(config_manager, stored.session)
                if stored.session.status == "ended"
                else None
            )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2StoreError as exc:
        return _error(str(exc), 422)
    except ValueError as exc:
        return _error(str(exc), 409)
    return {
        "ok": True,
        "resumed": True,
        **_numeric_payload(
            runtime,
            stored,
            end_receipt=receipt,
            display_binding=binding,
            story_projection_compatible=compatible,
        ),
    }


@router.get("/session/{session_id}")
async def get_numeric_session(session_id: str, story_id: str):
    config_manager = get_config_manager()
    try:
        runtime = await _runtime_for_story(config_manager, str(story_id or "").strip())
        # 指定 Session 的恢复也要覆盖回执写入，保持与角色、剧本删除事务相同的锁序。
        async with character_config_mutation_lock, runtime.story_session_guard():
            stored = await runtime.restore_session(session_id)
            if (
                stored is None
                or stored.session.story_package_id != runtime.engine.story_id
            ):
                # Session ID 是全局定位符，但指定恢复入口只能投影请求剧本自己的节点。
                return _error("numeric_session_not_found", 404)
            binding = _ensure_current_catgirl(stored.session, config_manager)
            receipt = (
                await _create_ended_receipt(config_manager, stored.session)
                if stored.session.status == "ended"
                else None
            )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2StoreError as exc:
        return _error(str(exc), 422)
    except ValueError as exc:
        return _error(str(exc), 409)
    return {
        "ok": True,
        **_numeric_payload(
            runtime,
            stored,
            end_receipt=receipt,
            display_binding=binding,
        ),
    }


@router.post("/session/input")
async def submit_numeric_input(request: Request):
    # 请求级统计包含失败尝试与争议复查；不会写入 Session，也不污染普通聊天。
    with numeric_v2_usage_scope() as calls:
        response = await _submit_numeric_input(request)
    return with_numeric_v2_usage(response, calls)


async def _submit_numeric_input(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload, error_defaults={"ok": False, "reason": "csrf_validation_failed"})
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not story_id or not session_id:
        return _error("story_id_and_session_id_required", 400)
    try:
        turn = TurnRequestV2.from_mapping(payload)
        config_manager = get_config_manager()
        runtime = await _runtime_for_story(config_manager, story_id)
        current = await runtime.restore_session(session_id)
        if current is None:
            return _error("numeric_session_not_found", 404)
        current_binding = _ensure_current_catgirl(current.session, config_manager)
        # 幂等重放直接返回已提交快照，不能再次调用模型或重复结算。
        if turn.client_turn_id in current.session.processed_client_turn_ids:
            replay_receipt = (
                await _create_receipt_for_existing_ended_session(
                    config_manager,
                    runtime,
                    current.session,
                )
                if current.session.status == "ended"
                else None
            )
            return {
                "ok": True,
                "idempotent_replay": True,
                **_numeric_payload(
                    runtime,
                    current,
                    end_receipt=replay_receipt,
                    display_binding=current_binding,
                ),
            }
        if current.session.status == "ended":
            return _error("session_already_ended", 409)
        if turn.base_revision != current.session.revision:
            return _error("numeric_base_revision_mismatch", 409)
        if (
            turn.input_source == "suggestion"
            and turn.message not in _current_suggested_inputs(current.session)
        ):
            # 推荐来源只能引用当前 revision 已公开的按钮；过期或伪造文本不能改变 Actor 节奏。
            return _error("numeric_suggested_input_not_current", 409)
        # HTTP 层只完成请求前置校验和错误映射，模型顺序与原子提交由应用工作流固定。
        workflow = await execute_numeric_v2_turn(
            config_manager=config_manager,
            runtime=runtime,
            current=current,
            turn=turn,
            ensure_current_binding=lambda session: _ensure_current_catgirl(
                session,
                config_manager,
            ),
            before_commit=lambda: _assert_numeric_writable(
                config_manager,
                "sessions",
            ),
        )
        outcome = workflow.outcome
        performance = workflow.performance
        stored = workflow.stored
        current_binding = workflow.display_binding
        end_receipt = None
        if stored.session.status == "ended":
            end_receipt = await _create_receipt_for_existing_ended_session(
                config_manager,
                runtime,
                stored.session,
            )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except (NumericV2RevisionConflictError, NumericV2StoreRevisionConflictError) as exc:
        if str(exc) == "session_already_ended":
            return _error("session_already_ended", 409)
        return _error("numeric_base_revision_mismatch", 409)
    except NumericV2DuplicateTurnError:
        return _error("numeric_duplicate_client_turn_id", 409)
    except NumericV2SessionNotFoundError:
        return _error("numeric_session_not_found", 404)
    except NumericV2EvaluatorUnavailableError:
        return _error("numeric_v2_evaluator_unavailable", 503)
    except NumericV2EvaluatorError:
        return _error("numeric_v2_evaluator_failed", 502)
    except NumericV2ActorUnavailableError:
        return _error("numeric_v2_actor_unavailable", 503)
    except NumericV2ActorError:
        return _error("numeric_v2_actor_failed", 502)
    except ValueError as exc:
        if str(exc) in {
            "catgirl_changed_requires_new_session",
            "catgirl_profile_changed_requires_retry",
        }:
            return _error(str(exc), 409)
        return _error(str(exc), 400)
    except (NumericV2RuntimeError, NumericV2StoreError) as exc:
        return _error(str(exc), 400)
    return {
        "ok": True,
        "resolved_turn": {
            "route_status": outcome.route_status,
            "route_changed": outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"],
        },
        "performance": _public_performance(performance),
        **_numeric_payload(
            runtime,
            stored,
            end_receipt=end_receipt,
            display_binding=current_binding,
        ),
    }


@router.post("/session/end")
async def end_numeric_session(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload, error_defaults={"ok": False, "reason": "csrf_validation_failed"})
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    base_revision = payload.get("base_revision")
    base_lifecycle_revision = payload.get("base_lifecycle_revision")
    if (
        not story_id
        or not session_id
        or isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision < 0
        or isinstance(base_lifecycle_revision, bool)
        or not isinstance(base_lifecycle_revision, int)
        or base_lifecycle_revision < 0
    ):
        return _error("numeric_session_end_request_invalid", 400)
    try:
        config_manager = get_config_manager()
        runtime = await _runtime_for_story(config_manager, story_id)
        # 结束状态与回执必须和当前角色事实一起提交，锁序固定为角色锁→故事锁。
        async with character_config_mutation_lock, runtime.story_session_guard():
            story_projection_compatible = True
            try:
                current = await runtime.restore_session(session_id)
            except NumericV2RuntimeError as exc:
                if str(exc) not in {
                    "story_package_revision_mismatch",
                    "story_package_hash_mismatch",
                }:
                    raise
                # 结束只改变生命周期。剧本升级后的旧 Session 允许收尾，
                # 但不会进入继续演绎或用新剧本投影旧节点。
                current = await runtime.restore_session_for_lifecycle(session_id)
                story_projection_compatible = False
            if current is None:
                return _error("numeric_session_not_found", 404)
            current_binding = _ensure_current_catgirl(
                current.session,
                config_manager,
            )
            idempotent_replay = current.session.status == "ended"
            if idempotent_replay:
                # Session 已提交但回执写入失败时只补建回执；仅相邻生命周期允许幂等重放。
                if (
                    current.session.revision != base_revision
                    or current.session.ended_reason != "user_exit"
                    or current.session.lifecycle_revision != base_lifecycle_revision + 1
                ):
                    raise NumericV2StoreRevisionConflictError("numeric_base_revision_mismatch")
                stored = current
            else:
                await _assert_numeric_writable(config_manager, "sessions")
                stored = await runtime.end_session(
                    session_id,
                    base_revision=base_revision,
                    base_lifecycle_revision=base_lifecycle_revision,
                    reason="user_exit",
                )
            receipt = await _create_ended_receipt(
                config_manager,
                stored.session,
            )
        if idempotent_replay:
            return {
                "ok": True,
                "idempotent_replay": True,
                **_numeric_payload(
                    runtime,
                    current,
                    end_receipt=receipt,
                    display_binding=current_binding,
                    story_projection_compatible=story_projection_compatible,
                ),
            }
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2StoreRevisionConflictError:
        return _error("numeric_base_revision_mismatch", 409)
    except NumericV2SessionNotFoundError:
        return _error("numeric_session_not_found", 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    except (NumericV2StoreError, NumericV2RuntimeError) as exc:
        return _error(str(exc), 400)
    return {
        "ok": True,
        **_numeric_payload(
            runtime,
            stored,
            end_receipt=receipt,
            display_binding=current_binding,
            story_projection_compatible=story_projection_compatible,
        ),
    }


@router.post("/session/resume")
async def resume_numeric_session(request: Request):
    """继续玩家主动退出的演绎；剧情自然结局不能从该入口恢复。"""  # noqa: DOCSTRING_CJK

    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    base_revision = payload.get("base_revision")
    base_lifecycle_revision = payload.get("base_lifecycle_revision")
    if (
        not story_id
        or not session_id
        or isinstance(base_revision, bool)
        or not isinstance(base_revision, int)
        or base_revision < 0
        or isinstance(base_lifecycle_revision, bool)
        or not isinstance(base_lifecycle_revision, int)
        or base_lifecycle_revision < 0
    ):
        return _error("numeric_session_resume_request_invalid", 400)
    try:
        config_manager = get_config_manager()
        runtime = await _runtime_for_story(config_manager, story_id)
        # 恢复状态与当前角色属于同一份可变事实，必须在统一锁顺序内读取并复验。
        async with character_config_mutation_lock, runtime.story_session_guard():
            current = await runtime.restore_session(session_id)
            if current is None:
                return _error("numeric_session_not_found", 404)
            current_binding = _ensure_current_catgirl(current.session, config_manager)
            await _assert_numeric_writable(config_manager, "sessions")
            stored = await runtime.resume_session(
                session_id,
                base_revision=base_revision,
                base_lifecycle_revision=base_lifecycle_revision,
            )
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2StoreRevisionConflictError:
        return _error("numeric_base_revision_mismatch", 409)
    except NumericV2SessionNotFoundError:
        return _error("numeric_session_not_found", 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    except (NumericV2StoreError, NumericV2RuntimeError) as exc:
        return _error(str(exc), 409)
    return {
        "ok": True,
        "resumed": True,
        **_numeric_payload(runtime, stored, display_binding=current_binding),
    }


@router.post("/session/speak-block")
async def speak_numeric_block(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    playback_request_id = str(payload.get("playback_request_id") or "").strip()
    revision = payload.get("revision")
    block_index = payload.get("block_index")
    dialogue_block_indexes = payload.get("dialogue_block_indexes")
    if (
        not story_id
        or not session_id
        or not playback_request_id
        or len(playback_request_id) > 160
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or isinstance(block_index, bool)
        or not isinstance(block_index, int)
        or block_index < 0
        or (
            dialogue_block_indexes is not None
            and (
                not isinstance(dialogue_block_indexes, list)
                or not 1 <= len(dialogue_block_indexes) <= MAX_CONTENT_BLOCKS
                or any(
                    isinstance(index, bool) or not isinstance(index, int) or index < 0
                    for index in dialogue_block_indexes
                )
                or dialogue_block_indexes != sorted(set(dialogue_block_indexes))
                or dialogue_block_indexes[0] != block_index
            )
        )
    ):
        return _error("numeric_speak_block_request_invalid", 400)
    lock = _request_lock(_speak_request_locks, playback_request_id)
    async with lock:
        previous = _speak_request_results.get(playback_request_id)
        if previous is not None:
            return dict(previous)
        try:
            config_manager = get_config_manager()
            runtime = await _runtime_for_story(config_manager, story_id)
            stored = await runtime.restore_session(session_id)
            if stored is None:
                return _error("numeric_session_not_found", 404)
            current_binding = _ensure_current_catgirl(stored.session, config_manager)
            if stored.session.revision != revision:
                return _error("numeric_base_revision_mismatch", 409)
            if revision == 0:
                performance = stored.session.opening_performance
            else:
                history_index = revision - 1
                if history_index >= len(stored.session.performance_history):
                    return _error("numeric_speak_block_not_found", 404)
                performance = stored.session.performance_history[history_index]
                if int(performance.get("revision") or -1) != revision:
                    return _error("numeric_speak_block_not_found", 404)
            block_group = _performance_block_group(performance, block_index)
            if block_group is None:
                return _error("numeric_speak_block_not_found", 404)
            blocks, block_offset = block_group
            local_block_index = block_index - block_offset
            block = blocks[local_block_index]
            if block.get("type") != "dialogue" or block.get("speaker_id") != "active_catgirl":
                return _error("numeric_speak_block_not_dialogue", 422)
            requested_indexes = dialogue_block_indexes or [block_index]
            dialogue_parts: list[str] = []
            for requested_index in requested_indexes:
                requested_group = _performance_block_group(performance, requested_index)
                if requested_group is None:
                    return _error("numeric_speak_block_not_found", 404)
                requested_blocks, requested_offset = requested_group
                if requested_offset != block_offset:
                    return _error("numeric_speak_blocks_cross_segment", 422)
                requested_block = requested_blocks[requested_index - requested_offset]
                if (
                    requested_block.get("type") != "dialogue"
                    or requested_block.get("speaker_id") != "active_catgirl"
                ):
                    return _error("numeric_speak_block_not_dialogue", 422)
                dialogue_parts.append(str(requested_block.get("text") or "").strip())
            # 括号动作已经被内容块解析器剔除；这里只合并前端声明且后端复验通过的对白块。
            dialogue_text = " ".join(dialogue_parts).strip()
            all_blocks = performance_content_blocks(performance)
            first_dialogue_index = next(
                (index for index, item in enumerate(all_blocks) if item.get("type") == "dialogue"),
                block_index,
            )
            from main_routers.shared_state import get_session_manager

            # 最终身份和 Session 复验与 TTS 入队共用生命周期锁；角色切换、剧本删除或
            # Session 替换只能发生在旧对白入队前或入队后，不能在清理完成后迟到写回。
            async with character_config_mutation_lock, runtime.story_session_guard():
                latest = await runtime.restore_session(session_id)
                if latest is None:
                    return _error("numeric_session_not_found", 404)
                current_binding = _ensure_current_catgirl(
                    latest.session,
                    config_manager,
                )
                if latest.session.revision != revision:
                    return _error("numeric_base_revision_mismatch", 409)
                if revision == 0:
                    latest_performance = latest.session.opening_performance
                else:
                    latest_history_index = revision - 1
                    if latest_history_index >= len(latest.session.performance_history):
                        return _error("numeric_speak_block_not_found", 404)
                    latest_performance = latest.session.performance_history[
                        latest_history_index
                    ]
                if latest_performance != performance:
                    return _error("numeric_speak_block_not_found", 404)
                result = await speak_committed_line(
                    dialogue_text,
                    session_id=session_id,
                    state_revision=revision,
                    # TTS 必须使用 character_id 解析出的当前名称；角色改名后旧名称已无语音配置。
                    lanlan_name=str(current_binding.get("catgirl_name") or ""),
                    resolve_current_catgirl=lambda: _current_catgirl_binding(config_manager)["catgirl_name"],
                    get_session_manager=get_session_manager,
                    metadata_kind="theater_numeric_v2_dialogue_block",
                    request_id=playback_request_id,
                    interrupt_audio=block_index == first_dialogue_index,
                )
            response = {
                "ok": True,
                "block_index": block_index,
                "dialogue_block_count": len(dialogue_parts),
                **result,
            }
            if block_index == first_dialogue_index:
                response["first_dialogue"] = True
            _speak_request_results[playback_request_id] = response
            if len(_speak_request_results) > 512:
                expired_request_id = next(iter(_speak_request_results))
                _speak_request_results.pop(expired_request_id)
                _speak_request_locks.pop(expired_request_id, None)
            return response
        except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
            return _package_error(exc)
        except (NumericV2StoreError, NumericV2RuntimeError, ValueError) as exc:
            return _error(str(exc), 409)


async def _validated_receipt(
    store: NumericV2ArchiveStore,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """异步读取并校验客户端只能引用本次已结束 Session 的回执。"""  # noqa: DOCSTRING_CJK

    receipt = await store.aload(str(payload.get("end_receipt_id") or "").strip())
    if receipt is None:
        raise NumericV2ArchiveError("numeric_end_receipt_not_found")
    for field, value in (
        ("story_id", str(payload.get("story_id") or "").strip()),
        ("session_id", str(payload.get("session_id") or "").strip()),
        ("revision", payload.get("revision")),
    ):
        if receipt.get(field) != value:
            raise NumericV2ArchiveError("numeric_end_receipt_mismatch")
    return receipt


@router.post("/session/archive")
async def archive_numeric_session(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    archive_request_id = str(payload.get("archive_request_id") or "").strip()
    if not archive_request_id or len(archive_request_id) > 160:
        return _error("numeric_archive_request_invalid", 400)
    lock = _request_lock(
        _archive_request_locks,
        str(payload.get("end_receipt_id") or ""),
    )
    async with lock:
        store: NumericV2ArchiveStore | None = None
        receipt: dict[str, Any] | None = None
        try:
            config_manager = get_config_manager()
            store = _archive_store(config_manager)
            receipt = await _validated_receipt(store, payload)
            if await asyncio.to_thread(store.pending_forget, receipt["story_id"], receipt.get("character_id", "")):
                return _error("numeric_theater_memory_forget_pending", 409)
            if receipt.get("status") == "written":
                # 上次进程可能在 written 回执与 Session 水位两次原子写之间中断；
                # 幂等重试返回成功前先修复水位，避免续演后重复归档旧回合。
                await _assert_numeric_writable(config_manager, "archives")
                await store.areconcile_written_receipt(receipt)
                return {"ok": True, "status": "already_written"}
            if receipt.get("status") == "skipped":
                return _error("numeric_archive_already_skipped", 409)
            expected_request_id = str(receipt.get("archive_request_id") or "")
            if expected_request_id and archive_request_id != expected_request_id:
                return _error("numeric_archive_request_mismatch", 409)
            runtime = await _runtime_for_story(config_manager, receipt["story_id"])
            # 角色生命周期锁固定记忆目录名称；故事锁避免剧本删除完成后又写回孤立冷档案。
            async with character_config_mutation_lock, runtime.story_session_guard():
                if await asyncio.to_thread(store.pending_forget, receipt["story_id"], receipt.get("character_id", "")):
                    return _error("numeric_theater_memory_forget_pending", 409)
                compatible = True
                try:
                    stored = await runtime.restore_session(receipt["session_id"])
                except NumericV2RuntimeError as exc:
                    if str(exc) not in {"story_package_revision_mismatch", "story_package_hash_mismatch"}:
                        raise
                    stored = await runtime.restore_session_for_lifecycle(receipt["session_id"])
                    compatible = False
                if stored is None:
                    return _error("numeric_session_not_found", 404)
                current_binding = _ensure_current_catgirl(stored.session, config_manager)
                if receipt.get("character_id") != current_binding.get("character_id"):
                    return _error("numeric_end_receipt_character_mismatch", 409)
                if stored.session.status != "ended" or stored.session.revision != receipt["revision"]:
                    return _error("numeric_archive_session_not_ended", 409)
                public = _numeric_payload(
                    runtime,
                    stored,
                    display_binding=current_binding,
                    story_projection_compatible=compatible,
                )
                title = str(runtime.engine.story["meta"]["title"])
                messages = build_numeric_v2_memory_messages(
                    title=title,
                    session=stored.session,
                    ending=(public["scene"] or {}).get("ending"),
                    archive_from_revision=int(receipt.get("archive_from_revision") or 1),
                    archive_through_revision=int(
                        receipt.get("archive_through_revision") or stored.session.revision
                    ),
                    include_opening=bool(receipt.get("include_opening", True)),
                )
                from config import MEMORY_SERVER_PORT
                from utils.internal_http_client import get_internal_http_client

                await _assert_numeric_writable(config_manager, "archives")
                receipt = await store.aupdate(
                    receipt,
                    status="writing",
                    archive_request_id=archive_request_id,
                )
                # 先落不含隐藏状态的完整冷档案，再让记忆服务迁移旧版全文；
                # 这样旧时间索引被折叠时，公开演绎仍有可恢复副本。
                await store.astage_public_archive(
                    receipt=receipt,
                    title=title,
                    session=stored.session,
                    ending=(public["scene"] or {}).get("ending"),
                )
                response = await get_internal_http_client().post(
                    f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cache/{quote(current_binding['catgirl_name'], safe='')}",
                    # 记忆服务使用同一稳定键收敛未知响应和跨进程重试，不能只在剧场侧去重。
                    json={
                        "input_history": json.dumps(messages, ensure_ascii=False),
                        "idempotency_key": archive_request_id,
                    },
                    timeout=8.0,
                )
                data = response.json() if response.content else {}
                if not response.is_success or data.get("status") == "error":
                    await store.aupdate(receipt, status="pending")
                    return _error("numeric_archive_memory_failed", 502)
                await store.acommit_staged_public_archive(receipt)
                await store.aupdate(
                    receipt,
                    status="written",
                    archive_request_id=archive_request_id,
                )
                return {"ok": True, "status": "written", "count": data.get("count")}
        except NumericV2ArchiveError as exc:
            return _error(str(exc), 409)
        except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
            return _package_error(exc)
        except MaintenanceModeError:
            raise
        except Exception:
            if store is not None and receipt is not None and receipt.get("status") == "writing":
                try:
                    await store.aupdate(receipt, status="pending")
                except NumericV2ArchiveError:
                    pass
            return _error("numeric_archive_memory_failed", 502)


@router.post("/session/archive/skip")
async def skip_numeric_session_archive(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    lock = _request_lock(
        _archive_request_locks,
        str(payload.get("end_receipt_id") or ""),
    )
    async with lock:
        try:
            config_manager = get_config_manager()
            story_id = str(payload.get("story_id") or "").strip()
            NumericV2PackageRegistry(
                _numeric_root(config_manager) / "numeric_v2" / "packages"
            ).package_path(story_id)
            # 跳过归档与角色/剧本删除使用相同锁顺序，防止删除后重新创建孤立回执。
            async with character_config_mutation_lock, numeric_v2_story_session_guard(
                _numeric_root(config_manager),
                story_id,
            ):
                store = _archive_store(config_manager)
                receipt = await _validated_receipt(store, payload)
                binding = _current_catgirl_binding(config_manager)
                if receipt.get("character_id") != binding["character_id"]:
                    return _error("numeric_end_receipt_character_mismatch", 409)
                if receipt.get("status") == "written":
                    return _error("numeric_archive_already_written", 409)
                await _assert_numeric_writable(config_manager, "end_receipts")
                await store.adiscard_staged_public_archive(
                    str(receipt.get("receipt_id") or "")
                )
                await store.aupdate(receipt, status="skipped")
                return {"ok": True, "status": "skipped"}
        except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
            return _package_error(exc)
        except NumericV2ArchiveError as exc:
            return _error(str(exc), 409)


@router.get("/memory/archives")
async def list_numeric_memory_archives(story_id: str):
    """列出当前猫娘在指定剧本中的冷档案摘要。"""  # noqa: DOCSTRING_CJK

    config_manager = get_config_manager()
    normalized_story_id = str(story_id or "").strip()
    try:
        await _runtime_for_story(config_manager, normalized_story_id)
        binding = _current_catgirl_binding(config_manager)
        archives = await asyncio.to_thread(
            _archive_store(config_manager).list_public_archives,
            story_id=normalized_story_id,
            character_id=binding["character_id"],
            legacy_catgirl_name=binding["catgirl_name"],
        )
        public_archives = [
            {
                key: value
                for key, value in archive.items()
                if key not in {"path", "modified_ns"}
            }
            for archive in archives
        ]
        return {"ok": True, "archives": public_archives}
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2ArchiveError as exc:
        return _error(str(exc), 500)


@router.get("/memory/archive")
async def get_numeric_memory_archive(story_id: str, session_id: str):
    """读取当前猫娘有权查看的一份完整公开演绎。"""  # noqa: DOCSTRING_CJK

    normalized_story_id = str(story_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    if not normalized_story_id or not normalized_session_id:
        return _error("story_id_and_session_id_required", 400)
    config_manager = get_config_manager()
    try:
        runtime = await _runtime_for_story(config_manager, normalized_story_id)
        # 读取与角色改名、角色删除和剧本删除串行，避免校验身份后文件立即迁移或消失。
        async with character_config_mutation_lock, runtime.story_session_guard():
            binding = _current_catgirl_binding(config_manager)
            archive = await asyncio.to_thread(
                _archive_store(config_manager).load_public_archive,
                story_id=normalized_story_id,
                session_id=normalized_session_id,
                character_id=binding["character_id"],
                legacy_catgirl_name=binding["catgirl_name"],
            )
        return {"ok": True, "archive": archive}
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2ArchiveError as exc:
        return _error(str(exc), 404)


@router.post("/memory/archive/pin")
async def pin_numeric_memory_archive(request: Request):
    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not story_id or not session_id or not isinstance(payload.get("pinned"), bool):
        return _error("numeric_public_archive_pin_invalid", 400)
    config_manager = get_config_manager()
    try:
        runtime = await _runtime_for_story(config_manager, story_id)
        # 置顶会改写角色归属的冷档案，锁顺序与归档、遗忘和角色删除保持一致。
        async with character_config_mutation_lock, runtime.story_session_guard():
            await _assert_numeric_writable(config_manager, "public_archives")
            binding = _current_catgirl_binding(config_manager)
            archive_store = _archive_store(config_manager)
            result = await archive_store.mutate(
                archive_store.set_public_archive_pinned,
                story_id=story_id,
                session_id=session_id,
                character_id=binding["character_id"],
                legacy_catgirl_name=binding["catgirl_name"],
                pinned=payload["pinned"],
            )
        return {"ok": True, "archive": result}
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except NumericV2ArchiveError as exc:
        return _error(str(exc), 404)


@router.post("/memory/forget")
async def forget_numeric_story_memory(request: Request):
    """显式忘记当前猫娘的指定剧本，不删除剧本包或 Session。"""  # noqa: DOCSTRING_CJK

    payload = await _json_object(request)
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )
    if validation_error is not None:
        return validation_error
    story_id = str(payload.get("story_id") or "").strip()
    expected_character_id = str(payload.get("character_id") or "").strip()
    if not story_id or not expected_character_id:
        return _error("story_id_and_character_id_required", 400)
    config_manager = get_config_manager()
    try:
        # 仅用注册表路径规则复验 ID；剧本包删除后仍必须允许清理其残留记忆。
        NumericV2PackageRegistry(
            _numeric_root(config_manager) / "numeric_v2" / "packages"
        ).package_path(story_id)
        try:
            runtime = await _runtime_for_story(config_manager, story_id)
        except NumericV2PackageNotFoundError:
            runtime = None
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        # 遗忘同样写入角色记忆目录，必须与角色改名串行；独立故事锁不依赖剧本包存在。
        async with character_config_mutation_lock, numeric_v2_story_session_guard(
            _numeric_root(config_manager),
            story_id,
        ):
            binding = _current_catgirl_binding(config_manager)
            if binding["character_id"] != expected_character_id:
                return _error("catgirl_changed_requires_refresh", 409)
            await _assert_numeric_writable(config_manager, "memory")
            archive_store = _archive_store(config_manager)
            archive_scope = {
                "story_id": story_id,
                "character_id": binding["character_id"],
                "legacy_catgirl_name": binding["catgirl_name"],
            }
            session_id = await runtime.store.get_story_session_id(story_id, binding["character_id"]) if runtime else ""
            stored = await runtime.restore_session_for_lifecycle(session_id) if session_id else None
            if stored is not None:
                _ensure_current_catgirl(stored.session, config_manager)
            pending = await archive_store.mutate(
                archive_store.prepare_forget, **archive_scope,
                session=stored.session if stored is not None else None,
            )
            if runtime is not None and pending["session_id"]:
                target = await runtime.restore_session_for_lifecycle(pending["session_id"])
                if target is not None:
                    _ensure_current_catgirl(target.session, config_manager)
                    # The frozen boundary survives retries; later turns are not forgotten.
                    stored = await runtime.store.forget_history_through_current_revision(
                        target.session.session_id, through_revision=pending["through_revision"],
                    )
            response = await get_internal_http_client().post(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/internal/memory/"
                f"{quote(binding['catgirl_name'], safe='')}/theater/forget",
                json={"story_id": story_id},
                timeout=8.0,
            )
            data = response.json() if response.content else {}
            if not response.is_success or data.get("ok") is not True:
                return _error("numeric_theater_memory_forget_failed", 502)
            await archive_store.mutate(archive_store.delete_forget_files, pending)
            removed_archives = len(pending["archive_files"])
            removed_receipts = len(pending["receipt_files"])
            if stored is not None and stored.session.status == "ended":
                # 保留一个最新“不写入”决策回执，防止选剧页立即再次询问。
                skipped = await archive_store.acreate_or_get(stored.session)
                await archive_store.aupdate(skipped, status="skipped")
            # Removing the intent is the last step; every preceding operation is retryable.
            await archive_store.mutate(archive_store.complete_forget, story_id, binding["character_id"])
        return {
            "ok": True,
            "removed_recent": int(data.get("removed_recent") or 0),
            "removed_time_index": int(data.get("removed_time_index") or 0),
            "removed_archives": removed_archives,
            "removed_receipts": removed_receipts,
        }
    except (NumericV2PackageError, NumericV2PackageNotFoundError) as exc:
        return _package_error(exc)
    except MaintenanceModeError:
        raise
    except Exception:
        return _error("numeric_theater_memory_forget_failed", 502)


__all__ = ["router"]
