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

from utils.config_manager import get_config_manager
from utils.token_tracker import set_call_type
from utils.llm_client import SystemMessage, HumanMessage, AIMessage, messages_to_dict, messages_from_dict, create_chat_llm, openai_retry_error_types
import re
import json
import os
import asyncio
import logging

from config.prompts.prompts_memory import (
    get_recent_history_manager_prompt, get_detailed_recent_history_manager_prompt,
    get_further_summarize_prompt, get_history_review_prompt,
    get_summary_stale_hint,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import get_global_language
from utils.tokenize import acount_tokens
from config import (
    MEMORY_LLM_HARD_TIMEOUT_SECONDS,
    RECENT_HISTORY_MAX_ITEMS,
    RECENT_COMPRESS_THRESHOLD_ITEMS,
    RECENT_SUMMARY_MAX_TOKENS,
    RECENT_PER_MESSAGE_MAX_TOKENS,
    RECENT_SUMMARY_STALE_HOURS,
    RECENT_COMPRESS_INPUT_BUDGET_TOKENS,
    RECENT_HARD_CAP_TOKENS,
)
from datetime import datetime

# Backward-compat alias (Stage-1 → Stage-2 trigger threshold).
# Two-stage flow: Stage 1 (`compress_history`) summarises raw messages with no
# explicit length cap; Stage 2 (`further_compress`) is invoked only when Stage-1
# output exceeds this threshold. Stage-2's own prompt hard-caps output at
# 500 chars/words per language.
MAX_SUMMARY_TOKENS = RECENT_SUMMARY_MAX_TOKENS

# ── Phase C review snapshot/capacity 算法 ─────────────────────────────
# Fingerprint = 末尾 K 条消息的 (type, content[:50]) 元组列表。K=3 兼顾
# 抗碰撞（连续 3 条 mixed user+ai 几乎不会误命中）和定位精度。
REVIEW_FINGERPRINT_K = 3
REVIEW_FINGERPRINT_CONTENT_PREFIX = 50


def _msg_fingerprint(m) -> tuple[str, str]:
    """Normalize a message into a (type, content_prefix) tuple for fingerprint comparison.

    Supports message objects (HumanMessage/AIMessage/...) and dicts (persisted
    fingerprints). When content is a list (multimodal), it is joined into a
    string. Content is truncated to the first
    REVIEW_FINGERPRINT_CONTENT_PREFIX characters — stable across restarts as
    long as the user doesn't rewrite existing message content.
    """
    if isinstance(m, dict):
        t = m.get('type', '') or ''
        c = m.get('content', '') if 'content' in m else (m.get('data', {}).get('content', '') if isinstance(m.get('data'), dict) else '')
    else:
        t = getattr(m, 'type', '') or ''
        c = getattr(m, 'content', '') or ''
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict):
                parts.append(p.get('text', '') or str(p))
            else:
                parts.append(str(p))
        c = ' '.join(parts)
    elif not isinstance(c, str):
        c = str(c)
    return (str(t), c[:REVIEW_FINGERPRINT_CONTENT_PREFIX])


def build_review_fingerprint(snapshot, k: int = REVIEW_FINGERPRINT_K) -> list[dict]:
    """Take the last K messages of the snapshot as the fingerprint, serialized into JSON-persistable dicts."""
    if not snapshot:
        return []
    tail = snapshot[-k:] if len(snapshot) >= k else list(snapshot)
    out = []
    for m in tail:
        t, c = _msg_fingerprint(m)
        out.append({'type': t, 'content': c})
    return out


def _find_fingerprint_position(current: list, fingerprint: list[dict]) -> int | None:
    """Find, in current, the last run of K consecutive messages matching the fingerprint.

    Returns the index in current of the fingerprint's last element (i.e. the
    cutoff); None when not found. Searches from the tail backwards, taking the
    rearmost (most recent) of multiple candidates.
    """
    if not current or not fingerprint:
        return None
    k = len(fingerprint)
    if len(current) < k:
        return None
    fp_norm = [(fp['type'], fp['content']) for fp in fingerprint]
    for i in range(len(current) - k, -1, -1):
        if all(_msg_fingerprint(current[i + j]) == fp_norm[j] for j in range(k)):
            return i + k - 1
    return None


def _compute_review_capacity(snapshot: list, current: list) -> tuple[int, int | None]:
    """Given the snapshot taken at review start and the current history, compute (capacity, cutoff_idx).

    1. Use the snapshot's last K entries as an anchor to locate cutoff_idx in current.
    2. Walk backwards from cutoff_idx, comparing snapshot[-1], snapshot[-2], ...
       against current[cutoff_idx], current[cutoff_idx-1], ...; the consecutive
       match length is the capacity (stopping when an "alien" entry such as a
       compression SystemMessage appears in between).

    Returns ``(0, None)`` for a wasted (no-op) review.
    """
    if not snapshot or not current:
        return (0, None)
    anchor = build_review_fingerprint(snapshot, REVIEW_FINGERPRINT_K)
    cutoff_idx = _find_fingerprint_position(current, anchor)
    if cutoff_idx is None:
        return (0, None)
    # 从 cutoff 起逆向走（包含 cutoff 自身），算 capacity
    capacity = 0
    s_idx = len(snapshot) - 1
    c_idx = cutoff_idx
    while s_idx >= 0 and c_idx >= 0 and _msg_fingerprint(current[c_idx]) == _msg_fingerprint(snapshot[s_idx]):
        capacity += 1
        s_idx -= 1
        c_idx -= 1
    return (capacity, cutoff_idx)


# Setup logger
from utils.file_utils import (
    atomic_write_json,
    atomic_write_json_async,
    read_json_async,
    robust_json_loads,
)
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

class CompressedRecentHistoryManager:
    def __init__(
        self,
        max_history_length: int = RECENT_HISTORY_MAX_ITEMS,
        compress_threshold: int = RECENT_COMPRESS_THRESHOLD_ITEMS,
    ):
        self._config_manager = get_config_manager()
        # 通过get_character_data获取相关变量
        _, _, _, _, name_mapping, _, _, _, recent_log = self._config_manager.get_character_data()
        self.max_history_length = max_history_length      # 压缩后保留条数
        self.compress_threshold = compress_threshold      # >此值才触发压缩
        self.log_file_path = recent_log
        self.name_mapping = name_mapping
        self.user_histories = {}
        for ln in self.log_file_path:
            if os.path.exists(self.log_file_path[ln]):
                self.user_histories[ln] = self._load_history_from_file(self.log_file_path[ln], ln)
            else:
                self.user_histories[ln] = []

    def _get_default_path(self, lanlan_name: str) -> str:
        """Single place for the default path, avoiding duplicated code."""
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, lanlan_name), 'recent.json')

    def _ensure_path_for_character(self, lanlan_name: str) -> str:
        """Ensure the character has a valid file path; returns the path."""
        if lanlan_name not in self.log_file_path:
            self.log_file_path[lanlan_name] = self._get_default_path(lanlan_name)
            logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径")
        return self.log_file_path[lanlan_name]

    def _reset_history_file(self, file_path, lanlan_name, reason):
        """Reset the recent file to a valid empty JSON array when it is corrupted or empty."""
        try:
            assert_cloudsave_writable(
                self._config_manager,
                operation="reset",
                target=f"memory/{lanlan_name}/recent.json",
            )
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            atomic_write_json(file_path, [], indent=2, ensure_ascii=False)
            logger.warning(f"[RecentHistory] {lanlan_name} 的历史记录文件无效（{reason}），已重置为空列表: {file_path}")
        except MaintenanceModeError:
            raise
        except Exception as reset_error:
            logger.error(f"[RecentHistory] 重置 {lanlan_name} 的历史记录文件失败: {reset_error}", exc_info=True)

    async def _areset_history_file(self, file_path, lanlan_name, reason):
        try:
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager,
                operation="reset",
                target=f"memory/{lanlan_name}/recent.json",
            )
            await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
            await atomic_write_json_async(file_path, [], indent=2, ensure_ascii=False)
            logger.warning(f"[RecentHistory] {lanlan_name} 的历史记录文件无效（{reason}），已重置为空列表: {file_path}")
        except MaintenanceModeError:
            raise
        except Exception as reset_error:
            logger.error(f"[RecentHistory] 重置 {lanlan_name} 的历史记录文件失败: {reset_error}", exc_info=True)

    def _load_history_from_file(self, file_path, lanlan_name):
        """Safely read the recent file, auto-resetting on an empty file or invalid JSON."""
        try:
            with open(file_path, encoding='utf-8') as f:
                raw_content = f.read()

            if not raw_content.strip():
                self._reset_history_file(file_path, lanlan_name, "文件为空")
                return []

            file_content = json.loads(raw_content)
            if not isinstance(file_content, list):
                self._reset_history_file(file_path, lanlan_name, "JSON 根节点不是列表")
                return []

            return messages_from_dict(file_content)
        except json.JSONDecodeError as e:
            self._reset_history_file(file_path, lanlan_name, f"JSON 解析失败: {e}")
            return []
        except Exception as e:
            logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，使用空列表")
            return []

    async def _aload_history_from_file(self, file_path, lanlan_name):
        try:
            raw_content = await asyncio.to_thread(self._read_text, file_path)
            if not raw_content.strip():
                await self._areset_history_file(file_path, lanlan_name, "文件为空")
                return []
            file_content = await asyncio.to_thread(json.loads, raw_content)
            if not isinstance(file_content, list):
                await self._areset_history_file(file_path, lanlan_name, "JSON 根节点不是列表")
                return []
            return await asyncio.to_thread(messages_from_dict, file_content)
        except json.JSONDecodeError as e:
            await self._areset_history_file(file_path, lanlan_name, f"JSON 解析失败: {e}")
            return []
        except Exception as e:
            logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，使用空列表")
            return []

    @staticmethod
    def _read_text(file_path: str) -> str:
        with open(file_path, encoding='utf-8') as f:
            return f.read()
    
    def _get_llm(self):
        """Fetch the LLM instance dynamically to support config hot-reload.

        timeout=30 pairs with business-level max_retries=3 + exponential backoff
        (worst case ~127s), covering the "give up" ceiling after the /process
        upstream's 30s timeout.
        max_retries=0 disables the OpenAI SDK's default 2 automatic retries,
        avoiding a 3× blow-up stacked on the business-level retry.
        """
        api_config = self._config_manager.get_model_api_config('summary')
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=30, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length summary/JSON isn't truncated
            provider_type=api_config.get('provider_type'),
        )

    def _get_review_llm(self):
        """Fetch the review LLM instance dynamically to support config hot-reload.

        timeout uses MEMORY_LLM_HARD_TIMEOUT_SECONDS (the upstream forwards with
        a 120s hard cap; must be ≤110). Review is a pure background task (after
        the Phase C redesign it holds no locks, never blocks the user path, and
        concurrent runs are harmless), so thinking can be enabled freely — the
        judgment-dense work of rewriting history clearly benefits from it.

        Phase D: extra_body=None explicitly overrides create_chat_llm's
        automatic resolution, letting thinking models respond with their
        default behavior (thinking mode on).
        max_retries=0 as above: SDK auto-retry off; the business-layer retry is
        the safety net.
        """
        api_config = self._config_manager.get_model_api_config('correction')
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=MEMORY_LLM_HARD_TIMEOUT_SECONDS, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
            extra_body=None,
            provider_type=api_config.get('provider_type'),
        )

    async def update_history(self, new_messages, lanlan_name, detailed=False, compress=True, on_compress_done=None):
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        await asyncio.to_thread(
            assert_cloudsave_writable,
            self._config_manager,
            operation="save",
            target=f"memory/{lanlan_name}/recent.json",
        )

        self._ensure_path_for_character(lanlan_name)

        if lanlan_name not in self.user_histories:
            self.user_histories[lanlan_name] = []

        file_path = self.log_file_path[lanlan_name]
        if await asyncio.to_thread(os.path.exists, file_path):
            self.user_histories[lanlan_name] = await self._aload_history_from_file(
                file_path, lanlan_name,
            )

        try:
            self.user_histories[lanlan_name].extend(new_messages)
            logger.debug(f"[RecentHistory] {lanlan_name} 添加了 {len(new_messages)} 条新消息，当前共 {len(self.user_histories[lanlan_name])} 条")

            # 先把 extend 后的未压缩状态落盘，再进入耗时的 compress_history。
            # compress_history 会走 LLM，耗时数秒到数十秒，期间进程崩溃或 task 被 cancel
            # （CancelledError 穿透下面的 except Exception）会导致本批 new_messages 丢失。
            await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
            await atomic_write_json_async(
                file_path,
                await asyncio.to_thread(messages_to_dict, self.user_histories[lanlan_name]),
                indent=2,
                ensure_ascii=False,
            )

            if compress and len(self.user_histories[lanlan_name]) > self.compress_threshold:
                to_compress = self.user_histories[lanlan_name][:-self.max_history_length+1]
                snapshot = list(to_compress)
                compressed_result = await self.compress_history(to_compress, lanlan_name, detailed)
                if compressed_result is None:
                    logger.warning(
                        f"[RecentHistory] {lanlan_name} 摘要失败，跳过本轮压缩以保留原始历史"
                    )
                    # best-effort：通知上层起一个受保护的后台压缩任务尽力压（主路径失败）。
                    # 硬上限裁剪**不在这里**做——否则"历史超 cap 后任何一次暂时性失败"
                    # 都会立刻丢最旧原文，而后台压缩用的是裁剪前 snapshot → 合并失配
                    # moot → 那批对话没被摘要就永久丢了。改由后台 best-effort 也压不成
                    # 后再裁剪（memory_server._run_backup_compress / dead-letter 分支），
                    # 让暂时性失败有机会被后台压成摘要保留。
                    await self._notify_compress_done(on_compress_done, lanlan_name, snapshot, False, detailed)
                else:
                    compressed = [compressed_result[0]]
                    self.user_histories[lanlan_name] = compressed + self.user_histories[lanlan_name][-self.max_history_length+1:]
                    # 主路径成功 → 通知上层 cancel 在跑的后台压缩（兜底不再需要）。
                    await self._notify_compress_done(on_compress_done, lanlan_name, snapshot, True, detailed)
        except Exception as e:
            logger.error(f"[RecentHistory] 更新历史记录时出错: {e}", exc_info=True)

        try:
            await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
            await atomic_write_json_async(
                file_path,
                await asyncio.to_thread(messages_to_dict, self.user_histories.get(lanlan_name, [])),
                indent=2,
                ensure_ascii=False,
            )
            logger.debug(f"[RecentHistory] {lanlan_name} 历史记录已保存到文件: {file_path}")
        except Exception as e:
            logger.error(f"[RecentHistory] 保存历史记录失败: {e}", exc_info=True)


    # ── Past block 更新 meta（防止"几天前的事还在 summary 里被反复带出来"
    #    ——见 config.RECENT_SUMMARY_STALE_HOURS 注释）。
    # 锚点不是"上次 summary 时间"——summary 每轮压缩都会跑，跟着锚点会让
    # stale hint 永远跟在最后一次压缩后 1 小时，无法形成"每隔 N 小时
    # 刷一次 past block"的稳定节奏。这里改记"上次 hint 真正注入的时刻"，
    # 即"上次 LLM 实际更新 past block 的时刻"——只有那种 turn 才推进锚点。
    def _summary_meta_path(self, lanlan_name: str) -> str:
        """Side meta file per character, co-located with recent.json
        ({"last_past_block_update_at": ISO}).

        Prefers the directory of ``self.log_file_path[lanlan_name]`` — the
        actual path source of every recent.json read/write in this class (item
        9 of the character_data tuple). If the user's config moved a
        character's recent.json outside memory_dir, the meta file still sits in
        the same directory instead of wandering off (CodeRabbit review catch on
        PR #1316). Before update_history has run, log_file_path may be empty —
        in that case fall back to the memory_dir-based derivation.
        """
        recent_path = (self.log_file_path or {}).get(lanlan_name)
        if recent_path:
            return os.path.join(os.path.dirname(recent_path), 'recent_meta.json')
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, lanlan_name),
            'recent_meta.json',
        )

    async def _aread_last_past_block_update_at(self, lanlan_name: str) -> datetime | None:
        path = self._summary_meta_path(lanlan_name)
        if not await asyncio.to_thread(os.path.exists, path):
            return None
        try:
            def _read():
                with open(path, encoding='utf-8') as f:
                    return f.read()
            raw = await asyncio.to_thread(_read)
            data = robust_json_loads(raw)
            if not isinstance(data, dict):
                return None
            # 兼容 PR #1316 早期 in-progress 版本的旧 key（last_summary_at）。
            # 用 OR 兜底——已合并版本不会写 last_summary_at，本兼容仅服务
            # 在我本机跑过该 PR 中间 commit 的开发者，下次写 meta 会用新 key
            # 覆盖旧文件。
            ts = data.get('last_past_block_update_at') or data.get('last_summary_at')
            if not ts:
                return None
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    async def _awrite_last_past_block_update_at(self, lanlan_name: str) -> None:
        path = self._summary_meta_path(lanlan_name)
        try:
            await asyncio.to_thread(os.makedirs, os.path.dirname(path), exist_ok=True)
            await atomic_write_json_async(
                path,
                {'last_past_block_update_at': datetime.now().isoformat()},
                indent=2,
                ensure_ascii=False,
            )
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name}: 写 recent_meta 失败: {e}")

    def _render_messages_to_text(self, messages, lanlan_name):
        """把消息列表渲染成喂给摘要 LLM 的文本：每条做头尾保留截断 + role 前缀。

        单条 message 文本超过 RECENT_PER_MESSAGE_MAX_TOKENS 时做头尾保留截断
        （head=tail=半数 token）。用户长贴 / AI 偶尔写小作文都会触发；头尾各
        保留确保问候/问题与结尾的总结/请求都不丢，中段砍掉。
        """
        from utils.tokenize import truncate_head_tail_tokens
        per_msg_cap = RECENT_PER_MESSAGE_MAX_TOKENS
        head_tail = per_msg_cap // 2
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
                content = truncate_head_tail_tokens(content, head_tail, head_tail)
                line = f"{role} | {content}"
            else:
                parts = []
                try:
                    for item in content:
                        if isinstance(item, dict):
                            parts.append(item.get('text', f"|{item.get('type', '')}|"))
                        else:
                            parts.append(str(item))
                except Exception:
                    parts = [str(content)]
                joined = "\n".join(parts)
                joined = truncate_head_tail_tokens(joined, head_tail, head_tail)
                line = f"{role} | {joined}"
            lines.append(line)
        return "\n".join(lines)

    def _build_summary_prompt(self, messages_text, detailed):
        """构建 Stage-1 摘要 prompt（不含 stale-hint 前缀；单次压缩与分段 map 共用）。

        ``{MASTER_NAME}`` 是 prompt 里"保留负面反馈"段引用 master 实名的字面
        占位符（与同 prompt 里既有的 ``%s`` 共存）。⚠️ master_name 替换**最后**
        做：它是 user-controlled，含 ``%`` 会让先前的 ``%`` formatting 崩溃；含
        ``%s`` 会被先前的 ``.replace("%s", ...)`` 二次替换（codex P2）。
        """
        lang = get_global_language()
        master_name = self.name_mapping['human']
        if not detailed:
            return (
                get_recent_history_manager_prompt(lang)
                .replace("%s", messages_text)
                .replace("{MASTER_NAME}", master_name)
            )
        return (
            (get_detailed_recent_history_manager_prompt(lang) % messages_text)
            .replace("{MASTER_NAME}", master_name)
        )

    async def _invoke_summary_llm(self, prompt):
        """调摘要 LLM（3 次重试 + 对网络/429 指数退避），成功返回 summary 字符串，
        失败返回 None。不含 further_compress / memo 包装 / stale 锚点——那些由
        compress_history 主体在拿到 summary 后处理。Stage-1 单次压缩与分段 map
        阶段共用本方法。
        """
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(prompt)).content  # noqa: LLM_INPUT_BUDGET  # compression prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从 JSON 字典中提取对话摘要，key 与 prompt 模板里约定的一致
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Qwen 偶尔返回 list/dict 而不是字符串；强制 str-ify 后再用
                    # （不然 acount_tokens 会抛 TypeError 把整轮压缩崩掉）。
                    summary = (
                        raw_summary if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    print(f"💗摘要结果：{summary}")
                    return summary
                else:
                    print('💥 摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 摘要模型失败，已达到最大重试次数: {e}')
                    break
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 摘要模型失败：{e}')
                # 如果解析失败，重试
                retries += 1
        return None

    def _split_messages_by_budget(self, messages, lanlan_name):
        """按渲染后累计 token 把消息切成多段，每段 ≤ RECENT_COMPRESS_INPUT_BUDGET_TOKENS。
        不切碎单条消息（单条已被 per-message 截断到 ≤500 token）。"""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        chunks, cur, cur_tok = [], [], 0
        for msg in messages:
            t = count_tokens(self._render_messages_to_text([msg], lanlan_name))
            if cur and cur_tok + t > budget:
                chunks.append(cur)
                cur, cur_tok = [], 0
            cur.append(msg)
            cur_tok += t
        if cur:
            chunks.append(cur)
        return chunks

    def _split_texts_by_budget(self, texts):
        """按累计 token 把字符串列表分批，每批拼接 ≤ 预算（reduce 阶段用）。"""
        from utils.tokenize import count_tokens
        budget = RECENT_COMPRESS_INPUT_BUDGET_TOKENS
        batches, cur, cur_tok = [], [], 0
        for txt in texts:
            tok = count_tokens(txt)
            if cur and cur_tok + tok > budget:
                batches.append(cur)
                cur, cur_tok = [], 0
            cur.append(txt)
            cur_tok += tok
        if cur:
            batches.append(cur)
        return batches

    async def _segmented_compress(self, messages, lanlan_name, detailed):
        """输入过大时的分段 map-reduce：把 messages 切段逐段总结成中间摘要，反复
        reduce 直到拼接 ≤ 预算，返回该文本交给 compress_history 主体做最终总结。
        任一段 LLM 失败返回 None（上层据此跳过本轮压缩）。"""
        chunks = self._split_messages_by_budget(messages, lanlan_name)
        partials = []
        for chunk in chunks:
            s = await self._invoke_summary_llm(
                self._build_summary_prompt(self._render_messages_to_text(chunk, lanlan_name), detailed)
            )
            if s is None:
                return None
            partials.append(s)
        logger.info(
            f"[RecentHistory] {lanlan_name} 分段压缩：{len(messages)} 条原始消息 → {len(chunks)} 段中间摘要"
        )
        # 中间摘要拼接仍超预算 → 再分批合并总结，限深度防极端。
        depth = 0
        while (
            len(partials) > 1
            and await acount_tokens("\n\n".join(partials)) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS
            and depth < 3
        ):
            batches = self._split_texts_by_budget(partials)
            if len(batches) >= len(partials):
                break  # 无法再缩（单段已超预算），交给主体兜底
            new_partials = []
            for batch in batches:
                s = await self._invoke_summary_llm(
                    self._build_summary_prompt("\n\n".join(batch), detailed)
                )
                if s is None:
                    return None
                new_partials.append(s)
            partials = new_partials
            depth += 1
        merged = "\n\n".join(partials)
        # reduce 缩不动 / 深度耗尽时 merged 仍可能超预算 → 硬截到预算兜底，保证
        # 交给主体最终总结的输入有界（best-effort，丢尾部）。
        if await acount_tokens(merged) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            from utils.tokenize import atruncate_to_tokens
            merged = await atruncate_to_tokens(merged, RECENT_COMPRESS_INPUT_BUDGET_TOKENS)
        return merged

    # detailed: 保留尽可能多的细节
    async def compress_history(self, messages, lanlan_name, detailed=False):
        messages_text = self._render_messages_to_text(messages, lanlan_name)
        # 输入过大（积压一直压不掉时会膨胀）→ 先分段 map-reduce 缩小输入，减小
        # 单次 LLM 输入、避免输入过大导致超时。正常输入不走这条。
        if await acount_tokens(messages_text) > RECENT_COMPRESS_INPUT_BUDGET_TOKENS:
            reduced = await self._segmented_compress(messages, lanlan_name, detailed)
            if reduced is None:
                logger.warning(f"[RecentHistory] {lanlan_name} 分段压缩失败，跳过本轮压缩")
                return None
            messages_text = reduced

        lang = get_global_language()
        prompt = self._build_summary_prompt(messages_text, detailed)

        # Past block 时间衰减：距上次"实际更新 past block"超过
        # RECENT_SUMMARY_STALE_HOURS 小时时，在 prompt 头部加提醒让 LLM 把明显
        # 过时的内容挪到 summary 末尾的"较久前"段落。锚点只在 hint 真正注入时
        # 推进——这样 hint 形成"每 N 小时触发一次"的节奏，而不是每轮压缩都刷。
        # 仅影响本次 summary 文本，不持久化到 reflection / persona。
        stale_hint_injected = False
        first_time_baseline = False
        try:
            last_past_update = await self._aread_last_past_block_update_at(lanlan_name)
            if last_past_update is None:
                # 第一次为该角色 compress——先建立 baseline 锚点，本轮不注入 hint。
                first_time_baseline = True
            else:
                gap_hours = (datetime.now() - last_past_update).total_seconds() / 3600.0
                if gap_hours >= RECENT_SUMMARY_STALE_HOURS:
                    hint = get_summary_stale_hint(lang, gap_hours)
                    prompt = hint + "\n\n" + prompt
                    stale_hint_injected = True
        except Exception as e:
            # 时间衰减提醒是 best-effort；失败不能挡 summary 主流程
            logger.debug(f"[RecentHistory] {lanlan_name}: stale hint 注入失败: {e}")

        # Stage-1 + Stage-2 联合重试：原行为是 further_compress 失败时重试整个
        # stage-1（stage-1 LLM 有随机性，重下一次可能直接生成 ≤MAX 的 summary 而
        # 不必二次压缩）。重构后用有限计数循环复现该重试，同时避免原 `continue`
        # 不计数可能导致的死循环。
        summary = None
        for _ in range(3):
            s = await self._invoke_summary_llm(prompt)
            if s is None:
                # stage-1 连续失败：不生成空备忘录，避免覆盖既有 memo 或丢未压原文。
                logger.warning(f"[RecentHistory] {lanlan_name} 摘要连续失败，跳过本轮压缩")
                return None
            if await acount_tokens(s) <= MAX_SUMMARY_TOKENS:
                summary = s
                break
            reduced = await self.further_compress(s)
            if reduced is not None:
                summary = reduced if isinstance(reduced, str) else json.dumps(reduced, ensure_ascii=False)
                break
            # stage-2 失败 → 重试 stage-1（最多 3 轮）
        if summary is None:
            logger.warning(f"[RecentHistory] {lanlan_name} 二次压缩连续失败，跳过本轮压缩")
            return None

        # 推进 past-block 更新锚点（best-effort）：第一次 compress 建 baseline；
        # 注入过 stale hint 表示 LLM 本轮真的更新了 past block。常规压缩不动锚点，
        # 让 hint 形成稳定的"每 N 小时一次"节奏。
        if first_time_baseline or stale_hint_injected:
            await self._awrite_last_past_block_update_at(lanlan_name)

        # 第二个返回值（用于上层缓存）跟 memo_text 用的 summary 保持一致——之前
        # 用 raw 摘要会出现"用户看到的 memo 用 stage-2、缓存却存 stage-1"的不一致。
        from config.prompts.prompts_sys import _loc, MEMORY_MEMO_WITH_SUMMARY
        memo_text = _loc(MEMORY_MEMO_WITH_SUMMARY, get_global_language()).format(summary=summary)
        return SystemMessage(content=memo_text), summary

    async def _notify_compress_done(self, callback, lanlan_name, snapshot, ok, detailed):
        """Invoke the best-effort compression callback without blocking the main flow.

        memory_server injects the callback: ok=False starts background
        compression, while ok=True cancels any running background task.
        """
        if callback is None:
            return
        try:
            await callback(lanlan_name, snapshot, ok, detailed)
        except Exception as e:
            logger.debug(f"[RecentHistory] {lanlan_name} on_compress_done({ok}) 回调异常: {e}")

    async def enforce_hard_cap(self, lanlan_name):
        """Keep recent-history prompt size bounded as a final fallback.

        When history still exceeds RECENT_HARD_CAP_TOKENS after best-effort
        background compression, discard the oldest uncompressed dialogue text
        while preserving the first memo, when present, and at least the latest
        max_history_length entries.
        """
        history = self.user_histories.get(lanlan_name, [])
        if not history:
            return
        # 不用条数提前断言"不可能超上限"——几条超长原文就能顶破 token 上限。
        # 统一交给下面按真实 token 算的 _trim 决定（_trim 内仍保证至少留近期
        # max_history_length 条，丢不动就不丢）。

        from utils.tokenize import count_tokens

        def _raw_tokens(msgs):
            # 硬上限按**真实注入 prompt** 的 token 算，不能走 _render_messages_to_text
            # （那个为压缩输入把每条截到 ≤RECENT_PER_MESSAGE_MAX_TOKENS，会严重低估
            # 长消息、让硬上限对超长原文失效）。这里数原始 content 的 token。
            total = 0
            for m in msgs:
                c = getattr(m, 'content', '')
                if isinstance(c, list):
                    c = ' '.join(
                        p.get('text', '') if isinstance(p, dict) else str(p) for p in c
                    )
                elif not isinstance(c, str):
                    c = str(c)
                total += count_tokens(c)
            return total

        def _trim():
            if _raw_tokens(history) <= RECENT_HARD_CAP_TOKENS:
                return None  # 未超，不动
            # 首条若是备忘录（已压缩的长期记忆）则保留，只丢正文里最旧的原文。
            head = [history[0]] if isinstance(history[0], SystemMessage) else []
            body = history[len(head):]
            kept = []
            kept_tok = _raw_tokens(head)
            for msg in reversed(body):
                mtok = _raw_tokens([msg])
                if kept and kept_tok + mtok > RECENT_HARD_CAP_TOKENS and len(kept) >= self.max_history_length:
                    break
                kept.append(msg)
                kept_tok += mtok
            kept.reverse()
            return head + kept

        new_history = await asyncio.to_thread(_trim)
        if new_history is not None and len(new_history) < len(history):
            dropped = len(history) - len(new_history)
            logger.warning(
                f"[RecentHistory] {lanlan_name} 历史超硬上限 {RECENT_HARD_CAP_TOKENS} token，"
                f"丢弃最旧 {dropped} 条未压缩原文以保证有界"
            )
            self.user_histories[lanlan_name] = new_history
            # 自包含落盘：本方法现在由后台 task / 回调调用（update_history 之外），
            # 不能再依赖 update_history 的后续落盘。
            try:
                await asyncio.to_thread(
                    assert_cloudsave_writable,
                    self._config_manager, operation="save",
                    target=f"memory/{lanlan_name}/recent.json",
                )
                file_path = self._ensure_path_for_character(lanlan_name)
                await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
                await atomic_write_json_async(
                    file_path,
                    await asyncio.to_thread(messages_to_dict, new_history),
                    indent=2, ensure_ascii=False,
                )
            except MaintenanceModeError:
                raise
            except Exception as e:
                logger.error(f"[RecentHistory] {lanlan_name} 硬上限裁剪落盘失败: {e}", exc_info=True)

    async def merge_backup_memo(self, lanlan_name, snapshot, memo):
        """后台压缩成功后，把 memo 合并回当前 history（快照对齐）。

        用 snapshot 的 fingerprint 在当前 history 里定位那批积压：仍整批连续在
        头部（capacity==len(snapshot) 且批头在 index 0）→ 替换成 [memo] + 这期间
        新增的对话，落盘，返回 'merged'；否则（已被主路径压掉 / 被 /new_dialog
        清空 / 头部已变）→ 丢弃返回 'moot'。

        读 current 到写 new_history 之间全是同步代码（_compute_review_capacity
        同步），asyncio 单线程下原子；落盘在写之后。调用方（memory_server）应在
        _get_settle_lock 内调用，串行化对该角色的写。
        """
        current = self.user_histories.get(lanlan_name, [])
        if not current or not snapshot:
            return 'moot'
        capacity, cutoff_idx = _compute_review_capacity(snapshot, current)
        if cutoff_idx is None or capacity != len(snapshot) or (cutoff_idx - capacity + 1) != 0:
            return 'moot'
        new_history = [memo] + current[cutoff_idx + 1:]
        self.user_histories[lanlan_name] = new_history
        try:
            await asyncio.to_thread(
                assert_cloudsave_writable,
                self._config_manager, operation="save",
                target=f"memory/{lanlan_name}/recent.json",
            )
            file_path = self._ensure_path_for_character(lanlan_name)
            await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
            await atomic_write_json_async(
                file_path,
                await asyncio.to_thread(messages_to_dict, new_history),
                indent=2, ensure_ascii=False,
            )
        except MaintenanceModeError:
            raise
        except Exception as e:
            # 落盘失败 → 内存与磁盘不一致（下次 update_history reload 会回滚内存），
            # 报 'failed' 让上层 bump 退避、不清计数，而不是谎报 'merged'。
            logger.error(f"[RecentHistory] {lanlan_name} 后台压缩合并落盘失败: {e}", exc_info=True)
            return 'failed'
        logger.info(
            f"[RecentHistory] {lanlan_name} 后台压缩合并完成：history {len(current)}→{len(new_history)}"
        )
        return 'merged'

    async def further_compress(self, initial_summary):
        # Stage-2 LLM 输出硬限：RECENT_SUMMARY_MAX_TOKENS + 100 余量 = 1100 token。
        # prompt 要求 700 字/words：CJK 700 字 ≈ 1050 token (×1.5)、
        # EN 700 words ≈ 933 token，都安全落在 1100 cap 之下。
        # 仍然防 LLM 写小作文；如果真撞到 cap，下面句末标点回溯保证语义边界。
        from utils.tokenize import truncate_to_last_sentence_end
        stage2_cap = RECENT_SUMMARY_MAX_TOKENS + 100
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(
                        # codex P2：先 % 再 .replace，否则 master_name 含 % 会崩
                        (get_further_summarize_prompt(get_global_language()) % initial_summary)
                        .replace("{MASTER_NAME}", self.name_mapping['human']),
                        max_completion_tokens=stage2_cap,
                    )).content
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从 JSON 字典中提取对话摘要，key 与 prompt 模板里约定的一致
                if 'summary' in summary_json:
                    raw_summary = summary_json['summary']
                    # Stage-2 归一化和 Stage-1 ([memory/recent.py:382](memory/recent.py:382))
                    # 保持一致：非字符串走 json.dumps(ensure_ascii=False) 而非
                    # str()，避免 list/dict 落到 Python repr (单引号) 漂移持久化
                    # 文本与 token 计量。
                    summary_text = (
                        raw_summary.strip() if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    # 命中 stage2_cap → LLM 输出可能停在句子中段（如逗号 / 短语）。
                    # 回溯到最后一个句末标点（. ! ? 。！？… \n），保证持久化的
                    # 摘要语义边界完整。如果根本没找到句末标点（极端短文本），
                    # truncate_to_last_sentence_end 返回 ""，此时退到原文以避免
                    # 完全丢摘要。
                    sane = truncate_to_last_sentence_end(summary_text)
                    if not sane:
                        sane = summary_text
                    print(f"💗第二轮摘要结果：{sane}")
                    return sane
                else:
                    print('💥 第二轮摘要failed: ', response_content)
                    retries += 1
            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 第二轮摘要模型失败，已达到最大重试次数: {e}')
                    return None
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 第二轮摘要模型失败：{e}')
                retries += 1
        return None

    def get_recent_history(self, lanlan_name):
        try:
            _, _, _, _, _, _, _, _, recent_log = self._config_manager.get_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        self._ensure_path_for_character(lanlan_name)

        # 确保角色在 user_histories 中
        if lanlan_name not in self.user_histories:
            self.user_histories[lanlan_name] = []

        # 如果文件存在，加载历史记录
        if lanlan_name in self.log_file_path and os.path.exists(self.log_file_path[lanlan_name]):
            self.user_histories[lanlan_name] = self._load_history_from_file(
                self.log_file_path[lanlan_name],
                lanlan_name
            )

        return self.user_histories.get(lanlan_name, [])

    async def aget_recent_history(self, lanlan_name):
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        self._ensure_path_for_character(lanlan_name)

        if lanlan_name not in self.user_histories:
            self.user_histories[lanlan_name] = []

        file_path = self.log_file_path[lanlan_name]
        if await asyncio.to_thread(os.path.exists, file_path):
            self.user_histories[lanlan_name] = await self._aload_history_from_file(
                file_path, lanlan_name,
            )

        return self.user_histories.get(lanlan_name, [])

    async def review_history(self, lanlan_name, snapshot=None, cancel_event=None):
        """
        Review the history, finding and fixing contradictions, redundancy,
        logical confusion, or repetition.

        Phase C redesign (snapshot + capacity-based replacement):
        - ``snapshot``: a copy of the history taken at spawn time (list of
          message objects). The LLM input uses the snapshot, not the current
          history — so while the review LLM runs, the user path can keep
          appending messages / triggering compression without interference.
        - On completion, fingerprint-match the snapshot's last K=3 entries to
          locate the cutoff position in the current history; walk back to get
          the capacity (consecutive match length); replace the consecutive
          ``capacity`` slots before the cutoff in the current history with the
          last ``min(capacity, len(corrected))`` entries of corrected; messages
          added after the cutoff stay untouched.
        - Cutoff not found (swallowed by compression / cleared by /new_dialog)
          → drop the whole batch = wasted review → the caller should set the
          fingerprint to None so the next review can start immediately.

        Returns:
            (str, list[dict] | None) tuple:
              ('patched', new_fingerprint) — patched and persisted;
                  new_fingerprint is the K-entry fingerprint of the review
                  region at the tail of the patched new_history, for the caller
                  to write into maint_state (it **must** use this new
                  fingerprint rather than ``build_review_fingerprint(snapshot)``
                  — the review may have rewritten any of the last K entries,
                  and the old fingerprint would never locate again in the new
                  history)
              ('white', None) — cutoff failed to match in the current history; batch dropped
              ('failed', None) — LLM failure / cancelled / empty history / malformed response
        """
        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（启动前）")
            return ('failed', None)

        # snapshot 由 caller 提供（spawn 时拍下）；为兼容老调用兜底从磁盘读
        if snapshot is None:
            snapshot = await self.aget_recent_history(lanlan_name)

        if not snapshot:
            print(f"{lanlan_name} 的历史记录为空，无需审阅")
            return ('failed', None)

        # 将 snapshot 转为可读文本格式（喂 LLM）
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name

        history_text = ""
        for msg in snapshot:
            if hasattr(msg, 'type') and msg.type in name_mapping:
                role = name_mapping[msg.type]
            else:
                role = "unknown"

            if hasattr(msg, 'content'):
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    content = "\n".join([str(i) if isinstance(i, str) else i.get("text", str(i)) for i in msg.content])
                else:
                    content = str(msg.content)
            else:
                content = str(msg)

            history_text += f"{role}: {content}\n\n"

        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（准备调用LLM前）")
            return ('failed', None)

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 使用LLM审阅历史记录
                set_call_type("memory_review")
                prompt = (
                    # codex P2：先 % formatting 再 .replace，否则 master_name 含 %
                    # 会让 5-arg `% (...)` 把它当格式符崩溃
                    (
                        get_history_review_prompt(get_global_language())
                        % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                    )
                    .replace("{MASTER_NAME}", self.name_mapping['human'])
                )
                review_llm = self._get_review_llm()
                try:
                    response_content = (await review_llm.ainvoke(prompt)).content  # noqa: LLM_INPUT_BUDGET  # review prompt built from RECENT_PER_MESSAGE_MAX_TOKENS-capped history.
                finally:
                    await review_llm.aclose()

                # 检查是否被取消（LLM调用后）
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理被取消（LLM调用后，保存前）")
                    return ('failed', None)

                # 确保response_content是字符串
                response_content = str(response_content).strip()

                # 清理响应内容（使用正则安全提取）
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()

                # 解析JSON响应
                review_result = robust_json_loads(response_content)

                if not (
                    isinstance(review_result, dict)
                    and 'explanation' in review_result
                    and isinstance(review_result.get('corrected_dialogue'), list)
                ):
                    print(f"❌ 审阅响应格式错误：{response_content}")
                    return ('failed', None)

                print(f"记忆整理结果：{review_result['explanation']}")

                # 将修正后的对话转换回消息格式。SystemMessage 类型由 compress
                # 产生（summary 备忘录），review 不应该输出，丢弃以保护压缩边界。
                #
                # content 归一化（trust-boundary 防御）：thinking 模型偶尔会把
                # JSON content 字段输出为 list/dict 而非 string。现有
                # compress_history（[memory/recent.py:329-340](memory/recent.py:329)）
                # 已经针对这种情况做过处理；review 的输出同样是模型生成、同样
                # 不可信，必须归一化后再写回 recent history，否则下游（recall /
                # prompt build / fingerprint 比对的 content[:50] 截取）会拿到非
                # 字符串数据炸掉。
                corrected_messages = []
                for msg_data in review_result['corrected_dialogue']:
                    if not isinstance(msg_data, dict):
                        continue
                    role = msg_data.get('role', 'user')
                    content = msg_data.get('content', '')

                    # 归一化 content 到 str
                    if not isinstance(content, str):
                        if isinstance(content, list):
                            parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    parts.append(item.get('text', '') or str(item))
                                else:
                                    parts.append(str(item))
                            content = '\n'.join(parts)
                        else:
                            content = str(content)

                    if role in ['system', 'system_message', name_mapping['system']]:
                        # prompt <要点3> 让 LLM 保留+可编辑 memo，过滤掉等于
                        # 让其在 prompt 里白做工，且 capacity 走过 head SystemMessage
                        # 后这一格无人填补，导致 memo 在写盘时蒸发（场景 D）。
                        # 但只在 snapshot 头本来就是 SystemMessage 时接收 LLM
                        # 的 system 输出——否则 history 还没压缩过、不该有
                        # SystemMessage，LLM 幻觉吐 system 必须丢，避免把伪
                        # memo 注入未压缩对话区污染下游。
                        if snapshot and isinstance(snapshot[0], SystemMessage):
                            corrected_messages.append(SystemMessage(content=content))
                        # else: 静默 drop，恢复老行为
                    elif role in ['user', 'human', name_mapping['human']]:
                        corrected_messages.append(HumanMessage(content=content))
                    elif role in ['ai', 'assistant', name_mapping['ai']]:
                        corrected_messages.append(AIMessage(content=content))
                    else:
                        # 默认作为用户消息处理
                        corrected_messages.append(HumanMessage(content=content))

                # 规范化 SystemMessage 位置：snapshot 头是 memo 时，
                # corrected_messages 必须以唯一一条 SystemMessage 开头。
                # 处理三种 LLM 坏输出：
                # (a) 完全漏返 → 用 snapshot[0] 兜底
                # (b) 放在中间 → 提到头部
                # (c) 多吐几条 → 只留首条
                # 不规范的话头部 memo 边界会被破，下游 prompt 拼装会拿到错位的
                # system 行（甚至中段 SystemMessage 跟下游 compress 的"alien stop"
                # 不变量打架）。
                # 注意：必须 gate 在 corrected_messages 非空——LLM 返空列表是
                # "整段都删"的语义信号，下面 take_count == 0 那条会按白 review
                # 处理；这里塞 snapshot[0] 进去会绕过白 review 闸门、把对话区
                # 全擦掉只剩 memo。
                if (
                    corrected_messages
                    and snapshot
                    and isinstance(snapshot[0], SystemMessage)
                ):
                    sys_msgs = [m for m in corrected_messages if isinstance(m, SystemMessage)]
                    others = [m for m in corrected_messages if not isinstance(m, SystemMessage)]
                    if not others:
                        # LLM 只返 system、没返任何对话 ≡ "整段对话都删"语义信号，
                        # 跟返空列表等价，应走白 review。重置成空让下面 take_count==0
                        # 闸门接管；不然 normalize 会塞一条 SystemMessage 进 corrected，
                        # 长度变 1 绕过闸门，对话区被擦光只剩 memo。
                        corrected_messages = []
                    else:
                        head = sys_msgs[0] if sys_msgs else snapshot[0]
                        corrected_messages = [head] + others

                # ── Phase C 关键：基于 snapshot 算 capacity 做尾部对齐替换 ──
                current = await self.aget_recent_history(lanlan_name)
                capacity, cutoff_idx = _compute_review_capacity(snapshot, current)

                if cutoff_idx is None:
                    # 白 review：cutoff 在当前 history 里失配（被压缩 / 被清空）
                    print(f"⚠️ {lanlan_name} review 完成但 cutoff 失配（白 review，丢弃）")
                    return ('white', None)

                take_count = min(capacity, len(corrected_messages))
                if take_count == 0:
                    # corrected 为空（罕见：LLM 返回空 corrected_dialogue），等价于
                    # 整段删除 review 范围。视为白 review 让下轮重建锚点；不去
                    # 写盘也不更新 fingerprint（避免 anchor 漂移到非 review 区）。
                    print(f"⚠️ {lanlan_name} review 输出为空，按白 review 处理")
                    return ('white', None)

                # 替换 [cutoff_idx - capacity + 1, cutoff_idx] 这 capacity 个 slot
                # 为 corrected 末尾 take_count 条；cutoff_idx 之后新增的保留。
                # take_count < capacity 时，前 (capacity - take_count) 个 slot
                # 直接消失（review 决定删条，结果就比原来短）。
                new_history = (
                    current[:cutoff_idx - capacity + 1]
                    + corrected_messages[-take_count:]
                    + current[cutoff_idx + 1:]
                )

                # 更新 + 落盘
                self.user_histories[lanlan_name] = new_history
                await asyncio.to_thread(
                    assert_cloudsave_writable,
                    self._config_manager,
                    operation="save",
                    target=f"memory/{lanlan_name}/recent.json",
                )
                await atomic_write_json_async(
                    self.log_file_path[lanlan_name],
                    await asyncio.to_thread(messages_to_dict, new_history),
                    indent=2,
                    ensure_ascii=False,
                )

                # ── Issue #3 修复：基于 patched 后的 new_history 算新 fingerprint ──
                # patched 区在 new_history 里的范围是 [patched_start, patched_end]：
                #   patched_start = cutoff_idx - capacity + 1
                #   patched_end   = patched_start + take_count - 1
                # 新 fingerprint = K 条以 patched_end 结尾的消息。如果 patched_end
                # 之前的消息不足 K-1 条，取从 0 开始所有可用的。
                patched_end = (cutoff_idx - capacity + 1) + take_count - 1
                fp_start = max(0, patched_end - REVIEW_FINGERPRINT_K + 1)
                fp_messages = new_history[fp_start:patched_end + 1]
                new_fingerprint = build_review_fingerprint(fp_messages, k=REVIEW_FINGERPRINT_K)

                print(
                    f"✅ {lanlan_name} 的记忆已修正：cutoff_idx={cutoff_idx}, "
                    f"capacity={capacity}, corrected={len(corrected_messages)}, "
                    f"take={take_count}, history {len(current)}→{len(new_history)}"
                )
                return ('patched', new_fingerprint)

            except openai_retry_error_types() as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 记忆整理失败，已达到最大重试次数: {e}')
                    return ('failed', None)
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
                # 检查是否被取消
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理在重试等待期间被取消")
                    return ('failed', None)
            except Exception as e:
                logger.error(f"❌ 历史记录审阅失败：{e}")
                return ('failed', None)

        # 如果所有重试都失败
        print(f"❌ {lanlan_name} 的记忆整理失败，已达到最大重试次数")
        return ('failed', None)
