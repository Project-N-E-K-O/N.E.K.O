# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Localized text for voice preview and local speech recognition."""

VOICE_PREVIEW_TEXTS = {
    "zh-CN": "喵喵喵～这里是林悠怡～很高兴见到你～",
    "zh-TW": "喵喵喵～這裡是林悠怡～很高興見到你～",
    "en": "Meow, meow, meow~ This is Lin Youyi. It's nice to meet you~",
    "ja": "にゃんにゃんにゃん〜 林悠怡（リン・ユーイー）です。お会いできてうれしいです〜",
    "ko": "냥냥냥~ 저는 린유이예요. 만나서 반가워요~",
    "ru": "Мяу-мяу-мяу~ Это Линь Юуи. Очень рада знакомству~",
    "es": "Miau, miau, miau~ Soy Lin Youyi. Me alegra conocerte~",
    "pt": "Miau, miau, miau~ Eu sou Lin Youyi. Prazer em conhecer você~",
}


# 本地 Whisper 只有一个 "zh" 语言码，繁体会话默认也多半输出简体。
# 会话语言是 zh-TW / zh-HK / zh-MO / zh-Hant 时把这句作为 initial_prompt 传入，
# 引导模型沿用繁体字形。静音时模型偶尔会原样复述这句，识别结果与它完全相同时会被丢弃。
WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT = "以下是繁體中文的句子。"


# 本地 Whisper 在静音/噪声片段上常见的固定幻觉句（来自字幕训练数据）。
# 这里存的是归一化后的整句：小写、首尾标点与空白已去掉、内部空白压成单个空格。
# 只有整句完全等于其中一项、且模型置信度同时偏低时才会被丢弃，
# 用户真的说出这些话时不会被误杀。
WHISPER_SILENCE_HALLUCINATIONS = frozenset({
    # en
    "thank you",
    "thank you very much",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "you",
    # zh-CN / zh-TW
    "谢谢观看",
    "謝謝觀看",
    "谢谢大家",
    "謝謝大家",
    "字幕由amara.org社区提供",
    "字幕由amara.org社群提供",
    "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目",
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    # ja
    "ご視聴ありがとうございました",
    "ご清聴ありがとうございました",
    # ko
    "시청해주셔서 감사합니다",
    "시청해 주셔서 감사합니다",
    # ru
    "продолжение следует",
    "субтитры сделал dimatorzok",
    "субтитры создавал dimatorzok",
    # es
    "gracias por ver el video",
    "subtítulos realizados por la comunidad de amara.org",
    # pt
    "obrigado por assistir",
    "legendas pela comunidade amara.org",
})
