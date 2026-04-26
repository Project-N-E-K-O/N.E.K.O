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
        """动态获取LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('summary')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
        )

    def _get_review_llm(self):
        """动态获取审核LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('correction')
        return create_chat_llm(
            api_config['model'], api_config['base_url'],
            api_config['api_key'] or None,
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

    async def review_history(self, lanlan_name, cancel_event=None):
        """
        审阅历史记录，寻找并修正矛盾、冗余、逻辑混乱或复读的部分
        :param lanlan_name: 角色名称
        :param cancel_event: asyncio.Event对象，用于取消操作
        """
        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（启动前）")
            return False
            
        # 检查配置文件中是否禁用自动审阅
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            config_path = str(config_manager.get_runtime_config_path('core_config.json'))
            if await asyncio.to_thread(os.path.exists, config_path):
                config_data = await read_json_async(config_path)
                if 'recent_memory_auto_review' in config_data and not config_data['recent_memory_auto_review']:
                    print(f"{lanlan_name} 的自动记忆整理已禁用，跳过审阅")
                    return False
        except Exception as e:
            print(f"读取配置文件失败：{e}，继续执行审阅")

        # 获取当前历史记录

        current_history = await self.aget_recent_history(lanlan_name)
        
        if not current_history:
            print(f"{lanlan_name} 的历史记录为空，无需审阅")
            return False
        
        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"{lanlan_name} 的记忆整理被取消（获取历史后）")
            return False
        
        # 将消息转换为可读的文本格式
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name
        
        history_text = ""
        for msg in current_history:
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
            return False
        
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
                    return False
                
                # 确保response_content是字符串
                response_content = str(response_content).strip()

                # 清理响应内容（使用正则安全提取）
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()

                # 解析JSON响应
                review_result = robust_json_loads(response_content)
                
                if '修正说明' in review_result and '修正后的对话' in review_result:
                    print(f"记忆整理结果：{review_result['修正说明']}")
                    
                    # 将修正后的对话转换回消息格式
                    corrected_messages = []
                    for msg_data in review_result['修正后的对话']:
                        role = msg_data.get('role', 'user')
                        content = msg_data.get('content', '')
                        
                        if role in ['user', 'human', name_mapping['human']]:
                            corrected_messages.append(HumanMessage(content=content))
                        elif role in ['ai', 'assistant', name_mapping['ai']]:
                            corrected_messages.append(AIMessage(content=content))
                        elif role in ['system', 'system_message', name_mapping['system']]:
                            corrected_messages.append(SystemMessage(content=content))
                        else:
                            # 默认作为用户消息处理
                            corrected_messages.append(HumanMessage(content=content))
                    
                    # 更新历史记录
                    self.user_histories[lanlan_name] = corrected_messages

                    # 保存到文件
                    assert_cloudsave_writable(
                        self._config_manager,
                        operation="save",
                        target=f"memory/{lanlan_name}/recent.json",
                    )
                    await atomic_write_json_async(
                        self.log_file_path[lanlan_name],
                        await asyncio.to_thread(messages_to_dict, corrected_messages),
                        indent=2,
                        ensure_ascii=False,
                    )
                    
                    print(f"✅ {lanlan_name} 的记忆已修正并保存")
                    return True
                else:
                    print(f"❌ 审阅响应格式错误：{response_content}")
                    return False
                    
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                logger.info(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 记忆整理失败，已达到最大重试次数: {e}')
                    return False
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
                # 检查是否被取消
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理在重试等待期间被取消")
                    return False
            except Exception as e:
                logger.error(f"❌ 历史记录审阅失败：{e}")
                return False
        
        # 如果所有重试都失败
        print(f"❌ {lanlan_name} 的记忆整理失败，已达到最大重试次数")
        return False
