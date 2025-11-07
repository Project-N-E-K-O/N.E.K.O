# 不再使用 import *，只导入需要的
from config.prompts_chara import lanlan_prompt
import json
import os
import logging
import os
from pathlib import Path
from utils.config_manager import get_config_manager

# Setup logger for this module
logger = logging.getLogger(__name__)

# 初始化配置管理器（自动迁移配置文件）
_config_manager = get_config_manager()

# 读取角色配置
CHARACTER_JSON_PATH = str(_config_manager.get_config_path('characters.json'))
CORE_CONFIG_PATH = str(_config_manager.get_config_path('core_config.json'))
USER_PREFERENCES_PATH = str(_config_manager.get_config_path('user_preferences.json'))
# 默认值
_default_master = {"档案名": "哥哥", "性别": "男", "昵称": "哥哥"}
_default_lanlan = {"test": {"性别": "女", "年龄": 15, "昵称": "T酱, 小T", "live2d": "mao_pro", "voice_id": "", "system_prompt": lanlan_prompt}}


def load_characters(character_json_path=None):
    """加载角色配置"""
    if character_json_path is None:
        character_json_path = CHARACTER_JSON_PATH
    
    try:
        with open(character_json_path, 'r', encoding='utf-8') as f:
            character_data = json.load(f)
    except FileNotFoundError:
        logger.info(f"未找到猫娘配置文件: {character_json_path}，创建默认配置。")
        character_data = {"主人": _default_master, "猫娘": _default_lanlan}
        # 保存默认配置
        save_characters(character_data, character_json_path)
    except Exception as e:
        logger.error(f"💥 读取猫娘配置文件出错: {e}，使用默认人设。")
        character_data = {"主人": _default_master, "猫娘": _default_lanlan}
    return character_data

def save_characters(data, character_json_path=None):
    """保存角色配置"""
    if character_json_path is None:
        character_json_path = CHARACTER_JSON_PATH
    
    # 确保目录存在
    Path(character_json_path).parent.mkdir(parents=True, exist_ok=True)
    
    with open(character_json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_character_data():
    """获取角色数据"""
    character_data = load_characters()
    # MASTER_NAME 必须始终存在，取档案名
    master_name = character_data.get('主人', {}).get('档案名', _default_master['档案名'])
    # 获取所有猫娘名
    catgirl_names = list(character_data['猫娘'].keys()) if character_data['猫娘'] and len(character_data['猫娘']) > 0 else list(_default_lanlan.keys())
    
    # 获取当前猫娘，如果没有设置或设置的猫娘不存在，则使用第一个猫娘
    current_catgirl = character_data.get('当前猫娘', '')
    if current_catgirl and current_catgirl in catgirl_names:
        her_name = current_catgirl
    else:
        her_name = catgirl_names[0] if catgirl_names else ''
        # 如果当前猫娘无效（不存在或为空），自动更新配置文件
        if her_name and current_catgirl != her_name:
            logger.info(f"当前猫娘配置无效 ('{current_catgirl}')，已自动切换到 '{her_name}'")
            character_data['当前猫娘'] = her_name
            save_characters(character_data)
    
    master_basic_config = character_data.get('主人', _default_master)
    lanlan_basic_config = character_data['猫娘'] if catgirl_names else _default_lanlan

    NAME_MAPPING = {'human': master_name, 'system': "SYSTEM_MESSAGE"}
    # 生成以猫娘名为key的各类store（现在使用我的文档下的memory目录）
    LANLAN_PROMPT = {name: character_data['猫娘'][name].get('system_prompt', lanlan_prompt) for name in catgirl_names}
    
    # 使用配置管理器获取memory目录
    memory_base = str(_config_manager.memory_dir)
    SEMANTIC_STORE = {name: f'{memory_base}/semantic_memory_{name}' for name in catgirl_names}
    TIME_STORE = {name: f'{memory_base}/time_indexed_{name}' for name in catgirl_names}
    SETTING_STORE = {name: f'{memory_base}/settings_{name}.json' for name in catgirl_names}
    RECENT_LOG = {name: f'{memory_base}/recent_{name}.json' for name in catgirl_names}

    return master_name, her_name, master_basic_config, lanlan_basic_config, NAME_MAPPING, LANLAN_PROMPT, SEMANTIC_STORE, TIME_STORE, SETTING_STORE, RECENT_LOG

TIME_ORIGINAL_TABLE_NAME = "time_indexed_original"
TIME_COMPRESSED_TABLE_NAME = "time_indexed_compressed"

MODELS_WITH_EXTRA_BODY = ["qwen-flash-2025-07-28", "qwen3-vl-plus-2025-09-23"]

def get_core_config():
    """
    动态读取核心配置
    返回一个包含所有核心配置的字典
    """
    # 从 config/api.py 导入默认值
    from config.api import (
        CORE_API_KEY as DEFAULT_CORE_API_KEY,
        AUDIO_API_KEY as DEFAULT_AUDIO_API_KEY,
        OPENROUTER_API_KEY as DEFAULT_OPENROUTER_API_KEY,
        MCP_ROUTER_API_KEY as DEFAULT_MCP_ROUTER_API_KEY,
        CORE_URL as DEFAULT_CORE_URL,
        CORE_MODEL as DEFAULT_CORE_MODEL,
        OPENROUTER_URL as DEFAULT_OPENROUTER_URL,
        SUMMARY_MODEL as DEFAULT_SUMMARY_MODEL,
        CORRECTION_MODEL as DEFAULT_CORRECTION_MODEL,
        EMOTION_MODEL as DEFAULT_EMOTION_MODEL
    )
    
    # 初始化配置
    config = {
        'CORE_API_KEY': DEFAULT_CORE_API_KEY,
        'AUDIO_API_KEY': DEFAULT_AUDIO_API_KEY,
        'OPENROUTER_API_KEY': DEFAULT_OPENROUTER_API_KEY,
        'MCP_ROUTER_API_KEY': DEFAULT_MCP_ROUTER_API_KEY,
        'CORE_URL': DEFAULT_CORE_URL,
        'CORE_MODEL': DEFAULT_CORE_MODEL,
        'CORE_API_TYPE': 'qwen',
        'OPENROUTER_URL': DEFAULT_OPENROUTER_URL,
        'SUMMARY_MODEL': DEFAULT_SUMMARY_MODEL,
        'CORRECTION_MODEL': DEFAULT_CORRECTION_MODEL,
        'EMOTION_MODEL': DEFAULT_EMOTION_MODEL,
        'ASSIST_API_KEY_QWEN': DEFAULT_CORE_API_KEY,
        'ASSIST_API_KEY_OPENAI': DEFAULT_CORE_API_KEY,
        'ASSIST_API_KEY_GLM': DEFAULT_CORE_API_KEY,
        'ASSIST_API_KEY_STEP': DEFAULT_CORE_API_KEY,
        'ASSIST_API_KEY_SILICON': DEFAULT_CORE_API_KEY,
        'COMPUTER_USE_MODEL': 'glm-4.5v',
        'COMPUTER_USE_GROUND_MODEL': 'glm-4.5v',
        'COMPUTER_USE_MODEL_URL': 'https://open.bigmodel.cn/api/paas/v4',
        'COMPUTER_USE_GROUND_URL': 'https://open.bigmodel.cn/api/paas/v4',
        'COMPUTER_USE_MODEL_API_KEY': '',
        'COMPUTER_USE_GROUND_API_KEY': '',
        'IS_FREE_VERSION': False,  # 标识是否为免费版
    }
    
    try:
        with open(CORE_CONFIG_PATH, 'r', encoding='utf-8') as f:
            core_cfg = json.load(f)
        
        # 更新API Key
        if 'coreApiKey' in core_cfg and core_cfg['coreApiKey']:
            config['CORE_API_KEY'] = core_cfg['coreApiKey']
        
        # 读取 core_api 类型
        config['CORE_API_TYPE'] = core_cfg.get('coreApi', 'qwen')
        
        # 根据 coreApi 类型设置 CORE_URL 和 CORE_MODEL
        if 'coreApi' in core_cfg and core_cfg['coreApi']:
            if core_cfg['coreApi'] == 'free':
                # 免费版配置
                config['CORE_URL'] = "ws://47.100.209.206:9805" #还在备案，之后会换成wss+域名
                config['CORE_MODEL'] = "free-model"  # 免费版无需指定模型
                config['CORE_API_KEY'] = "free-access"  # 免费版无需真实API key
                config['IS_FREE_VERSION'] = True
            elif core_cfg['coreApi'] == 'qwen':
                config['CORE_URL'] = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
                config['CORE_MODEL'] = "qwen3-omni-flash-realtime"
            elif core_cfg['coreApi'] == 'glm':
                config['CORE_URL'] = "wss://open.bigmodel.cn/api/paas/v4/realtime"
                config['CORE_MODEL'] = "glm-realtime-air"
            elif core_cfg['coreApi'] == 'openai':
                config['CORE_URL'] = "wss://api.openai.com/v1/realtime"
                config['CORE_MODEL'] = "gpt-realtime"
            elif core_cfg['coreApi'] == 'step':
                config['CORE_URL'] = "wss://api.stepfun.com/v1/realtime"
                config['CORE_MODEL'] = "step-audio-2"
        
        # 读取各种辅助API Key
        config['ASSIST_API_KEY_QWEN'] = core_cfg.get('assistApiKeyQwen', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_OPENAI'] = core_cfg.get('assistApiKeyOpenai', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_GLM'] = core_cfg.get('assistApiKeyGlm', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_STEP'] = core_cfg.get('assistApiKeyStep', '') or config['CORE_API_KEY']
        config['ASSIST_API_KEY_SILICON'] = core_cfg.get('assistApiKeySilicon', '') or config['CORE_API_KEY']
        
        # 读取MCP Token
        if 'mcpToken' in core_cfg and core_cfg['mcpToken']:
            config['MCP_ROUTER_API_KEY'] = core_cfg['mcpToken']
        
        # Computer Use配置
        config['COMPUTER_USE_MODEL_API_KEY'] = config['COMPUTER_USE_GROUND_API_KEY'] = config['ASSIST_API_KEY_GLM']
        
        # 根据 assistApi 类型设置辅助模型
        if 'coreApi' in core_cfg and core_cfg['coreApi'] == 'free':
            # 免费版辅助API配置
            config['assistApi'] = 'free'
            config['OPENROUTER_URL'] = "http://47.100.209.206:9807/v1" #还在备案，之后会换成https+域名
            config['SUMMARY_MODEL'] = "free-model"
            config['CORRECTION_MODEL'] = "free-model"
            config['EMOTION_MODEL'] = "free-model"
            config['VISION_MODEL'] = "free-vision-model"
            config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = "free-access"
            config['IS_FREE_VERSION'] = True
        elif 'assistApi' in core_cfg and core_cfg['assistApi']:
            if core_cfg['assistApi'] == 'qwen':
                config['OPENROUTER_URL'] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                config['SUMMARY_MODEL'] = "qwen3-next-80b-a3b-instruct"
                config['CORRECTION_MODEL'] = "qwen3-235b-a22b-instruct-2507"
                config['EMOTION_MODEL'] = "qwen-flash-2025-07-28"
                config['VISION_MODEL'] = "qwen3-vl-plus-2025-09-23"
                config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_QWEN']
            elif core_cfg['assistApi'] == 'openai':
                config['OPENROUTER_URL'] = "https://api.openai.com/v1"
                config['SUMMARY_MODEL'] = "gpt-4.1-mini"
                config['CORRECTION_MODEL'] = "gpt-5-chat-latest"
                config['EMOTION_MODEL'] = "gpt-4.1-nano"
                config['VISION_MODEL'] = "gpt-5-chat-latest"
                config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_OPENAI']
            elif core_cfg['assistApi'] == 'glm':
                config['OPENROUTER_URL'] = "https://open.bigmodel.cn/api/paas/v4"
                config['SUMMARY_MODEL'] = "glm-4.5-flash"
                config['CORRECTION_MODEL'] = "glm-4.5-air"
                config['EMOTION_MODEL'] = "glm-4.5-flash"
                config['VISION_MODEL'] = "glm-4v-plus-0111"
                config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_GLM']
            elif core_cfg['assistApi'] == 'step':
                config['OPENROUTER_URL'] = "https://api.stepfun.com/v1"
                config['SUMMARY_MODEL'] = "step-2-mini"
                config['CORRECTION_MODEL'] = "step-2-mini"
                config['EMOTION_MODEL'] = "step-2-mini"
                config['VISION_MODEL'] = "step-1o-turbo-vision"
                config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_STEP']
            elif core_cfg['assistApi'] == 'silicon':
                config['OPENROUTER_URL'] = "https://api.siliconflow.cn/v1"
                config['SUMMARY_MODEL'] = "Qwen/Qwen3-Next-80B-A3B-Instruct"
                config['CORRECTION_MODEL'] = "deepseek-ai/DeepSeek-V3.2-Exp"
                config['EMOTION_MODEL'] = "inclusionAI/Ling-mini-2.0"
                config['VISION_MODEL'] = "Qwen/Qwen3-VL-235B-A22B-Instruct"
                config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_SILICON']
        else:
            # 默认使用qwen
            config['OPENROUTER_URL'] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            config['SUMMARY_MODEL'] = "qwen-plus-2025-07-14"
            config['CORRECTION_MODEL'] = "qwen3-235b-a22b-instruct-2507"
            config['EMOTION_MODEL'] = "qwen-turbo-2025-07-15"
            config['VISION_MODEL'] = "qwen3-vl-plus-2025-09-23"
            config['AUDIO_API_KEY'] = config['OPENROUTER_API_KEY'] = config['ASSIST_API_KEY_QWEN']
    
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(f"💥 Error parsing Core API Key: {e}")
    
    # 确保有默认值
    if not config['AUDIO_API_KEY']:
        config['AUDIO_API_KEY'] = config['CORE_API_KEY']
    if not config['OPENROUTER_API_KEY']:
        config['OPENROUTER_API_KEY'] = config['CORE_API_KEY']
    
    return config

# 但是保留不易变的常量（端口、表名等）
from config.api import (
    MAIN_SERVER_PORT,
    MEMORY_SERVER_PORT,
    MONITOR_SERVER_PORT,
    COMMENTER_SERVER_PORT,
    TOOL_SERVER_PORT,
    MCP_ROUTER_URL,
    ROUTER_MODEL,
    SETTING_PROPOSER_MODEL,
    SETTING_VERIFIER_MODEL,
    SEMANTIC_MODEL,
    RERANKER_MODEL
)

# 这些也是不易变的
__all__ = [
    # 函数
    'get_character_data',
    'get_core_config',
    'load_characters',
    'save_characters',
    # 路径
    'CHARACTER_JSON_PATH',
    'CORE_CONFIG_PATH',
    'USER_PREFERENCES_PATH',
    # 不易变的常量
    'TIME_ORIGINAL_TABLE_NAME',
    'TIME_COMPRESSED_TABLE_NAME',
    'MODELS_WITH_EXTRA_BODY',
    'MAIN_SERVER_PORT',
    'MEMORY_SERVER_PORT',
    'MONITOR_SERVER_PORT',
    'COMMENTER_SERVER_PORT',
    'TOOL_SERVER_PORT',
    'MCP_ROUTER_URL',
    'ROUTER_MODEL',
    'SETTING_PROPOSER_MODEL',
    'SETTING_VERIFIER_MODEL',
    'SEMANTIC_MODEL',
    'RERANKER_MODEL',
]
