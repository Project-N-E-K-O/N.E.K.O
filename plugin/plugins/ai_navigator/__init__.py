"""
AI 网站导航插件 (AI-Navigator)

功能：
- 提供完整的 AI 平台导航 Web UI 界面
- 猫娘可通过插件调用在浏览器中打开任意 AI 平台网站
- 支持 30+ 国内外主流 AI 平台（对话、图像、视频、音乐、智能体）

入口：
- open_platform   打开指定的 AI 平台网站
- open_ui         打开 AI 导航 Web UI 界面
- list_platforms  列出所有可用的 AI 平台
- get_platform    获取指定平台的详细信息
"""

from __future__ import annotations

import asyncio
import sys
import subprocess
from typing import Any, Optional

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
    get_plugin_logger,
)


def _match_platform(query: str):
    """多策略模糊匹配平台名称，支持中英文简称、别名、ID、混合输入"""
    q = query.strip().lower()
    if not q:
        return None

    q_clean = q.replace("-", "").replace("_", "").replace(".", "").replace(" ", "")

    scored_matches = []

    for p in PLATFORMS:
        pid = p["id"].lower()
        pname = p["name"].lower()
        paliases = [a.lower() for a in p.get("aliases", [])]
        pname_clean = pname.replace("-", "").replace("_", "").replace(" ", "").replace(".", "")

        score = 0

        if q == pid or q == pname:
            return p

        if q in pid:
            score += 80
        if q in pname:
            score += 70

        for alias in paliases:
            alias_clean = alias.replace("-", "").replace("_", "").replace(" ", "").replace(".", "")
            if q == alias or q in alias:
                score += 60
            if alias_clean and q_clean in alias_clean:
                score += 50
            if q_clean and alias_clean and len(q_clean) >= 2 and q_clean in alias_clean:
                score += 45

        if q_clean in pname_clean and len(q_clean) >= 2:
            score += 30

        if score > 0:
            scored_matches.append((score, p))

    if scored_matches:
        scored_matches.sort(key=lambda x: x[0], reverse=True)
        return scored_matches[0][1]

    return None


def _open_url_in_browser(url: str) -> None:
    """在默认浏览器打开 URL（同步调用，仅供 asyncio.to_thread 使用）"""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
    except Exception as e:
        raise RuntimeError(f"无法在浏览器中打开 {url}: {e}")


PLATFORMS = [
    {"id": "yiyan", "name": "文心一言", "aliases": ["文心", "一言", "文心一言", "百度", "yiyan", "ernie", "百度ai", "baidu"], "url": "https://yiyan.baidu.com/", "description": "百度知识增强大语言模型", "category": "chat", "region": "cn"},
    {"id": "qianwen", "name": "通义千问", "aliases": ["千问", "通义", "通义千问", "qwen", "qianwen", "阿里", "aliyun", "alibaba", "ali", "通义大模型"], "url": "https://www.qianwen.com/chat", "description": "阿里云超大规模语言模型", "category": "chat", "region": "cn"},
    {"id": "deepseek", "name": "DeepSeek", "aliases": ["深度求索", "deepseek", "deep seek", "ds", "deep seek ai", "深度"], "url": "https://chat.deepseek.com/", "description": "深度求索AI智能助手", "category": "chat", "region": "cn"},
    {"id": "doubao", "name": "豆包", "aliases": ["字节", "doubao", "dou bao", "字节跳动", "bytedance", "豆包ai"], "url": "https://www.doubao.com/chat", "description": "字节跳动AI智能助手", "category": "chat", "region": "cn"},
    {"id": "chatgpt", "name": "ChatGPT", "aliases": ["gpt", "openai", "GPT-4", "chat gpt", "chatgpt", "chat-gpt", "chatgpt4", "gpt4", "o1", "o3", "o3mini", "o1mini"], "url": "https://chatgpt.com/", "description": "OpenAI现象级对话产品", "category": "chat", "region": "global"},
    {"id": "kimi", "name": "Kimi", "aliases": ["月之暗面", "kimi", "moonshot", "kimi ai", "kimi智能助手"], "url": "https://www.kimi.com/?chat_enter_method=new_chat", "description": "月之暗面长文本AI", "category": "chat", "region": "cn"},
    {"id": "chatglm", "name": "智谱清言", "aliases": ["智谱", "清言", "智谱清言", "glm", "chatglm", "chat glm", "chat-glm", "glm4", "智谱ai", "zhipu"], "url": "https://chatglm.cn/main/alltoolsdetail", "description": "智谱AI对话模型", "category": "chat", "region": "cn"},
    {"id": "mistral", "name": "Mistral", "aliases": ["mistral", "mistral ai", "欧洲", "法国", "mistralllm"], "url": "https://chat.mistral.ai/chat", "description": "欧洲开源大语言模型", "category": "chat", "region": "global"},
    {"id": "perplexity", "name": "Perplexity", "aliases": ["pplx", "ppl", "perplexity", "per plexity", "perplexity ai", "ppx", "perplex"], "url": "https://www.perplexity.ai/", "description": "AI联网搜索引擎", "category": "chat", "region": "global"},
    {"id": "copilot", "name": "Copilot", "aliases": ["微软", "copilot", "co pilot", "co-pilot", "copliot", "microsoft", "bing", "必应", "gpt4o", "微软ai"], "url": "https://copilot.microsoft.com/", "description": "微软AI智能助手", "category": "chat", "region": "global"},
    {"id": "xinghuo", "name": "讯飞星火", "aliases": ["讯飞", "星火", "讯飞星火", "科大讯飞", "xinghuo", "iflytek", "spark", "spark desk"], "url": "https://xinghuo.xfyun.cn/desk", "description": "科大讯飞认知大模型", "category": "chat", "region": "cn"},
    {"id": "yuanbao", "name": "腾讯元宝", "aliases": ["元宝", "腾讯", "腾讯元宝", "混元", "yuanbao", "hunyuan", "tencent", "腾讯ai"], "url": "https://yuanbao.tencent.com/chat", "description": "腾讯混元大语言模型", "category": "chat", "region": "cn"},
    {"id": "stepfun", "name": "阶跃星辰", "aliases": ["阶跃", "阶跃星辰", "stepfun", "step fun", "step fun ai", "跃问"], "url": "https://www.stepfun.com/chats/new", "description": "阶跃星辰多模态大模型", "category": "chat", "region": "cn"},
    {"id": "gemini", "name": "Gemini", "aliases": ["google", "谷歌", "双子座", "gemini", "bard", "gemini ai", "google gemini", "google ai", "gemini pro", "gemini ultra", "gemini flash"], "url": "https://gemini.google.com/", "description": "Google多模态AI助手", "category": "chat", "region": "global"},
    {"id": "jimeng", "name": "即梦 AI", "aliases": ["即梦", "即梦ai", "即梦AI", "剪映", "jimeng", "ji meng", "jimeng ai", "字节ai绘图", "即梦绘图"], "url": "https://jimeng.jianying.com/ai-tool/home", "description": "字节AI图像创作平台", "category": "image", "region": "cn"},
    {"id": "midjourney", "name": "Midjourney", "aliases": ["mj", "mid journey", "mid-journey", "midjourney", "mid journey", "mj ai", "mjai", "mdj", "midjourney ai", "mjourney"], "url": "https://www.midjourney.com/explore", "description": "顶级AI绘画生成", "category": "image", "region": "global"},
    {"id": "ideogram", "name": "Ideogram", "aliases": ["ideogram", "ideo gram", "ideogram ai", "ideogramai", "ideo"], "url": "https://ideogram.ai/t/explore", "description": "AI图像生成设计", "category": "image", "region": "global"},
    {"id": "kling", "name": "可灵 AI", "aliases": ["可灵", "可灵ai", "可灵AI", "kling", "k ling", "快手", "kuaishou", "kling ai", "可灵视频"], "url": "https://klingai.com/app", "description": "快手AI视频生成", "category": "video", "region": "cn"},
    {"id": "runway", "name": "Runway", "aliases": ["runway", "run way", "runwayml", "runway ml", "专业视频", "runway ai"], "url": "https://app.runwayml.com/video-tools", "description": "专业AI视频工具", "category": "video", "region": "global"},
    {"id": "pika", "name": "Pika", "aliases": ["pika", "pik a", "pika ai", "pika labs", "pikalabs", "pika艺术"], "url": "https://pika.art/", "description": "AI视频生成创作", "category": "video", "region": "global"},
    {"id": "sora", "name": "Sora", "aliases": ["sora", "openai视频", "sora ai", "sora video", "sora2"], "url": "https://sora.chatgpt.com/profile", "description": "OpenAI视频生成", "category": "video", "region": "global"},
    {"id": "tongyiwan", "name": "通义万相", "aliases": ["万相", "通义万相", "通义万象", "tongyiwan", "tong yi wan", "wanxiang", "wan xiang", "wanxiang", "aliwan", "阿里万相", "阿里万象"], "url": "https://tongyi.aliyun.com/wan/explore", "description": "阿里云图像视频生成", "category": "video", "region": "cn"},
    {"id": "firefly", "name": "Firefly", "aliases": ["adobe", "萤火虫", "firefly", "fire fly", "adobe firefly", "adobe ai", "ps", "photoshop", "ae"], "url": "https://firefly.adobe.com/generate/video", "description": "Adobe AI创意工具", "category": "video", "region": "global"},
    {"id": "soundful", "name": "Soundful", "aliases": ["soundful", "sound ful", "soundful ai", "soundful music"], "url": "https://my.soundful.com/", "description": "AI音乐生成平台", "category": "music", "region": "global"},
    {"id": "udio", "name": "Udio", "aliases": ["udio", "u dio", "udio ai", "udio music", "ud io"], "url": "https://www.udio.com/home", "description": "AI音乐创作生成", "category": "music", "region": "global"},
    {"id": "lalal", "name": "LALAL.AI", "aliases": ["lalal", "lala", "lalal.ai", "lalal ai", "音频分离", "lala.ai", "lall.ai", "人声分离", "伴奏提取"], "url": "https://www.lalal.ai/", "description": "AI音频分离提取", "category": "music", "region": "global"},
    {"id": "stableaudio", "name": "Stable Audio", "aliases": ["stable audio", "stability", "stableaudio", "stable audio", "stable audio ai", "stability ai音乐"], "url": "https://stableaudio.com/generate", "description": "Stability AI音乐生成", "category": "music", "region": "global"},
    {"id": "haimian", "name": "海绵音乐", "aliases": ["海绵", "海绵音乐", "haimian", "hai mian", "haimian music", "海绵ai", "海绵ai音乐"], "url": "https://www.haimian.com/", "description": "字节跳动AI音乐", "category": "music", "region": "cn"},
    {"id": "suno", "name": "Suno", "aliases": ["suno", "su no", "suno ai", "suno music", "suno音乐", "sunoai"], "url": "https://suno.com/", "description": "AI音乐创作生成", "category": "music", "region": "global"},
    {"id": "dify", "name": "Dify", "aliases": ["dify", "di fy", "开源平台", "dify ai", "dify平台", "dif y"], "url": "https://cloud.dify.ai/apps", "description": "开源LLM应用开发平台", "category": "agent", "region": "global"},
    {"id": "yuanqi", "name": "腾讯元器", "aliases": ["元器", "腾讯", "腾讯元器", "yuanqi", "yuan qi", "tencent yuanqi", "元器平台", "元器智能体"], "url": "https://yuanqi.tencent.com/", "description": "腾讯智能体开发平台", "category": "agent", "region": "cn"},
    {"id": "flowai", "name": "Flow AI", "aliases": ["flow", "flow ai", "flowai", "工作流", "自动化", "工作流平台", "flowai平台", "flowaicc", "flow ai平台", "智能体工作流", "flowcc", "工作流智能体", "工作台", "ai工作台", "ai工作台页面"], "url": "https://flowai.cc/dashboard/projects", "description": "AI工作流自动化平台", "category": "agent", "region": "cn"},
    {"id": "betteryeah", "name": "BetterYeah", "aliases": ["better yeah", "betteryeah", "better yeah", "企业智能体", "betteryeah ai", "better yeah ai", "better yeah平台"], "url": "https://ai.betteryeah.com/explore", "description": "企业AI智能体平台", "category": "agent", "region": "cn"},
    {"id": "coze", "name": "扣子 Coze", "aliases": ["扣子", "coze", "扣子coze", "扣子 coze", "co ze", "coze ai", "coze平台", "coze智能体", "coze bot", "扣子平台", "扣子智能体", "字节coze", "字节扣子"], "url": "https://www.coze.cn/", "description": "字节AI智能体开发平台", "category": "agent", "region": "cn"},
]

CATEGORY_NAMES = {
    "chat": "对话大模型",
    "image": "图片生成",
    "video": "视频生成",
    "music": "音乐生成",
    "agent": "工作流智能体",
}


@neko_plugin
class AiNavigatorPlugin(NekoPluginBase):
    """AI 网站导航插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = get_plugin_logger(__name__)

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        """插件启动：注册静态 UI"""
        self.logger.info("AI 网站导航插件启动中...")

        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate",
            )
            if ok:
                self.logger.info("✅ AI 导航 UI 已注册，访问: http://localhost:48916/plugin/ai_navigator/ui/")
            else:
                self.logger.warning("注册静态 UI 失败")

        return Ok({
            "status": "ready",
            "total_platforms": len(PLATFORMS),
            "categories": list(CATEGORY_NAMES.values()),
            "message": f"✅ AI 网站导航插件已就绪，共收录 {len(PLATFORMS)} 个平台",
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        """插件关闭"""
        self.logger.info("AI 网站导航插件已关闭")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="open_platform",
        name="打开 AI 平台",
        description="在浏览器中打开指定的 AI 平台网站。支持的平台包括：文心一言、通义千问、DeepSeek、豆包、ChatGPT、Kimi、智谱清言、Mistral、Perplexity、Copilot、讯飞星火、腾讯元宝、阶跃星辰、Gemini、即梦AI、Midjourney、Ideogram、可灵AI、Runway、Pika、Sora、通义万相、Firefly、Soundful、Udio、LALAL.AI、Stable Audio、海绵音乐、Suno、Dify、腾讯元器、Flow AI、BetterYeah、扣子Coze",
        input_schema={
            "type": "object",
            "properties": {
                "platform_name": {
                    "type": "string",
                    "description": "平台名称或ID（如 '文心一言'、'qianwen'、'ChatGPT' 等）"
                }
            },
            "required": ["platform_name"]
        },
        llm_result_fields=["message"]
    )
    async def open_platform(self, platform_name: str, **_):
        """在浏览器中打开指定的 AI 平台网站"""
        matched = _match_platform(platform_name)

        if not matched:
            available_names = ", ".join(p["name"] for p in PLATFORMS)
            return Err(SdkError(
                f"未找到平台 '{platform_name}'。\n"
                f"可用平台：{available_names}"
            ))

        url = matched["url"]
        try:
            await asyncio.to_thread(_open_url_in_browser, url)
            self.logger.info(f"已在浏览器中打开: {matched['name']} ({url})")
            return Ok({
                "success": True,
                "platform": matched["name"],
                "url": url,
                "message": f"✅ 已在浏览器中打开 {matched['name']}\n{matched['description']}\n{url}",
            })
        except Exception as e:
            self.logger.exception("打开浏览器失败")
            return Err(SdkError(f"打开浏览器失败: {e}"))

    @plugin_entry(
        id="open_ui",
        name="打开 AI 导航界面",
        description="在浏览器中打开 AI 网站导航的完整 Web UI 界面，可浏览和点击访问所有收录的 AI 平台",
        kind="action"
    )
    async def open_ui(self, **_):
        """在浏览器中打开 AI 导航 Web UI"""
        url = "http://localhost:48916/plugin/ai_navigator/ui/"
        try:
            await asyncio.to_thread(_open_url_in_browser, url)
            self.logger.info(f"已在浏览器中打开: {url}")
            return Ok({"success": True, "url": url, "message": f"已在浏览器打开 AI 导航界面"})
        except Exception as e:
            self.logger.exception("打开 UI 失败")
            return Err(SdkError(f"打开 UI 失败: {e}"))

    @plugin_entry(
        id="list_platforms",
        name="列出所有 AI 平台",
        description="【必须调用】当用户问"有多少个平台"、"列出所有平台"、"列出全部平台"、"列出全部"、"全部平台"、"全部列出"、"列出"等平台列表相关问题时，必须调用此入口！不要从自己的知识里编造平台！此插件共收录 34 个平台，分为 5 大类：对话大模型 14 个、图片生成 3 个、视频生成 6 个、音乐生成 6 个、工作流智能体 5 个。调用后请直接使用返回结果回答用户，不要添加任何插件里没有的平台（如 Claude、DALL-E、Stable Diffusion 等不在收录范围内）。",
        llm_result_fields=["message"]
    )
    async def list_platforms(self, **_):
        """列出所有可用的 AI 平台"""
        lines = ["✨ AI 平台导航列表", ""]

        for cat_key in ["chat", "image", "video", "music", "agent"]:
            cat_platforms = [p for p in PLATFORMS if p["category"] == cat_key]
            lines.append(f"▎{CATEGORY_NAMES[cat_key]}（{len(cat_platforms)} 个）:")
            for p in cat_platforms:
                region_tag = "国内" if p["region"] == "cn" else "海外"
                lines.append(f"  • {p['name']} [{region_tag}] - {p['description']}")
            lines.append("")

        lines.append(f"共收录 {len(PLATFORMS)} 个 AI 平台")

        return Ok({
            "success": True,
            "total": len(PLATFORMS),
            "message": "\n".join(lines),
        })

    @plugin_entry(
        id="get_platform",
        name="获取平台信息",
        description="获取指定 AI 平台的详细信息，包括名称、URL、描述等",
        input_schema={
            "type": "object",
            "properties": {
                "platform_name": {
                    "type": "string",
                    "description": "平台名称或ID"
                }
            },
            "required": ["platform_name"]
        },
        llm_result_fields=["message"]
    )
    async def get_platform(self, platform_name: str, **_):
        """获取指定平台的详细信息"""
        matched = _match_platform(platform_name)

        if not matched:
            return Err(SdkError(f"未找到平台 '{platform_name}'"))

        region_tag = "国内" if matched["region"] == "cn" else "海外"
        cat_name = CATEGORY_NAMES.get(matched["category"], matched["category"])

        lines = [
            f"🔍 平台信息",
            "",
            f"名称: {matched['name']}",
            f"ID: {matched['id']}",
            f"分类: {cat_name}",
            f"地区: {region_tag}",
            f"描述: {matched['description']}",
            f"URL: {matched['url']}",
        ]

        return Ok({
            "success": True,
            "platform": matched,
            "message": "\n".join(lines),
        })
