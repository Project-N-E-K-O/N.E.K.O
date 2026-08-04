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
        # tier / entity 是内部枚举（scoped 条目的 entity 恒等于 subject.kind），
        # 裸拼会让 `[fact/group_chat]` 出现在中文 prompt 里。与本体侧
        # main_logic/core/tool_calling.py 的召回渲染同一张标签表。
        #
        # 预算：这段此前只有"取前 5 条"，单条零上限——一条被合并出来的超长
        # reflection 就能把召回段撑到几千 token。单条按 token 截断（不丢弃：
        # 召回按相关度排，命中的那条留半段也比整条消失有用），整段按
        # take_lines_within_token_budget 收口，与本体侧同一个 helper。
        from config import (
            RECALL_RENDER_ENTRY_MAX_TOKENS,
            RECALL_RENDER_LINE_OVERHEAD_TOKENS,
            RECALL_RENDER_TOTAL_MAX_TOKENS,
        )
        from config.prompts.prompts_memory import render_recall_entry_tag
        from utils.language_utils import get_global_language_full
        from utils.tokenize import take_lines_within_token_budget, truncate_to_tokens

        lang = get_global_language_full()
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            text = truncate_to_tokens(text, RECALL_RENDER_ENTRY_MAX_TOKENS)
            tag = render_recall_entry_tag(
                item.get("tier"), item.get("entity"), lang,
            )
            anchor = str(
                item.get("event_end_at")
                or item.get("event_start_at")
                or item.get("created_at")
                or ""
            ).strip()
            suffix = f" ({anchor[:10]})" if anchor else ""
            # 整行再兜一次底。截断只管 text，而 tag 里的 tier / entity 是
            # 未知枚举原样透出的（见 render_recall_entry_tag），手改过的
            # facts.json 能塞进任意长的 entity——而整段预算的"至少留一条"
            # 规则会无条件留下第一行。行上限用「单条 + 行装饰」的口径，
            # 正常条目够不着，只有畸形数据会被它切。
            lines.append(truncate_to_tokens(
                f"{index}. {tag} {text}{suffix}",
                RECALL_RENDER_ENTRY_MAX_TOKENS + RECALL_RENDER_LINE_OVERHEAD_TOKENS,
            ))
        kept, dropped = take_lines_within_token_budget(
            lines, RECALL_RENDER_TOTAL_MAX_TOKENS,
        )
        if kept_count_out is not None:
            # out-param 而非改返回签名（与 reply_context_node 的
            # used_member_subject_out 同模式）：既有直调方不受影响。
            kept_count_out.append(len(kept))
        logger = getattr(self.plugin, "logger", None)
        if dropped and logger is not None:
            # 诊断行不该成为渲染的硬依赖：这个函数此前对 plugin 对象零依赖，
            # 抛 AttributeError 会被上游 _build_recalled_memory_text 的
            # except 吞掉，整段召回为了一条日志凭空消失。
            logger.info(
                f"QQ 长期记忆召回段超出 {RECALL_RENDER_TOTAL_MAX_TOKENS} tok 预算，"
                f"丢弃末尾 {dropped} 条"
            )
        return "\n".join(kept)

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
        speaker_trust: float | None = None,
        speaker_id: str | None = None,
        speaker_is_owner: bool = False,
        display_name: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        # speaker_label 只在单发言人批次（成员 bucket / 私聊 participant
        # digest）传：提取 prompt 用它替代私聊主人名渲染 user 轮，避免对方
        # 发言被抽成"关于主人"的事实。群 digest 不传——内容里每条消息已带
        # 发言人头。speaker_trust 与 label 同源同段，作为 fact 的代码侧
        # 仲裁 provenance；精确值不进入 prompt。display_name 是 subject 的人类可读
        # 名（群名/昵称），服务端中和后刷进 persona section 元数据，渲染
        # 标题用；纯装饰性，缺省即退化裸 id。
        payload: dict[str, Any] = {
            "input_history": json.dumps(messages, ensure_ascii=False),
            "subject": subject,
        }
        if speaker_label:
            payload["speaker_label"] = speaker_label
        if speaker_trust is not None:
            payload["speaker_trust"] = speaker_trust
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

    async def post_scoped_memory_history_batch(
        self,
        her_name: str,
        segments: list[dict[str, Any]],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """The batched multi-speaker shape of /scoped_history.

        ``segments``: ``[{'messages': [...], 'subject': {...},
        'speaker_label': str, 'speaker_trust': float|None,
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
            trust = segment.get("speaker_trust")
            if trust is not None:
                wire["speaker_trust"] = trust
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
