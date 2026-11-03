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


# Local Whisper has only one "zh" language code and usually outputs simplified Chinese even in traditional-Chinese sessions.
# Pass this sentence as initial_prompt for zh-TW / zh-HK / zh-MO / zh-Hant sessions,
# encouraging traditional character forms. During silence the model sometimes repeats it verbatim; discard results that exactly match it.
WHISPER_TRADITIONAL_CHINESE_INITIAL_PROMPT = "以下是繁體中文的句子。"


# Common fixed hallucination phrases from local Whisper on silence/noise (originating in subtitle training data).
# Store normalized full sentences: lowercase, no leading/trailing punctuation or whitespace, and internal whitespace collapsed to single spaces.
# Discard only when the entire sentence exactly matches an entry and model confidence is also low;
# genuine user speech containing these words will not be falsely rejected.
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
