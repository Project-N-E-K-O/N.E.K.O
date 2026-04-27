from utils.config_manager import get_config_manager
from utils.token_tracker import set_call_type
from utils.llm_client import SystemMessage, HumanMessage, AIMessage, messages_to_dict, messages_from_dict, create_chat_llm
import re
import json
import os
import asyncio
import logging
from openai import APIConnectionError, InternalServerError, RateLimitError

from config.prompts_memory import (
    get_recent_history_manager_prompt, get_detailed_recent_history_manager_prompt,
    get_further_summarize_prompt, get_history_review_prompt,
)
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.language_utils import get_global_language
from utils.tokenize import acount_tokens
from config import (
    RECENT_HISTORY_MAX_ITEMS,
    RECENT_COMPRESS_THRESHOLD_ITEMS,
    RECENT_SUMMARY_MAX_TOKENS,
    RECENT_PER_MESSAGE_MAX_TOKENS,
)

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
    """归一化一条消息为 (type, content_prefix) 元组用于 fingerprint 比对。

    支持消息对象（HumanMessage/AIMessage/...）和 dict（持久化 fingerprint）。
    content 是 list 时（multimodal）拼成 string。content 截断到前
    REVIEW_FINGERPRINT_CONTENT_PREFIX 字符——只要重启用户没改写已有消息内容，
    这个前缀稳定。
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
    """从 snapshot 末尾取 K 条做 fingerprint，序列化成可 JSON 持久化的 dict 列表。"""
    if not snapshot:
        return []
    tail = snapshot[-k:] if len(snapshot) >= k else list(snapshot)
    out = []
    for m in tail:
        t, c = _msg_fingerprint(m)
        out.append({'type': t, 'content': c})
    return out


def _find_fingerprint_position(current: list, fingerprint: list[dict]) -> int | None:
    """在 current 里找最后一段连续 K 条消息匹配 fingerprint 的位置。

    返回 fingerprint 末位（也就是 cutoff）在 current 里的 index；
    找不到返回 None。从尾往前搜，多个候选时取最靠后的（最近）。
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
    """给定 review 启动时的 snapshot 和当前 history，算出 (capacity, cutoff_idx)。

    1. 用 snapshot 末尾 K 条做 anchor 在 current 里定位 cutoff_idx。
    2. 从 cutoff_idx 起逆向走，对比 snapshot[-1], snapshot[-2], ... 与
       current[cutoff_idx], current[cutoff_idx-1], ... 的连续匹配长度
       即 capacity（当中间出现压缩 SystemMessage 等"alien"条目时停下）。

    返回 ``(0, None)`` 表示白 review。
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
        """统一获取默认路径，避免重复代码。"""
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, lanlan_name), 'recent.json')

    def _ensure_path_for_character(self, lanlan_name: str) -> str:
        """确保角色有有效的文件路径，返回路径。"""
        if lanlan_name not in self.log_file_path:
            self.log_file_path[lanlan_name] = self._get_default_path(lanlan_name)
            logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径")
        return self.log_file_path[lanlan_name]

    def _reset_history_file(self, file_path, lanlan_name, reason):
        """当 recent 文件损坏或为空时，重置为合法的空 JSON 数组。"""
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
            await asyncio.to_thread(os.makedirs, os.path.dirname(file_path), exist_ok=True)
            await atomic_write_json_async(file_path, [], indent=2, ensure_ascii=False)
            logger.warning(f"[RecentHistory] {lanlan_name} 的历史记录文件无效（{reason}），已重置为空列表: {file_path}")
        except Exception as reset_error:
            logger.error(f"[RecentHistory] 重置 {lanlan_name} 的历史记录文件失败: {reset_error}", exc_info=True)

    def _load_history_from_file(self, file_path, lanlan_name):
        """安全读取 recent 文件，遇到空文件或非法 JSON 时自动重置。"""
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
        """动态获取LLM实例以支持配置热重载。

        timeout=30 配合业务层 max_retries=3 + 指数 backoff（最坏 ~127s）
        覆盖 /process 上游 30s timeout 后的"放弃" 上限。
        max_retries=0 禁掉 OpenAI SDK 默认 2 次自动重试，避免与业务层 retry 叠加翻 3 倍。
        """
        api_config = self._config_manager.get_model_api_config('summary')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=30, max_retries=0,
        )

    def _get_review_llm(self):
        """动态获取审核LLM实例以支持配置热重载。

        timeout=120 给 review_history 重写历史足够余量。review 是纯后台任务
        （Phase C 重设计后不持锁、不阻塞用户路径、并发跑也无所谓），完全可以
        开 thinking——重写历史的判断密度高，思考受益明显。

        Phase D：extra_body=None 显式覆盖 create_chat_llm 自动解析，让 thinking
        模型按其默认行为响应（thinking 模式开启）。
        max_retries=0 同上：禁 SDK 自动重试，由业务层 retry 兜底。
        """
        api_config = self._config_manager.get_model_api_config('correction')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
            timeout=120, max_retries=0,
            extra_body=None,
        )

    async def update_history(self, new_messages, lanlan_name, detailed=False, compress=True):
        try:
            _, _, _, _, _, _, _, _, recent_log = await self._config_manager.aget_character_data()
            self.log_file_path = recent_log
        except Exception as e:
            logger.error(f"获取角色配置失败: {e}")

        assert_cloudsave_writable(
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
                compressed = [(await self.compress_history(to_compress, lanlan_name, detailed))[0]]
                self.user_histories[lanlan_name] = compressed + self.user_histories[lanlan_name][-self.max_history_length+1:]
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


    # detailed: 保留尽可能多的细节
    async def compress_history(self, messages, lanlan_name, detailed=False):
        from utils.tokenize import truncate_head_tail_tokens
        # 单条 message 文本超过 RECENT_PER_MESSAGE_MAX_TOKENS 时做头尾保留
        # 截断（head=tail=半数 token）。用户长贴 / AI 偶尔写小作文都会触发；
        # 头尾各保留确保问候/问题与结尾的总结/请求都不丢，中段砍掉。
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
        messages_text = "\n".join(lines)
        if not detailed:
            prompt = get_recent_history_manager_prompt(get_global_language()).replace("%s", messages_text)
        else:
            prompt = get_detailed_recent_history_manager_prompt(get_global_language()) % messages_text

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                try:
                    response_content = (await llm.ainvoke(prompt)).content
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从JSON字典中提取对话摘要，假设摘要存储在名为'key'的键下
                if '对话摘要' in summary_json:
                    raw_summary = summary_json['对话摘要']
                    # Qwen 偶尔返回 list/dict 而不是字符串；强制 str-ify 后再喂
                    # acount_tokens（不然会抛 TypeError 把整轮压缩流程崩掉）。
                    summary = (
                        raw_summary if isinstance(raw_summary, str)
                        else json.dumps(raw_summary, ensure_ascii=False)
                    )
                    print(f"💗摘要结果：{summary}")
                    if await acount_tokens(summary) > MAX_SUMMARY_TOKENS:
                        summary = await self.further_compress(summary)
                        if summary is None:
                            continue
                        if not isinstance(summary, str):
                            summary = json.dumps(summary, ensure_ascii=False)
                    from config.prompts_sys import _loc, MEMORY_MEMO_WITH_SUMMARY
                    memo_text = _loc(MEMORY_MEMO_WITH_SUMMARY, get_global_language()).format(summary=summary)
                    # 第二个返回值（用于上层缓存）跟 memo_text 用的 summary 保持
                    # 一致——之前用 raw 摘要会出现"用户看到的 memo 用了 stage-2
                    # 摘要、缓存却存了 stage-1 原文"的诡异不一致。
                    return SystemMessage(content=memo_text), summary
                else:
                    print('💥 摘要failed: ', response_content)
                    retries += 1
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
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
        # 如果所有重试都失败，返回None
        from config.prompts_sys import _loc, MEMORY_MEMO_EMPTY
        return SystemMessage(content=_loc(MEMORY_MEMO_EMPTY, get_global_language())), ""

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
                        get_further_summarize_prompt(get_global_language()) % initial_summary,
                        max_completion_tokens=stage2_cap,
                    )).content
                finally:
                    await llm.aclose()
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = robust_json_loads(response_content)
                # 从JSON字典中提取对话摘要，假设摘要存储在名为'key'的键下
                if '对话摘要' in summary_json:
                    summary_text = str(summary_json['对话摘要']).strip()
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
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
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
        审阅历史记录，寻找并修正矛盾、冗余、逻辑混乱或复读的部分。

        Phase C 重设计（snapshot + capacity-based 替换）：
        - ``snapshot``：spawn 时拍下的 history 副本（list of message objects）。
          LLM 输入用 snapshot 不用当前 history——这样 review LLM 期间用户路径
          可以继续追加消息 / 触发压缩，互不干扰。
        - 完成时基于 snapshot 末尾 K=3 条做 fingerprint 匹配，定位 cutoff 在
          当前 history 里的位置；逆向走出 capacity（连续匹配长度）；用 corrected
          末尾 ``min(capacity, len(corrected))`` 条替换当前 history 里 cutoff 前
          连续 ``capacity`` 个 slot；cutoff 之后的新增消息保持不动。
        - cutoff 找不到（被压缩吞了 / 被 /new_dialog 清了）→ 整段丢弃 = 白 review
          → caller 应将 fingerprint 设为 None，下一轮 review 立刻可起。

        Returns:
            (str, list[dict] | None) tuple:
              ('patched', new_fingerprint) — 成功 patch 并落盘；new_fingerprint
                  是 patch 后 new_history 末尾 review 区的 K 条 fingerprint，供
                  caller 写入 maint_state（**必须**用这个新 fingerprint，而不是
                  ``build_review_fingerprint(snapshot)``——review 可能改写过末
                  尾 K 条里的任一条，旧 fingerprint 在新 history 里再也定位不到）
              ('white', None) — cutoff 在当前 history 里失配，整段丢弃
              ('failed', None) — LLM 失败 / 被取消 / 历史为空 / 响应格式错误
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
                prompt = get_history_review_prompt(get_global_language()) % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                review_llm = self._get_review_llm()
                try:
                    response_content = (await review_llm.ainvoke(prompt)).content
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

                if not (isinstance(review_result, dict) and '修正说明' in review_result and '修正后的对话' in review_result):
                    print(f"❌ 审阅响应格式错误：{response_content}")
                    return ('failed', None)

                print(f"记忆整理结果：{review_result['修正说明']}")

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
                for msg_data in review_result['修正后的对话']:
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
                        # 跳过：summary 由 compress 拥有，review 不能改写
                        continue
                    elif role in ['user', 'human', name_mapping['human']]:
                        corrected_messages.append(HumanMessage(content=content))
                    elif role in ['ai', 'assistant', name_mapping['ai']]:
                        corrected_messages.append(AIMessage(content=content))
                    else:
                        # 默认作为用户消息处理
                        corrected_messages.append(HumanMessage(content=content))

                # ── Phase C 关键：基于 snapshot 算 capacity 做尾部对齐替换 ──
                current = await self.aget_recent_history(lanlan_name)
                capacity, cutoff_idx = _compute_review_capacity(snapshot, current)

                if cutoff_idx is None:
                    # 白 review：cutoff 在当前 history 里失配（被压缩 / 被清空）
                    print(f"⚠️ {lanlan_name} review 完成但 cutoff 失配（白 review，丢弃）")
                    return ('white', None)

                take_count = min(capacity, len(corrected_messages))
                if take_count == 0:
                    # corrected 为空（罕见：LLM 返回空"修正后的对话"），等价于
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
                assert_cloudsave_writable(
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

            except (APIConnectionError, InternalServerError, RateLimitError) as e:
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
