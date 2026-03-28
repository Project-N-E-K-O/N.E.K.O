# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import (
    CompressedRecentHistoryManager, ImportantSettingsManager, TimeIndexedMemory,
    FactStore, PersonaManager, ReflectionEngine,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
import json
import uvicorn
from utils.llm_client import convert_to_messages
from uuid import uuid4
from config import MEMORY_SERVER_PORT
from config.prompts_sys import _loc
from config.prompts_memory import (
    INNER_THOUGHTS_HEADER, INNER_THOUGHTS_BODY,
    CHAT_GAP_NOTICE, CHAT_GAP_LONG_HINT, CHAT_GAP_CURRENT_TIME,
    ELAPSED_TIME_HM, ELAPSED_TIME_H,
    MEMORY_RECALL_HEADER, MEMORY_RESULTS_HEADER,
    PERSONA_HEADER, INNER_THOUGHTS_DYNAMIC,
)
from utils.language_utils import get_global_language
from utils.config_manager import get_config_manager
from pydantic import BaseModel
import re
import asyncio
import logging
import argparse
from datetime import datetime
from utils.frontend_utils import get_timestamp

# 配置日志
from utils.logger_config import setup_logging
logger, log_config = setup_logging(service_name="Memory", log_level=logging.INFO)

class HistoryRequest(BaseModel):
    input_history: str

app = FastAPI()


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """返回带 N.E.K.O 签名的健康响应，供 launcher/前端识别，
    以区分当前服务与随机占用该端口的其他进程。"""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("memory", instance_id=INSTANCE_ID)


def validate_lanlan_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 50:
        raise HTTPException(status_code=400, detail="Invalid lanlan_name length")
    # 支持：字母、数字、下划线、连字符、空白、中日韩文字、括号
    # 与 characters_router.py / memory_router.py 的规则保持一致
    if not re.match(r"^[\w\-\s\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af()（）]+$", name):
        raise HTTPException(status_code=400, detail="Invalid characters in lanlan_name")
    # 禁止路径遍历
    if '/' in name or '\\' in name or '..' in name:
        raise HTTPException(status_code=400, detail="Invalid characters in lanlan_name")
    return name

# 初始化组件
_config_manager = get_config_manager()
recent_history_manager = CompressedRecentHistoryManager()
settings_manager = ImportantSettingsManager()  # 保留兼容，逐步迁移
time_manager = TimeIndexedMemory(recent_history_manager)
fact_store = FactStore(time_indexed_memory=time_manager)
persona_manager = PersonaManager()
reflection_engine = ReflectionEngine(fact_store, persona_manager)

# 用于保护重新加载操作的锁
_reload_lock = asyncio.Lock()

async def reload_memory_components():
    """重新加载记忆组件配置（用于新角色创建后）
    
    使用锁保护重新加载操作，确保原子性交换，避免竞态条件。
    先创建所有新实例，然后原子性地交换引用。
    """
    global recent_history_manager, settings_manager, time_manager, fact_store, persona_manager, reflection_engine
    async with _reload_lock:
        logger.info("[MemoryServer] 开始重新加载记忆组件配置...")
        try:
            # 先创建所有新实例
            new_recent = CompressedRecentHistoryManager()
            new_settings = ImportantSettingsManager()
            new_time = TimeIndexedMemory(new_recent)
            new_facts = FactStore(time_indexed_memory=new_time)
            new_persona = PersonaManager()
            new_reflection = ReflectionEngine(new_facts, new_persona)

            # 然后原子性地交换引用
            recent_history_manager = new_recent
            settings_manager = new_settings
            time_manager = new_time
            fact_store = new_facts
            persona_manager = new_persona
            reflection_engine = new_reflection
            
            logger.info("[MemoryServer] ✅ 记忆组件配置重新加载完成")
            return True
        except Exception as e:
            logger.error(f"[MemoryServer] ❌ 重新加载记忆组件配置失败: {e}", exc_info=True)
            return False

# 全局变量用于控制服务器关闭
shutdown_event = asyncio.Event()
# 全局变量控制是否响应退出请求
enable_shutdown = False
# 全局变量用于管理correction任务
correction_tasks = {}  # {lanlan_name: asyncio.Task}
correction_cancel_flags = {}  # {lanlan_name: asyncio.Event}
# 强引用注册表：防止 fire-and-forget task 被 GC
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _spawn_background_task(coro) -> asyncio.Task:
    """Create a background task with strong reference + exception logging."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task):
        _BACKGROUND_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.warning(f"[MemoryServer] 后台任务异常: {exc}")

    task.add_done_callback(_on_done)
    return task

@app.post("/shutdown")
async def shutdown_memory_server():
    """接收来自main_server的关闭信号"""
    global enable_shutdown
    if not enable_shutdown:
        logger.warning("收到关闭信号，但当前模式不允许响应退出请求")
        return {"status": "shutdown_disabled", "message": "当前模式不允许响应退出请求"}
    
    try:
        logger.info("收到来自main_server的关闭信号")
        shutdown_event.set()
        return {"status": "shutdown_signal_received"}
    except Exception as e:
        logger.error(f"处理关闭信号时出错: {e}")
        return {"status": "error", "message": str(e)}

REBUTTAL_CHECK_INTERVAL = 300  # 5 分钟
_last_rebuttal_check: dict[str, datetime] = {}  # per-character 上次检查时间戳


def _extract_user_messages_from_rows(rows: list) -> list[str]:
    """从 time_indexed SQL 查询结果中提取用户消息文本。

    rows: [(session_id, message_json), ...]
    message_json 是 langchain SQLChatMessageHistory 存储的 JSON 字符串。
    """
    user_msgs = []
    for _, msg_json in rows:
        try:
            msg = json.loads(msg_json) if isinstance(msg_json, str) else msg_json
            if isinstance(msg, dict) and msg.get('type') == 'human':
                content = msg.get('data', {}).get('content', '')
                if isinstance(content, str) and content.strip():
                    user_msgs.append(content)
        except (json.JSONDecodeError, TypeError):
            continue
    return user_msgs


async def _periodic_rebuttal_loop():
    """每 5 分钟检查 confirmed reflections 是否被近期对话反驳。

    通过 time_indexed SQL 查询上次检查之后的所有新对话消息，
    确保不遗漏任何未消费的用户回复。
    """
    from datetime import timedelta as _td

    while True:
        await asyncio.sleep(REBUTTAL_CHECK_INTERVAL)
        try:
            character_data = _config_manager.load_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            now = datetime.now()
            for name in catgirl_names:
                confirmed = reflection_engine.get_confirmed_reflections(name)
                if not confirmed:
                    continue

                # 确定查询起始时间：上次检查时间 or 1小时前（首次）
                start_time = _last_rebuttal_check.get(
                    name, now - _td(hours=1)
                )
                rows = time_manager.retrieve_original_by_timeframe(
                    name, start_time, now,
                )
                if not rows:
                    _last_rebuttal_check[name] = now
                    continue

                user_msgs = _extract_user_messages_from_rows(rows)
                _last_rebuttal_check[name] = now  # 标记已消费

                if not user_msgs:
                    continue

                # 复用 check_feedback 判断反驳
                feedbacks = await reflection_engine.check_feedback_for_confirmed(
                    name, confirmed, user_msgs,
                )
                for fb in feedbacks:
                    if isinstance(fb, dict) and fb.get('feedback') == 'denied':
                        rid = fb.get('reflection_id')
                        if rid:
                            reflection_engine.reject_promotion(name, rid)
                            logger.info(f"[Rebuttal] {name}: confirmed 反思被反驳: {rid}")
        except Exception as e:
            logger.debug(f"[Rebuttal] 定期反驳检查失败: {e}")


@app.on_event("startup")
async def startup_event_handler():
    """应用启动时初始化"""
    try:
        from utils.token_tracker import TokenTracker, install_hooks
        install_hooks()
        TokenTracker.get_instance().start_periodic_save()
        TokenTracker.get_instance().record_app_start()
    except Exception as e:
        logger.warning(f"[Memory] Token tracker init failed: {e}")

    # 自动迁移 settings → persona（如 persona 文件不存在）
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        for name in catgirl_names:
            persona_manager.ensure_persona(name)
        logger.info(f"[Memory] Persona 迁移检查完成，角色数: {len(catgirl_names)}")
    except Exception as e:
        logger.warning(f"[Memory] Persona 迁移检查失败: {e}")

    # 启动定期反驳检查任务（每 5 分钟）
    _spawn_background_task(_periodic_rebuttal_loop())


@app.on_event("shutdown")
async def shutdown_event_handler():
    """应用关闭时执行清理工作"""
    logger.info("Memory server正在关闭...")
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        pass
    logger.info("Memory server已关闭")


async def _run_review_in_background(lanlan_name: str):
    """在后台运行review_history，支持取消"""
    global correction_tasks, correction_cancel_flags
    
    # 获取该角色的取消标志
    cancel_event = correction_cancel_flags.get(lanlan_name)
    if not cancel_event:
        cancel_event = asyncio.Event()
        correction_cancel_flags[lanlan_name] = cancel_event
    
    try:
        # 直接异步调用review_history方法
        await recent_history_manager.review_history(lanlan_name, cancel_event)
        logger.info(f"✅ {lanlan_name} 的记忆整理任务完成")
    except asyncio.CancelledError:
        logger.info(f"⚠️ {lanlan_name} 的记忆整理任务被取消")
    except Exception as e:
        logger.error(f"❌ {lanlan_name} 的记忆整理任务出错: {e}")
    finally:
        # 清理任务记录
        if lanlan_name in correction_tasks:
            del correction_tasks[lanlan_name]
        # 重置取消标志
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].clear()

def _extract_ai_response(messages: list) -> str:
    """从消息列表中提取最后一条 AI 回复的文本。"""
    for m in reversed(messages):
        if getattr(m, 'type', '') == 'ai':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text']
                return ''.join(parts)
    return ''


def _extract_user_messages(messages: list) -> list[str]:
    """从消息列表中提取用户消息文本。"""
    user_msgs = []
    for m in messages:
        if getattr(m, 'type', '') == 'human':
            content = getattr(m, 'content', '')
            if isinstance(content, str):
                user_msgs.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        user_msgs.append(part.get('text', ''))
    return user_msgs


async def _extract_facts_and_check_feedback(messages: list, lanlan_name: str):
    """后台异步：事实提取 + 反馈检查 + 复读嗅探。失败静默跳过。

    认知框架：Facts → Reflection(pending→confirmed→promoted) → Persona
    """
    try:
        # 1. 事实提取
        await fact_store.extract_facts(messages, lanlan_name)
    except Exception as e:
        logger.warning(f"[MemoryServer] 事实提取失败: {e}")

    try:
        # 2. 全局复读嗅探：扫描 AI 回复中是否重复提及 persona 条目
        ai_response = _extract_ai_response(messages)
        if ai_response:
            persona_manager.record_mentions(lanlan_name, ai_response)
    except Exception as e:
        logger.warning(f"[MemoryServer] 复读嗅探失败: {e}")

    try:
        # 3. 检查用户对之前 surfaced 反思的反馈（宽松确认）
        surfaced = reflection_engine.load_surfaced(lanlan_name)
        pending_surfaced = [s for s in surfaced if s.get('feedback') is None]
        if pending_surfaced:
            user_msgs = _extract_user_messages(messages)
            if user_msgs:
                feedbacks = await reflection_engine.check_feedback(lanlan_name, user_msgs)
                # 收集 LLM 返回的 denied IDs
                denied_ids = {
                    fb.get('reflection_id')
                    for fb in feedbacks
                    if isinstance(fb, dict) and fb.get('feedback') == 'denied'
                }
                for s in pending_surfaced:
                    rid = s.get('reflection_id')
                    if rid in denied_ids:
                        reflection_engine.reject_promotion(lanlan_name, rid)
                    else:
                        # 宽松确认：用户有回复 + 未被 denied → 自动 confirm
                        reflection_engine.confirm_promotion(lanlan_name, rid)
    except Exception as e:
        logger.warning(f"[MemoryServer] 反馈检查失败: {e}")

    try:
        # 4. 审视矛盾队列（如果有 pending corrections）
        resolved = await persona_manager.resolve_corrections(lanlan_name)
        if resolved:
            logger.info(f"[MemoryServer] {lanlan_name}: 审视了 {resolved} 条 persona 矛盾")
    except Exception as e:
        logger.warning(f"[MemoryServer] 矛盾审视失败: {e}")


@app.post("/cache/{lanlan_name}")
async def cache_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """轻量级缓存：仅将新消息追加到 recent history，不触发 time_manager / review 等 LLM 操作。
    供 cross_server 在每轮 turn end 时调用，保持 memory_browser 实时可见。"""
    try:
        input_history = convert_to_messages(json.loads(request.input_history))
        if not input_history:
            return {"status": "cached", "count": 0}
        logger.info(f"[MemoryServer] cache: {lanlan_name} +{len(input_history)} 条消息")
        await recent_history_manager.update_history(input_history, lanlan_name, compress=False)
        return {"status": "cached", "count": len(input_history)}
    except Exception as e:
        logger.error(f"[MemoryServer] cache 失败: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/process/{lanlan_name}")
async def process_conversation(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = _config_manager.load_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")
        
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        logger.info(f"[MemoryServer] 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
        await recent_history_manager.update_history(input_history, lanlan_name)
        # 旧模块已禁用（性能不足）：
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await time_manager.store_conversation(uid, input_history, lanlan_name)

        # 异步事实提取（不阻塞返回，失败静默跳过）
        _spawn_background_task(_extract_facts_and_check_feedback(input_history, lanlan_name))

        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
        return {"status": "processed"}
    except Exception as e:
        logger.error(f"处理对话历史失败: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/renew/{lanlan_name}")
async def process_conversation_for_renew(request: HistoryRequest, lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks
    try:
        # 检查角色是否存在于配置中，如果不存在则记录信息但继续处理（允许新角色）
        try:
            character_data = _config_manager.load_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if lanlan_name not in catgirl_names:
                logger.info(f"[MemoryServer] renew: 角色 '{lanlan_name}' 不在配置中，但继续处理（可能是新创建的角色）")
        except Exception as e:
            logger.warning(f"检查角色配置失败: {e}，继续处理")
        
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        logger.info(f"[MemoryServer] renew: 收到 {lanlan_name} 的对话历史处理请求，消息数: {len(input_history)}")
        await recent_history_manager.update_history(input_history, lanlan_name, detailed=True)
        # 旧模块已禁用：
        # await settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # await semantic_manager.store_conversation(uid, input_history, lanlan_name)
        await time_manager.store_conversation(uid, input_history, lanlan_name)

        # 异步事实提取
        _spawn_background_task(_extract_facts_and_check_feedback(input_history, lanlan_name))
        
        # 在后台启动review_history任务
        if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
            # 如果已有任务在运行，取消它
            correction_tasks[lanlan_name].cancel()
            try:
                await correction_tasks[lanlan_name]
            except asyncio.CancelledError:
                pass
        
        # 启动新的review任务
        task = asyncio.create_task(_run_review_in_background(lanlan_name))
        correction_tasks[lanlan_name] = task
        
        return {"status": "processed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_recent_history/{lanlan_name}")
def get_recent_history(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空历史记录")
            return "开始聊天前，没有历史记录。\n"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return "开始聊天前，没有历史记录。\n"
    
    history = recent_history_manager.get_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _ = _config_manager.get_character_data()
    name_mapping['ai'] = lanlan_name
    result = f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
    for i in history:
        if i.type == 'system':
            result += i.content + "\n"
        else:
            texts = [j['text'] for j in i.content if j['type']=='text']
            joined = "\n".join(texts)
            result += f"{name_mapping[i.type]} | {joined}\n"
    return result

@app.get("/search_for_memory/{lanlan_name}/{query}")
async def get_memory(query: str, lanlan_name: str):
    """语义记忆已退环境，返回空结果占位。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    _lang = get_global_language()
    return (
        _loc(MEMORY_RECALL_HEADER, _lang).format(name=lanlan_name)
        + query
        + "\n\n"
        + _loc(MEMORY_RESULTS_HEADER, _lang).format(name=lanlan_name)
        + "\n（语义记忆已下线，暂无相关记忆片段。）"
    )

@app.get("/get_settings/{lanlan_name}")
def get_settings(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空设置")
            return f"{lanlan_name}记得{{}}"
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return f"{lanlan_name}记得{{}}"

    # 优先使用 persona markdown 渲染，回退到旧 settings 格式
    persona_md = persona_manager.render_persona_markdown(lanlan_name)
    if persona_md:
        return persona_md
    # 兼容回退
    result = f"{lanlan_name}记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}"
    return result


@app.get("/get_persona/{lanlan_name}")
def get_persona(lanlan_name: str):
    """返回完整 persona JSON（供 UI / memory_browser 使用）。"""
    lanlan_name = validate_lanlan_name(lanlan_name)
    return persona_manager.get_persona(lanlan_name)

@app.post("/reload")
async def reload_config():
    """重新加载记忆服务器配置（用于新角色创建后）"""
    try:
        success = await reload_memory_components()
        if success:
            return {"status": "success", "message": "配置已重新加载"}
        else:
            return {"status": "error", "message": "配置重新加载失败"}
    except Exception as e:
        logger.error(f"重新加载配置时出错: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.post("/cancel_correction/{lanlan_name}")
async def cancel_correction(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    """中断指定角色的记忆整理任务（用于记忆编辑后立即生效）"""
    global correction_tasks, correction_cancel_flags
    
    if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到取消请求，中断 {lanlan_name} 的correction任务")
        
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].set()
        
        correction_tasks[lanlan_name].cancel()
        try:
            await correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
        
        return {"status": "cancelled"}
    
    return {"status": "no_task"}

@app.get("/new_dialog/{lanlan_name}")
async def new_dialog(lanlan_name: str):
    lanlan_name = validate_lanlan_name(lanlan_name)
    global correction_tasks, correction_cancel_flags
    
    # 检查角色是否存在于配置中
    try:
        character_data = _config_manager.load_characters()
        catgirl_names = list(character_data.get('猫娘', {}).keys())
        if lanlan_name not in catgirl_names:
            logger.warning(f"角色 '{lanlan_name}' 不在配置中，返回空上下文")
            return PlainTextResponse("")
    except Exception as e:
        logger.error(f"检查角色配置失败: {e}")
        return PlainTextResponse("")
    
    # 中断正在进行的correction任务
    if lanlan_name in correction_tasks and not correction_tasks[lanlan_name].done():
        logger.info(f"🛑 收到new_dialog请求，中断 {lanlan_name} 的correction任务")
        
        # 设置取消标志
        if lanlan_name in correction_cancel_flags:
            correction_cancel_flags[lanlan_name].set()
        
        # 取消任务
        correction_tasks[lanlan_name].cancel()
        try:
            await correction_tasks[lanlan_name]
        except asyncio.CancelledError:
            logger.info(f"✅ {lanlan_name} 的correction任务已成功中断")
        except Exception as e:
            logger.warning(f"⚠️ 中断 {lanlan_name} 的correction任务时出现异常: {e}")
    
    # 正则表达式：删除所有类型括号及其内容（包括[]、()、{}、<>、【】、（）等）
    brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
    master_name, _, _, _, name_mapping, _, _, _, _ = _config_manager.get_character_data()
    name_mapping['ai'] = lanlan_name
    _lang = get_global_language()

    # ── [静态前缀] Persona 长期记忆（变化极少 → 最大化 prefix cache） ──
    # pending + confirmed 反思也注入上下文（分区标注）
    pending_reflections = reflection_engine.get_pending_reflections(lanlan_name)
    confirmed_reflections = reflection_engine.get_confirmed_reflections(lanlan_name)
    result = _loc(PERSONA_HEADER, _lang).format(name=lanlan_name)
    persona_md = persona_manager.render_persona_markdown(
        lanlan_name, pending_reflections, confirmed_reflections,
    )
    if persona_md:
        result += persona_md
    else:
        # 兼容回退：使用旧 settings
        result += f"{lanlan_name}记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}\n"

    # ── [动态部分] 内心活动（每次变化） ──
    result += _loc(INNER_THOUGHTS_HEADER, _lang).format(name=lanlan_name)
    result += _loc(INNER_THOUGHTS_DYNAMIC, _lang).format(
        name=lanlan_name,
        time=get_timestamp(),
    )

    # ── 距上次聊天间隔提示 ──
    try:
        from datetime import datetime as _dt
        last_time = time_manager.get_last_conversation_time(lanlan_name)
        if last_time:
            gap = _dt.now() - last_time
            gap_seconds = gap.total_seconds()
            if gap_seconds >= 3600:  # ≥ 1小时才显示
                hours = int(gap_seconds // 3600)
                minutes = int((gap_seconds % 3600) // 60)
                if minutes:
                    elapsed = _loc(ELAPSED_TIME_HM, _lang).format(h=hours, m=minutes)
                else:
                    elapsed = _loc(ELAPSED_TIME_H, _lang).format(h=hours)

                if gap_seconds >= 18000:  # ≥ 5小时：当前时间 + 间隔 + 长间隔提示，不额外换行
                    now_str = _dt.now().strftime("%Y-%m-%d %H:%M")
                    result += _loc(CHAT_GAP_CURRENT_TIME, _lang).format(now=now_str)
                    result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed)
                    result += _loc(CHAT_GAP_LONG_HINT, _lang).format(name=lanlan_name, master=master_name) + "\n"
                else:
                    result += _loc(CHAT_GAP_NOTICE, _lang).format(master=master_name, elapsed=elapsed) + "\n"
    except Exception as e:
        logger.warning(f"计算聊天间隔失败: {e}")

    for i in recent_history_manager.get_recent_history(lanlan_name):
        if isinstance(i.content, str):
            cleaned_content = brackets_pattern.sub('', i.content).strip()
            result += f"{name_mapping[i.type]} | {cleaned_content}\n"
        else:
            texts = [brackets_pattern.sub('', j['text']).strip() for j in i.content if j['type'] == 'text']
            result += f"{name_mapping[i.type]} | " + "\n".join(texts) + "\n"
    return PlainTextResponse(result)

if __name__ == "__main__":
    import threading
    import time
    import signal
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Memory Server')
    parser.add_argument('--enable-shutdown', action='store_true', 
                       help='启用响应退出请求功能（仅在终端用户环境使用）')
    args = parser.parse_args()
    
    # 设置全局变量
    enable_shutdown = args.enable_shutdown
    
    # 创建一个后台线程来监控关闭信号
    def monitor_shutdown():
        while not shutdown_event.is_set():
            time.sleep(0.1)
        logger.info("检测到关闭信号，正在关闭memory_server...")
        # 发送SIGTERM信号给当前进程
        os.kill(os.getpid(), signal.SIGTERM)
    
    # 只有在启用关闭功能时才启动监控线程
    if enable_shutdown:
        shutdown_monitor = threading.Thread(target=monitor_shutdown, daemon=True)
        shutdown_monitor.start()
    
    # 启动服务器
    uvicorn.run(app, host="127.0.0.1", port=MEMORY_SERVER_PORT)