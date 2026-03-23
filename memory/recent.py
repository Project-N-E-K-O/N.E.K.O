from config import get_extra_body
from utils.config_manager import get_config_manager
from utils.token_tracker import set_call_type
from utils.llm_client import ChatOpenAI, SystemMessage, HumanMessage, AIMessage, messages_to_dict, messages_from_dict, get_dashscope_cache_config
import json
import os
import re
import asyncio
import logging
from openai import APIConnectionError, InternalServerError, RateLimitError

from config.prompts_sys import recent_history_manager_prompt, detailed_recent_history_manager_prompt, further_summarize_prompt, history_review_prompt

# Setup logger
from utils.file_utils import atomic_write_json
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

class CompressedRecentHistoryManager:
    def __init__(self, max_history_length=10):
        self._config_manager = get_config_manager()
        # 通过get_character_data获取相关变量
        _, _, _, _, name_mapping, _, _, _, recent_log = self._config_manager.get_character_data()
        self.max_history_length = max_history_length
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
        self._config_manager.ensure_memory_directory()
        memory_base = str(self._config_manager.memory_dir)
        return os.path.join(memory_base, f'recent_{lanlan_name}.json')

    def _ensure_path_for_character(self, lanlan_name: str) -> str:
        """确保角色有有效的文件路径，返回路径。"""
        if lanlan_name not in self.log_file_path:
            self.log_file_path[lanlan_name] = self._get_default_path(lanlan_name)
            logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径")
        return self.log_file_path[lanlan_name]

    def _reset_history_file(self, file_path, lanlan_name, reason):
        """当 recent 文件损坏或为空时，重置为合法的空 JSON 数组。"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            atomic_write_json(file_path, [], indent=2, ensure_ascii=False)
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
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('summary')
        cache_config = get_dashscope_cache_config(api_config['base_url'])
        return ChatOpenAI(
            model=api_config['model'],
            base_url=api_config['base_url'],
            api_key=api_config['api_key'] if api_config['api_key'] else None,
            temperature=0.3,
            extra_body=get_extra_body(api_config['model']) or None,
            default_headers=cache_config['default_headers'],
            enable_cache_control=cache_config['enable_cache_control']
        )
    
    def _get_review_llm(self):
        """动态获取审核LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('correction')
        cache_config = get_dashscope_cache_config(api_config['base_url'])
        return ChatOpenAI(
            model=api_config['model'],
            base_url=api_config['base_url'],
            api_key=api_config['api_key'] if api_config['api_key'] else None,
            temperature=0.1,
            extra_body=get_extra_body(api_config['model']) or None,
            default_headers=cache_config['default_headers'],
            enable_cache_control=cache_config['enable_cache_control']
        )

    async def update_history(self, new_messages, lanlan_name, detailed=False, compress=True):
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

        try:
            self.user_histories[lanlan_name].extend(new_messages)
            logger.debug(f"[RecentHistory] {lanlan_name} 添加了 {len(new_messages)} 条新消息，当前共 {len(self.user_histories[lanlan_name])} 条")

            # 确保文件目录存在
            file_path = self.log_file_path[lanlan_name]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            atomic_write_json(
                file_path,
                messages_to_dict(self.user_histories[lanlan_name]),
                indent=2,
                ensure_ascii=False,
            )  # Save the updated history to file before compressing

            if compress and len(self.user_histories[lanlan_name]) > self.max_history_length:
                to_compress = self.user_histories[lanlan_name][:-self.max_history_length+1]
                compressed = [(await self.compress_history(to_compress, lanlan_name, detailed))[0]]
                self.user_histories[lanlan_name] = compressed + self.user_histories[lanlan_name][-self.max_history_length+1:]
        except Exception as e:
            logger.error(f"[RecentHistory] 更新历史记录时出错: {e}", exc_info=True)
            # 即使出错，也尝试保存当前状态
            try:
                file_path = self.log_file_path[lanlan_name]
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                atomic_write_json(
                    file_path,
                    messages_to_dict(self.user_histories.get(lanlan_name, [])),
                    indent=2,
                    ensure_ascii=False,
                )
            except Exception as save_error:
                logger.error(f"[RecentHistory] 保存历史记录失败: {save_error}", exc_info=True)
            return

        # 最终保存
        try:
            file_path = self.log_file_path[lanlan_name]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            atomic_write_json(
                file_path,
                messages_to_dict(self.user_histories[lanlan_name]),
                indent=2,
                ensure_ascii=False,
            )
            logger.debug(f"[RecentHistory] {lanlan_name} 历史记录已保存到文件: {file_path}")
        except Exception as e:
            logger.error(f"[RecentHistory] 最终保存历史记录失败: {e}", exc_info=True)


    # detailed: 保留尽可能多的细节
    async def compress_history(self, messages, lanlan_name, detailed=False):
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = lanlan_name
        lines = []
        for msg in messages:
            role = name_mapping.get(getattr(msg, 'type', ''), getattr(msg, 'type', ''))
            content = getattr(msg, 'content', '')
            if isinstance(content, str):
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
                line = f"{role} | {joined}"
            lines.append(line)
        messages_text = "\n".join(lines)
        if not detailed:
            prompt = recent_history_manager_prompt.replace("%s", messages_text)
        else:
            prompt = detailed_recent_history_manager_prompt % messages_text

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                response_content = (await llm.ainvoke(prompt)).content
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = json.loads(response_content)
                # 从JSON字典中提取对话摘要，假设摘要存储在名为'key'的键下
                if '对话摘要' in summary_json:
                    print(f"💗摘要结果：{summary_json['对话摘要']}")
                    summary = summary_json['对话摘要']
                    if len(summary) > 500:
                        summary = await self.further_compress(summary)
                        if summary is None:
                            continue
                    # Listen. Here, summary_json['对话摘要'] is not supposed to be anything else than str, but Qwen is shit.
                    return SystemMessage(content=f"先前对话的备忘录: {summary}"), str(summary_json['对话摘要'])
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
        return SystemMessage(content="先前对话的备忘录: 无。"), ""

    async def further_compress(self, initial_summary):
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                set_call_type("memory_compression")
                llm = self._get_llm()
                response_content = (await llm.ainvoke(further_summarize_prompt % initial_summary)).content
                response_content = str(response_content).strip()
                match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response_content)
                if match:
                    response_content = match.group(1).strip()
                summary_json = json.loads(response_content)
                # 从JSON字典中提取对话摘要，假设摘要存储在名为'key'的键下
                if '对话摘要' in summary_json:
                    print(f"💗第二轮摘要结果：{summary_json['对话摘要']}")
                    return summary_json['对话摘要']
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
            config_path = str(config_manager.get_config_path('core_config.json'))
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                    if 'recent_memory_auto_review' in config_data and not config_data['recent_memory_auto_review']:
                        print(f"{lanlan_name} 的自动记忆整理已禁用，跳过审阅")
                        return False
        except Exception as e:
            print(f"读取配置文件失败：{e}，继续执行审阅")
        
        # 获取当前历史记录
        
        current_history = self.get_recent_history(lanlan_name)
        
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
                prompt = history_review_prompt % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                review_llm = self._get_review_llm()
                response_content = (await review_llm.ainvoke(prompt)).content
                
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
                review_result = json.loads(response_content)
                
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
                    atomic_write_json(
                        self.log_file_path[lanlan_name],
                        messages_to_dict(corrected_messages),
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
