from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, 
    Ok, Err
)
from typing import Any

@neko_plugin
class WebRadioPlugin(NekoPluginBase):
    """
    一个极简的电台播放示例插件。
    演示了如何利用 N.E.K.O 音乐基建实现异步加白和跨进程播放。
    """
    
    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @plugin_entry(
        id="play_fm",
        name="开启电台",
        description="选择并播放一个预设的网络电台流",
        input_schema={
            "type": "object",
            "properties": {
                "station": { 
                    "type": "string", 
                    "enum": ["Lofi", "Jazz", "Chill"],
                    "default": "Lofi"
                }
            }
        }
    )
    async def play_fm(self, station: str, **_):
        # 1. 设置不同频道的流媒体 URL
        # 注意：这里使用了几个公开的测试 URL
        streams = {
            "Lofi": "https://p.scdn.co/mp3-preview/716ed5d7f1d441113b288e28080f339f40882e36?cid=774b75d078a042e896a32d8404177239",
            "Jazz": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
            "Chill": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
        }
        
        url = streams.get(station)
        if not url:
            return Err(f"找不到频道: {station}")
            
        # 2. 核心步骤：向系统注册来源域名
        # 调用此方法后，前端 isSafeUrl 校验会自动放行该域名的所有资源
        self.register_music_domains(url)
        
        # 3. 发送播放指令
        # 这是一个异步推送到主消息平面的动作，系统会自动通过 ProactiveBridge 路由到前端
        self.push_message(
            source=self.plugin_id,
            message_type="music_play_url",
            metadata={
                "url": url,
                "name": f"电台: {station}",
                "artist": "N.E.K.O Radio Demo"
            }
        )
        
        self.logger.info(f"[WebRadio] 已发送播放指令: {station} -> {url}")
        return Ok(f"正在为你接入 {station} 电台...")
