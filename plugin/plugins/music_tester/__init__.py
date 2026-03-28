# -*- coding: utf-8 -*-
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, Result
)
from typing import Any
import asyncio

@neko_plugin
class MusicTesterPlugin(NekoPluginBase):
    """用于验证音乐白名单竞态保护机制的测试插件"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self.logger.info("[MusicTester] 插件已启动")
        return Ok({"status": "ready"})

    @plugin_entry(
        id="test_race",
        name="测试竞态保护",
        description="同时发送'域名注册'和'播放请求'，验证前端是否能正确缓冲并播放",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要测试的音乐链接",
                    "default": "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/Tours/Enthusiast/Tours_-_01_-_Enthusiast.mp3"
                }
            }
        }
    )
    async def test_race(self, url: str = "", **_):
        if not url:
            return Err("请提供测试 URL")

        self.logger.info(f"[MusicTester] 开始竞态测试: {url}")
        
        # 1. 立即注册域名 (内部已封装好正确的 push_message)
        self.register_music_domains(url)
        
        # 2. 立即发送播放请求 (纠正参数名和结构)
        self.push_message(
            source=self.plugin_id,
            message_type="music_play_url",
            metadata={
                "url": url,
                "name": "竞态测试音频",
                "artist": "N.E.K.O Tester"
            }
        )

        return Ok({
            "message": "已同时发出注册和播放指令，请观察浏览器控制台是否出现'收到加白信号'的逻辑。",
            "url": url
        })

    @plugin_entry(
        id="test_normal",
        name="常规播放",
        description="仅发送播放请求（如果域名未注册，应被正常拦截）",
        input_schema={
            "type": "object",
            "properties": {
                "url": { "type": "string", "description": "音乐链接" }
            }
        }
    )
    async def test_normal(self, url: str, **_):
        self.push_message(
            source=self.plugin_id,
            message_type="music_play_url",
            metadata={
                "url": url,
                "name": "常规测试",
                "artist": "N.E.K.O Tester"
            }
        )
        return Ok({"status": "sent"})
