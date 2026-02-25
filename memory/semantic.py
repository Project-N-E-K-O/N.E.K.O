# from langchain_chroma import Chroma
# ↑ 这个库引入了Chroma和onnx依赖，显著增大了一键包体积，暂时注释掉
from datetime import datetime
from memory.recent import CompressedRecentHistoryManager
from config import SEMANTIC_MODEL, RERANKER_MODEL, get_extra_body
from utils.config_manager import get_config_manager
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from config.prompts_sys import semantic_manager_prompt
import json
import asyncio
from openai import APIConnectionError, InternalServerError, RateLimitError

class SemanticMemory:
    def __init__(self, recent_history_manager: CompressedRecentHistoryManager, persist_directory=None):
        self._config_manager = get_config_manager()
        # 通过get_character_data获取相关变量
        _, _, _, _, name_mapping, _, semantic_store, _, _, _ = self._config_manager.get_character_data()
        self.original_memory = {}
        self.compressed_memory = {}
        if persist_directory is None:
            persist_directory = semantic_store
        for i in persist_directory:
            self.original_memory[i] = SemanticMemoryOriginal(persist_directory, i, name_mapping)
            self.compressed_memory[i] = SemanticMemoryCompressed(persist_directory, i, recent_history_manager, name_mapping)
    
    def _get_reranker(self):
        """动态获取Reranker LLM实例以支持配置热重载"""
        api_config = self._config_manager.get_model_api_config('summary')
        return ChatOpenAI(model=RERANKER_MODEL, base_url=api_config['base_url'], api_key=api_config['api_key'], temperature=0.1, extra_body=get_extra_body(RERANKER_MODEL) or None)

    async def store_conversation(self, event_id, messages, lanlan_name):
        self.original_memory[lanlan_name].store_conversation(event_id, messages)
        await self.compressed_memory[lanlan_name].store_compressed_summary(event_id, messages)

    async def hybrid_search(self, query, lanlan_name, with_rerank=True, k=10):
        # 从原始和压缩记忆中获取结果
        original_results = self.original_memory[lanlan_name].retrieve_by_query(query, k)
        compressed_results = self.compressed_memory[lanlan_name].retrieve_by_query(query, k)
        combined = original_results + compressed_results

        if with_rerank:
            return await self.rerank_results(query, combined)
        else:
            return combined

    async def query(self, query, lanlan_name):
        results_text = "\n".join([
            f"记忆片段{i} | \n{doc.page_content}\n"
            for i, doc in enumerate(await self.hybrid_search(query, lanlan_name))
        ])
        return f"""======{lanlan_name}尝试回忆=====\n{query}\n\n====={lanlan_name}的相关记忆=====\n{results_text}"""

    async def rerank_results(self, query, results: list, k=5) -> list:
        # 使用LLM重新排序结果
        results_text = "\n\n".join([
            f"记忆片段 {i + 1}:\n{doc.page_content}"
            for i, doc in enumerate(results)
        ])

        prompt = semantic_manager_prompt % (query, results_text, k)
        retries = 0
        max_retries = 3
        while retries < max_retries:
            try:
                reranker = self._get_reranker()
                response = await reranker.ainvoke(prompt)
            except (APIConnectionError, InternalServerError, RateLimitError) as e:
                print(f"ℹ️ 捕获到 {type(e).__name__} 错误")
                retries += 1
                if retries >= max_retries:
                    print(f'❌ Rerank query失败，已达到最大重试次数: {e}')
                    return []
                # 指数退避: 1, 2, 4 秒
                wait_time = 2 ** (retries - 1)
                print(f'⚠️ 遇到网络或429错误，等待 {wait_time} 秒后重试 (第 {retries}/{max_retries} 次)')
                await asyncio.sleep(wait_time)
                continue
            except Exception as e:
                retries += 1
                print(f'❌ Rerank query失败: {e}')
                if retries >= max_retries:
                    return []
                continue

            try:
                # 解析排序后的文档编号
                reranked_indices = json.loads(response.content)
                # 按新顺序排序结果
                reranked_results = [results[idx] for idx in reranked_indices[:k] if 0 <= idx < len(results)]
                return reranked_results
            except Exception as e:
                retries += 1
                print(f'❌ Rerank结果解析失败: {e}')
                if retries >= max_retries:
                    return []
        return []


class SemanticMemoryOriginal:
    def __init__(self, persist_directory, lanlan_name, name_mapping):
        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config('summary')
        self.embeddings = OpenAIEmbeddings(base_url=api_config['base_url'], model=SEMANTIC_MODEL, api_key=api_config['api_key'])
        # self.vectorstore = Chroma(
        #     collection_name="Origin",
        #     persist_directory=persist_directory[lanlan_name],
        #     embedding_function=self.embeddings
        # )
        self.vectorstore = None
        self.lanlan_name = lanlan_name
        self.name_mapping = name_mapping

    def store_conversation(self, event_id, messages):
        # 将对话转换为文本
        texts = []
        metadatas = []
        name_mapping = self.name_mapping.copy()
        name_mapping['ai'] = self.lanlan_name

        for message in messages:
            try:
                parts = []
                for i in message.content:
                    if isinstance(i, dict):
                        parts.append(i.get("text", f"|{i.get('type','')}|"))
                    else:
                        parts.append(str(i))
                joined = "\n".join(parts)
            except Exception:
                joined = str(message.content)
            texts.append(f"{name_mapping[message.type]} | {joined}\n")
            metadatas.append({
                "event_id": event_id,
                "role": message.type,
                "year": str(datetime.now().year),
                "month": "%02d" % (datetime.now().month),
                "day": "%02d" % (datetime.now().day),
                "weekday": "%02d" % (datetime.now().weekday()),
                "hour": "%02d" % (datetime.now().hour),
                "minute": "%02d" % (datetime.now().minute),
                "timestamp": datetime.now().isoformat()
            })

        # 存储到向量数据库
        self.vectorstore.add_texts(texts=texts, metadatas=metadatas)

    def retrieve_by_query(self, query, k=10):
        # 在原始对话上进行精确语义搜索
        return self.vectorstore.similarity_search(query, k=k)


class SemanticMemoryCompressed:
    def __init__(self, persist_directory, lanlan_name, recent_history_manager: CompressedRecentHistoryManager, name_mapping):
        self.lanlan_name = lanlan_name
        self.name_mapping = name_mapping
        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config('summary')
        self.embeddings = OpenAIEmbeddings(base_url=api_config['base_url'], model=SEMANTIC_MODEL, api_key=api_config['api_key'])
        self.vectorstore = None
        # self.vectorstore = Chroma(
        #     collection_name="Compressed",
        #     persist_directory=persist_directory[lanlan_name],
        #     embedding_function=self.embeddings
        # )
        self.recent_history_manager = recent_history_manager

    async def store_compressed_summary(self, event_id, messages):
        # 存储压缩摘要的嵌入
        _, summary = await self.recent_history_manager.compress_history(messages, self.lanlan_name)
        if not summary:
            return
        self.vectorstore.add_texts(
            texts=[summary],
            metadatas=[{
                "event_id": event_id,
                "role": "SYSTEM_SUMMARY",
                "year": str(datetime.now().year),
                "month": "%02d" % (datetime.now().month),
                "day": "%02d" % (datetime.now().day),
                "weekday": "%02d" % (datetime.now().weekday()),
                "hour": "%02d" % (datetime.now().hour),
                "minute": "%02d" % (datetime.now().minute),
                "timestamp": datetime.now().isoformat()
            }]
        )

    def retrieve_by_query(self, query, k=10):
        # 在压缩摘要上进行语义搜索
        return self.vectorstore.similarity_search(query, k=k)