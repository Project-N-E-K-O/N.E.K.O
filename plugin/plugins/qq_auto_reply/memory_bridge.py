from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class QQMemoryQueryResult:
    text: str = ""
    hit_count: int = 0
    elapsed_ms: float = 0.0
    raw_results: list[dict[str, Any]] = field(default_factory=list)
    # text 里实际渲染出的条目数（预算截断后）：hit_count 是检索命中数，
    # 两者在预算丢弃尾部条目时会不同。消费方给模型报条数必须用它——
    # 记忆原文可含 "N. " 开头的行，从 text 反解会数错。
    rendered_count: int = 0


class QQMemoryBridge:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    @staticmethod
    def _client():
        """The process-wide client for internal 127.0.0.1 services.

        Every endpoint used to build and tear down its own AsyncClient, and
        each construction eagerly initializes an SSLContext even for plain
        http to localhost — the reason utils/http/internal_client.py exists.
        A busy group does at least two of these per turn and a member drain
        fires eight at once. Its lifetime is the process's (main_server's
        shutdown hook closes it), so nothing here may close it.

        The timeout has to be passed **per request**: it differs by endpoint
        (scoped history waits on an LLM extraction, the rest are local
        reads), and the shared client carries an unrelated default."""
        from utils.internal_http_client import get_internal_http_client

        return get_internal_http_client()

    def _base_url(self) -> str:
        from config import MEMORY_SERVER_PORT

        return f"http://127.0.0.1:{MEMORY_SERVER_PORT}"

    #: The one place the platform literal lives. Subject builders below keep
    #: composing their ids by hand ON PURPOSE — rewriting a subject_id would
    #: make every stored scoped memory and persona section an unreachable
    #: orphan, since attribution is byte equality of ``(key, scope)``.
    PLATFORM = "qq"

    @classmethod
    def speaker_account_id(cls, sender_id: object) -> str:
        """``platform:actor`` for the trust pool. Byte-identical to today."""
        return f"{cls.PLATFORM}:{str(sender_id or '').strip()}"

    @staticmethod
    def group_subject(group_id: object) -> dict[str, str]:
        return {
            "subject_kind": "group_chat",
            "subject_id": f"qq:{str(group_id or '').strip()}",
        }

    @staticmethod
    def group_participant_subject(group_id: object, sender_id: object) -> dict[str, str]:
        return {
            "subject_kind": "group_participant",
            "subject_id": (
                f"qq:{str(group_id or '').strip()}:{str(sender_id or '').strip()}"
            ),
        }

    @staticmethod
    def participant_subject(sender_id: object) -> dict[str, str]:
        """非 admin QQ 私聊对象的独立记忆主体（无群维度）。

        与群成员的 group_participant 平行：同一个人在群里与私聊里是两个
        隔离域（scope 由 subject_id 派生），跨域合并是单独的产品决定，
        不在 schema 层顺手做。"""
        return {
            "subject_kind": "participant",
            "subject_id": f"qq:{str(sender_id or '').strip()}",
        }

    async def fetch_bootstrap_memory(
        self,
        her_name: str,
        *,
        language: str | None = None,
        timeout: float = 5.0,
    ) -> str:
        from utils.language_utils import is_supported_language_code

        # Only a caller-supplied locale has explicit provenance.  With no
        # session locale, omission lets /new_dialog restore durable state.
        request_kwargs: dict[str, Any] = {"timeout": timeout}
        if is_supported_language_code(language):
            request_kwargs["params"] = {"language": language}
        client = self._client()
        response = await client.get(
            f"{self._base_url()}/new_dialog/{her_name}",
            **request_kwargs,
        )
        response.raise_for_status()
        return response.text.strip()

    async def fetch_scoped_bootstrap_memory(
        self,
        her_name: str,
        *,
        subjects: list[dict[str, str]],
        language: str | None = None,
        timeout: float = 5.0,
    ) -> str:
        if not subjects:
            return ""
        from utils.language_utils import is_supported_language_code

        # Same contract as the sibling methods: only a caller-supplied locale
        # has explicit provenance. Omitting the field lets the server restore
        # the durable per-subject locale, which the host process fallback
        # would otherwise overwrite with a coarser guess.
        payload: dict[str, Any] = {"subjects": subjects}
        if is_supported_language_code(language):
            payload["language"] = language
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_context",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.text.strip()

    async def post_scoped_mentions(
        self,
        her_name: str,
        response_text: str,
        *,
        subjects: list[dict[str, str]],
        timeout: float = 5.0,
    ) -> None:
        if not subjects or not response_text:
            return
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_mentions",
            json={"response_text": response_text, "subjects": subjects},
            timeout=timeout,
        )
        response.raise_for_status()

    async def post_scoped_forget(
        self,
        her_name: str,
        *,
        subject: dict[str, str],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Erase one subject's stored memory (facts/reflections/persona).

        删好友/退群后的撤回入口。幂等；服务端部分失败以 HTTP 错误暴露，
        重试安全。调用方自备触发时机（UI 操作/事件），bridge 只管线路。"""
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_forget",
            json={"subject": subject},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def query_relevant_memory(
        self,
        her_name: str,
        query: str,
        *,
        timeout: float = 5.0,
        limit: int = 5,
        subjects: list[dict[str, str]] | None = None,
        time_spec: str = "",
    ) -> QQMemoryQueryResult:
        # ``time_spec`` mirrors the endpoint's optional ``time`` field: alone
        # it recalls by event-time proximity; combined with a query it runs
        # the joint semantic + time search. Empty keeps the legacy shape.
        normalized_query = str(query or "").strip()
        normalized_time = str(time_spec or "").strip()
        if not normalized_query and not normalized_time:
            return QQMemoryQueryResult()
        # ``None`` means the legacy private caller omitted an authorization
        # boundary. An explicit empty list means the caller has no authorized
        # subject and must never fall back to that legacy corpus.
        if subjects == []:
            return QQMemoryQueryResult()
        request_payload: dict[str, Any] = {"query": normalized_query}
        if normalized_time:
            request_payload["time"] = normalized_time
        if subjects is not None:
            request_payload["subjects"] = subjects
        from utils.language_utils import get_global_language_full

        # Deliberately still sends the process locale, unlike the sibling
        # bootstrap/history methods. The difference is who renders: those
        # receive server-rendered text, so omitting the field lets the server
        # use the durable per-subject locale end to end. This one receives
        # *structured* rows and renders the tier/entity tags locally (see
        # render_relevant_memory), so omitting it here would only move the
        # server half to the subject locale while the tags stayed on this
        # process's — worse than today's self-consistent pair. Moving this
        # path onto the subject locale needs the resolved locale returned in
        # the response (or the tags rendered server-side); that is a response
        # contract change and belongs in its own PR.
        request_payload["language"] = get_global_language_full()
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/query_memory/{her_name}",
            json=request_payload,
            timeout=timeout,
        )
        response.raise_for_status()
        response_payload = response.json()
        results = response_payload.get("results") if isinstance(response_payload, dict) else None
        memory_items = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
        # 整段渲染扔进 worker 线程：render_relevant_memory 里的
        # truncate_to_tokens 编码的是**截断前**的原文，而这条链路存在的
        # 理由正是"上游可能返回一条超长的合并 reflection"。tiktoken 对
        # 切不开的超长 chunk 是二次退化，同步跑在事件循环上会连带卡住这
        # 个进程里其它群的回复。渲染函数本身保持同步（本体侧同构、测试
        # 直调），offload 放在唯一的 async 调用点。
        kept_count_out: list[int] = []
        rendered = await asyncio.to_thread(
            self.render_relevant_memory, memory_items[:limit],
            kept_count_out=kept_count_out,
        )
        elapsed_ms = response_payload.get("elapsed_ms", 0.0) if isinstance(response_payload, dict) else 0.0
        try:
            normalized_elapsed = float(elapsed_ms or 0.0)
        except (TypeError, ValueError):
            normalized_elapsed = 0.0
        return QQMemoryQueryResult(
            text=rendered,
            hit_count=len(memory_items),
            elapsed_ms=normalized_elapsed,
            raw_results=memory_items,
            rendered_count=kept_count_out[0] if kept_count_out else 0,
        )

    def render_relevant_memory(
        self,
        results: list[dict[str, Any]],
        *,
        kept_count_out: list[int] | None = None,
    ) -> str:
        """Render this group's recall hits through the shared entry point.

        No line building happens here. ``memory.recall_render`` is the one
        place recall results become prompt text — it carries the token
        budgets, and it carries them once so this side and the main app's
        ``recall_memory`` tool cannot drift apart (issue #2588; the two
        used to be hand-written twins). This method only supplies the
        locale, reports the drop, and adapts the result to the caller's
        out-param.

        No header: the QQ side wraps the block in ``LONG_TERM_MEMORY_SECTION``
        instead of an overview line.
        """
        from config import RECALL_RENDER_TOTAL_MAX_TOKENS
        from memory.recall_render import render_recall_block
        from utils.language_utils import get_global_language_full

        block = render_recall_block(results, get_global_language_full())
        if kept_count_out is not None:
            # out-param 而非改返回签名（与 reply_context_node 的
            # used_member_subject_out 同模式）：既有直调方不受影响。
            kept_count_out.append(block.kept)
        logger = getattr(self.plugin, "logger", None)
        if block.dropped and logger is not None:
            # 诊断行不该成为渲染的硬依赖：这个函数此前对 plugin 对象零依赖，
            # 抛 AttributeError 会被上游 execute_recall 的 except 吞掉，
            # 整段召回为了一条日志凭空消失。
            logger.info(
                f"QQ 长期记忆召回段超出 {RECALL_RENDER_TOTAL_MAX_TOKENS} tok 预算，"
                f"丢弃末尾 {block.dropped} 条"
            )
        return block.text

    async def post_memory_history(self, endpoint: str, her_name: str, messages: list[dict[str, Any]], *, timeout: float = 5.0) -> dict[str, Any]:
        # QQ currently has no explicit per-conversation locale; do not turn
        # the host process fallback into durable user evidence.
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/{endpoint}/{her_name}",
            json={
                "input_history": json.dumps(messages, ensure_ascii=False),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def post_scoped_memory_history(
        self,
        her_name: str,
        messages: list[dict[str, Any]],
        *,
        subject: dict[str, str],
        speaker_label: str | None = None,
        speaker_tier: str | None = None,
        speaker_activity_events: list[dict[str, Any]] | None = None,
        speaker_channel: str | None = None,
        speaker_id: str | None = None,
        speaker_is_owner: bool = False,
        display_name: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        # speaker_label 只在单发言人批次（成员 bucket / 私聊 participant
        # digest）传：提取 prompt 用它替代私聊主人名渲染 user 轮，避免对方
        # 发言被抽成"关于主人"的事实。群 digest 不传——内容里每条消息已带
        # 发言人头。speaker_tier 是权限档位，服务端据此自己算分并落库；插件
        # 不再持有 trust 池、不再演化、不再接收回传。display_name 是 subject
        # 的人类可读名（群名/昵称），服务端中和后刷进 persona section 元数据，
        # 渲染标题用；纯装饰性，缺省即退化裸 id。
        payload: dict[str, Any] = {
            "input_history": json.dumps(messages, ensure_ascii=False),
            "subject": subject,
        }
        if speaker_label:
            payload["speaker_label"] = speaker_label
        if speaker_tier is not None:
            payload["speaker_tier"] = speaker_tier
        if speaker_activity_events:
            payload["speaker_activity_events"] = speaker_activity_events
        if speaker_channel:
            payload["speaker_channel"] = speaker_channel
        if speaker_id:
            payload["speaker_id"] = speaker_id
        if speaker_is_owner:
            payload["speaker_is_owner"] = True
        if display_name:
            payload["display_name"] = display_name
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_history",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def post_legacy_speaker_trust(
        self,
        *,
        platform: str,
        source: str,
        profiles: dict[str, Any],
        chunk_index: int,
        final: bool,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Push one chunk of the frozen legacy trust ledger to the server.

        Character-agnostic route: trust is a property of the person, not of
        their relationship with one character.
        """
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/trust/import_legacy_profiles",
            json={
                "platform": platform,
                "source": source,
                "profiles": profiles,
                "chunk_index": chunk_index,
                "final": bool(final),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def declare_identity_scope(
        self,
        *,
        channel: str,
        actor_scope: str,
        conversation_scope: str,
        asserted_by: str,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """把本通道标识符的**协议语义**登记进服务端身份池。

        登记的是「这个连接模式的 wire format 是什么」，不是「我们观察到了
        什么」——所以这里没有、也永远不该有任何 account id 或样本参数。见
        ``adeclare_platform_identity_scope`` 的 docstring。
        """
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/identity/scope",
            json={
                "platform": self.PLATFORM,
                "channel": channel,
                "actor_scope": actor_scope,
                "conversation_scope": conversation_scope,
                "asserted_by": asserted_by,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def bind_speaker_account(
        self,
        *,
        account_id: str,
        entity_id: str,
        bound_by: str,
        require_unbound: bool = False,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """把一个 account 并入已有 entity。**只能由人触发**。

        唯一调用方是信任用户页的「合并到已有身份」——开放平台上同一个人在
        每个群是一个不同的 member_openid，跨群把信赖度并起来只有人工断言这
        一条路（见设计文档 §2.15.4.3 第 1 级）。任何自动建边（昵称、共现、
        时序、编辑距离）都被硬约束否决，不要在调用侧偷偷补上。
        """
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/identity/accounts/bind",
            json={
                "account_id": account_id,
                "entity_id": entity_id,
                "bound_by": bound_by,
                "require_unbound": bool(require_unbound),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def ensure_speaker_account(
        self, *, account_id: str, timeout: float = 10.0,
    ) -> dict[str, Any]:
        """确保一个 account 在身份池里有 entity，返回它。

        合并目标必须先有 entity 才能被 bind（服务端对未知 entity 直接
        404），而 entity 只从账本活动里诞生——新装机器上主人的私聊 account
        往往一个都没有，恰恰是所有群内 ID 要并进去的那一个。
        """
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/identity/accounts/ensure",
            json={"account_id": account_id},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def unbind_speaker_account(
        self, *, account_id: str, require_provenance: bool = False,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """把一个 account 拆回独立 entity。**误绑的唯一回滚**。

        ``require_provenance`` 让服务端在临界区里确认「这个账号确实是被绑
        过来的」，否则原样返回 ``changed=false``。UI 的撤销按钮必须带上：
        没有它，连点两下会把一个已经独立的账号反复搬进新 entity。
        """
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/identity/accounts/unbind",
            json={
                "account_id": account_id,
                "require_provenance": bool(require_provenance),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def fetch_speaker_profile(
        self, account_id: str, *, timeout: float = 10.0,
    ) -> dict[str, Any]:
        """一个 account 的只读诊断视图（entity_id + 账本权重）。

        合并候选**只能按账本权重排序**，这个方法就是权重的来源。
        """
        client = self._client()
        response = await client.get(
            f"{self._base_url()}/internal/trust/profile",
            params={"account_id": account_id},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()

    async def post_scoped_memory_history_batch(
        self,
        her_name: str,
        segments: list[dict[str, Any]],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """The batched multi-speaker shape of /scoped_history.

        ``segments``: ``[{'messages': [...], 'subject': {...},
        'speaker_label': str, 'speaker_tier': str|None,
        'speaker_activity_events': list|None, 'speaker_channel': str|None,
        'display_name': str|None}, ...]``——每段一位发言人。服务端一次抽取
        后按段分派，响应体按请求顺序逐段报 ok/failed，调用方只 pop 成功段
        的 bucket。display_name 是该段 subject 的显示名（昵称），只用于
        persona 标题，可缺省。"""
        payload_segments: list[dict[str, Any]] = []
        for segment in segments:
            wire: dict[str, Any] = {
                "input_history": json.dumps(
                    segment.get("messages") or [], ensure_ascii=False,
                ),
                "subject": segment.get("subject"),
                "speaker_label": segment.get("speaker_label"),
            }
            tier = segment.get("speaker_tier")
            if tier is not None:
                wire["speaker_tier"] = tier
            activity_events = segment.get("speaker_activity_events")
            if activity_events:
                wire["speaker_activity_events"] = activity_events
            channel = segment.get("speaker_channel")
            if channel:
                wire["speaker_channel"] = channel
            speaker_id = segment.get("speaker_id")
            if speaker_id:
                wire["speaker_id"] = speaker_id
            if segment.get("speaker_is_owner"):
                wire["speaker_is_owner"] = True
            excluded_identities = segment.get(
                "trust_signal_excluded_fact_identities"
            )
            if excluded_identities:
                wire["trust_signal_excluded_fact_identities"] = [
                    list(identity) for identity in excluded_identities
                ]
            display_name = segment.get("display_name")
            if display_name:
                wire["display_name"] = display_name
            payload_segments.append(wire)
        client = self._client()
        response = await client.post(
            f"{self._base_url()}/internal/memory/{her_name}/scoped_history",
            json={
                "segments": payload_segments,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
