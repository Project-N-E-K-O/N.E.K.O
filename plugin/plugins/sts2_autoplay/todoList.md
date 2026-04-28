已补充实时战况汇报：自动游玩循环会输出结构化 report，并新增 live_commentary 元数据供前端/TTS 播报。

猫娘解说能力：
- 根据血量、敌人攻击、斩杀、防守、奖励、商店/休息点/事件/地图等场景生成短口播。
- 低血量、斩杀、高伤害等关键局势提高优先级，可建议 TTS 打断。
- 普通局势按概率和冷却节流，避免每步刷屏。
- metadata.live_commentary 提供 text/mood/urgency/priority/tts/interrupt/scene 等字段。