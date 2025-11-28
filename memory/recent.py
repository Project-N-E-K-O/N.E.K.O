from datetime import datetime
from config import MODELS_WITH_EXTRA_BODY
from utils.config_manager import get_config_manager
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, messages_to_dict, messages_from_dict, HumanMessage, AIMessage
import json
import os
import asyncio
import logging
from openai import RateLimitError

from config.prompts_sys import recent_history_manager_prompt, detailed_recent_history_manager_prompt, further_summarize_prompt, history_review_prompt

# Setup logger
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="RecentMemory", log_level=logging.INFO)

class CompressedRecentHistoryManager:
    def __init__(self, max_history_length=10):
        self._config_manager = get_config_manager()
        # 通过get_character_data获取相关变量
        _, _, _, _, name_mapping, _, _, _, _, recent_log = self._config_manager.get_character_data()
        self.max_history_length = max_history_length
        self.log_file_path = recent_log
        self.name_mapping = name_mapping
        self.user_histories = {}
        for ln in self.log_file_path:
            if os.path.exists(self.log_file_path[ln]):
                with open(self.log_file_path[ln], encoding='utf-8') as f:
                    self.user_histories[ln] = messages_from_dict(json.load(f))
            else:
                self.user_histories[ln] = []
    
    def _get_llm(self):
        """动态获取LLM实例以支持配置热重载"""
        core_config = self._config_manager.get_core_config()
        api_key = core_config['OPENROUTER_API_KEY'] if core_config['OPENROUTER_API_KEY'] else None
        return ChatOpenAI(model=core_config['SUMMARY_MODEL'], base_url=core_config['OPENROUTER_URL'], api_key=api_key, temperature=0.3, extra_body={"enable_thinking": False} if core_config['SUMMARY_MODEL'] in MODELS_WITH_EXTRA_BODY else None)
    
    def _get_review_llm(self):
        """动态获取审核LLM实例以支持配置热重载"""
        core_config = self._config_manager.get_core_config()
        api_key = core_config['OPENROUTER_API_KEY'] if core_config['OPENROUTER_API_KEY'] else None
        return ChatOpenAI(model=core_config['CORRECTION_MODEL'], base_url=core_config['OPENROUTER_URL'], api_key=api_key, temperature=0.1, extra_body={"enable_thinking": False} if core_config['CORRECTION_MODEL'] in MODELS_WITH_EXTRA_BODY else None)

    async def update_history(self, new_messages, lanlan_name, detailed=False):
        # 检查角色是否存在于配置中，如果不存在则创建默认路径
        try:
            _, _, _, _, _, _, _, _, _, recent_log = self._config_manager.get_character_data()
            # 更新文件路径映射
            self.log_file_path = recent_log
            
            # 如果角色不在配置中，使用默认路径创建
            if lanlan_name not in recent_log:
                # 确保memory目录存在
                self._config_manager.ensure_memory_directory()
                memory_base = str(self._config_manager.memory_dir)
                default_path = os.path.join(memory_base, f'recent_{lanlan_name}.json')
                self.log_file_path[lanlan_name] = default_path
                logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径: {default_path}")
        except Exception as e:
            logger.error(f"检查角色配置失败: {e}")
            # 即使配置检查失败，也尝试使用默认路径
            try:
                # 确保memory目录存在
                self._config_manager.ensure_memory_directory()
                memory_base = str(self._config_manager.memory_dir)
                default_path = os.path.join(memory_base, f'recent_{lanlan_name}.json')
                if lanlan_name not in self.log_file_path:
                    self.log_file_path[lanlan_name] = default_path
                    logger.info(f"[RecentHistory] 使用默认路径: {default_path}")
            except Exception as e2:
                logger.error(f"创建默认路径失败: {e2}")
                return
        
        # 确保角色在 user_histories 中
        if lanlan_name not in self.user_histories:
            self.user_histories[lanlan_name] = []
        
        # 如果文件存在，加载历史记录
        if lanlan_name in self.log_file_path and os.path.exists(self.log_file_path[lanlan_name]):
            try:
                with open(self.log_file_path[lanlan_name], encoding='utf-8') as f:
                    file_content = json.load(f)
                    if file_content:
                        self.user_histories[lanlan_name] = messages_from_dict(file_content)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，使用空列表")
                self.user_histories[lanlan_name] = []

        try:
            self.user_histories[lanlan_name].extend(new_messages)
            logger.info(f"[RecentHistory] {lanlan_name} 添加了 {len(new_messages)} 条新消息，当前共 {len(self.user_histories[lanlan_name])} 条")

            # 确保文件目录存在
            file_path = self.log_file_path[lanlan_name]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, "w", encoding='utf-8') as f:  # Save the updated history to file before compressing
                json.dump(messages_to_dict(self.user_histories[lanlan_name]), f, indent=2, ensure_ascii=False)

            if len(self.user_histories[lanlan_name]) > self.max_history_length:
                # 压缩旧消息
                to_compress = self.user_histories[lanlan_name][:-self.max_history_length+1]
                compressed = [(await self.compress_history(to_compress, lanlan_name, detailed))[0]]

                # 只保留最近的max_history_length条消息
                self.user_histories[lanlan_name] = compressed + self.user_histories[lanlan_name][-self.max_history_length+1:]
        except Exception as e:
            logger.error(f"[RecentHistory] 更新历史记录时出错: {e}", exc_info=True)
            # 即使出错，也尝试保存当前状态
            try:
                file_path = self.log_file_path[lanlan_name]
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w", encoding='utf-8') as f:
                    json.dump(messages_to_dict(self.user_histories.get(lanlan_name, [])), f, indent=2, ensure_ascii=False)
            except Exception as save_error:
                logger.error(f"[RecentHistory] 保存历史记录失败: {save_error}", exc_info=True)
            return

        # 最终保存
        try:
            file_path = self.log_file_path[lanlan_name]
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding='utf-8') as f:
                json.dump(messages_to_dict(self.user_histories[lanlan_name]), f, indent=2, ensure_ascii=False)
            logger.info(f"[RecentHistory] {lanlan_name} 历史记录已保存到文件: {file_path}")
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
            prompt = recent_history_manager_prompt % messages_text
        else:
            prompt = detailed_recent_history_manager_prompt % messages_text

        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                llm = self._get_llm()
                response_content = (await llm.ainvoke(prompt)).content
                # 修复类型问题：确保response_content是字符串
                if isinstance(response_content, list):
                    response_content = str(response_content)
                if response_content.startswith("```"):
                    response_content = response_content.replace('```json','').replace('```', '')
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
            except RateLimitError as e:
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 摘要模型失败，已达到最大重试次数: {e}')
                    break
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 摘要模型失败：{e}')
                # 如果解析失败，重试
                retries += 1
        # 如果所有重试都失败，返回None
        return SystemMessage(content=f"先前对话的备忘录: 无。"), ""

    async def further_compress(self, initial_summary):
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                # 尝试将响应内容解析为JSON
                llm = self._get_llm()
                response_content = (await llm.ainvoke(further_summarize_prompt % initial_summary)).content
                # 修复类型问题：确保response_content是字符串
                if isinstance(response_content, list):
                    response_content = str(response_content)
                if response_content.startswith("```"):
                    response_content = response_content.replace('```json', '').replace('```', '')
                summary_json = json.loads(response_content)
                # 从JSON字典中提取对话摘要，假设摘要存储在名为'key'的键下
                if '对话摘要' in summary_json:
                    print(f"💗第二轮摘要结果：{summary_json['对话摘要']}")
                    return summary_json['对话摘要']
                else:
                    print('💥 第二轮摘要failed: ', response_content)
                    retries += 1
            except RateLimitError as e:
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 第二轮摘要模型失败，已达到最大重试次数: {e}')
                    return None
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
            except Exception as e:
                print(f'❌ 第二轮摘要模型失败：{e}')
                retries += 1
        return None

    def get_recent_history(self, lanlan_name):
        # 检查角色是否存在于配置中，如果不存在则创建默认路径
        try:
            _, _, _, _, _, _, _, _, _, recent_log = self._config_manager.get_character_data()
            # 更新文件路径映射
            self.log_file_path = recent_log
            
            # 如果角色不在配置中，使用默认路径
            if lanlan_name not in recent_log:
                # 确保memory目录存在
                self._config_manager.ensure_memory_directory()
                memory_base = str(self._config_manager.memory_dir)
                default_path = os.path.join(memory_base, f'recent_{lanlan_name}.json')
                self.log_file_path[lanlan_name] = default_path
                logger.info(f"[RecentHistory] 角色 '{lanlan_name}' 不在配置中，使用默认路径: {default_path}")
        except Exception as e:
            logger.error(f"检查角色配置失败: {e}")
            # 即使配置检查失败，也尝试使用默认路径
            try:
                memory_base = str(self._config_manager.memory_dir)
                default_path = f'{memory_base}/recent_{lanlan_name}.json'
                if lanlan_name not in self.log_file_path:
                    self.log_file_path[lanlan_name] = default_path
            except Exception as e2:
                logger.error(f"创建默认路径失败: {e2}")
                return []
        
        # 确保角色在 user_histories 中
        if lanlan_name not in self.user_histories:
            self.user_histories[lanlan_name] = []
        
        # 如果文件存在，加载历史记录
        if lanlan_name in self.log_file_path and os.path.exists(self.log_file_path[lanlan_name]):
            try:
                with open(self.log_file_path[lanlan_name], encoding='utf-8') as f:
                    file_content = json.load(f)
                    if file_content:
                        self.user_histories[lanlan_name] = messages_from_dict(file_content)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取 {lanlan_name} 的历史记录文件失败: {e}，使用空列表")
                self.user_histories[lanlan_name] = []
        
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
                        print(f"💡 {lanlan_name} 的自动记忆整理已禁用，跳过审阅")
                        return False
        except Exception as e:
            print(f"⚠️ 读取配置文件失败：{e}，继续执行审阅")
        
        # 获取当前历史记录
        
        current_history = self.get_recent_history(lanlan_name)
        
        if not current_history:
            print(f"💡 {lanlan_name} 的历史记录为空，无需审阅")
            return False
        
        # 检查是否被取消
        if cancel_event and cancel_event.is_set():
            print(f"⚠️ {lanlan_name} 的记忆整理被取消（获取历史后）")
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
                prompt = history_review_prompt % (self.name_mapping['human'], name_mapping['ai'], history_text, self.name_mapping['human'], name_mapping['ai'])
                review_llm = self._get_review_llm()
                response_content = (await review_llm.ainvoke(prompt)).content
                
                # 检查是否被取消（LLM调用后）
                if cancel_event and cancel_event.is_set():
                    print(f"⚠️ {lanlan_name} 的记忆整理被取消（LLM调用后，保存前）")
                    return False
                
                # 确保response_content是字符串
                if isinstance(response_content, list):
                    response_content = str(response_content)
                
                # 清理响应内容
                if response_content.startswith("```"):
                    response_content = response_content.replace('```json', '').replace('```', '')
                
                # 解析JSON响应
                review_result = json.loads(response_content)
                
                if '修正说明' in review_result and '修正后的对话' in review_result:
                    print(f"💡 记忆整理结果：{review_result['修正说明']}")
                    
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
                    with open(self.log_file_path[lanlan_name], "w", encoding='utf-8') as f:
                        json.dump(messages_to_dict(corrected_messages), f, indent=2, ensure_ascii=False)
                    
                    print(f"✅ {lanlan_name} 的记忆已修正并保存")
                    return True
                else:
                    print(f"❌ 审阅响应格式错误：{response_content}")
                    return False
                    
            except RateLimitError as e:
                retries += 1
                if retries >= max_retries:
                    print(f'❌ 记忆整理失败，已达到最大重试次数: {e}')
                    return False
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
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

    def clear_history(self, lanlan_name):
        """
        清除用户的聊天历史
        """
        self.user_histories[lanlan_name] = []
