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

"""
Avatar-interaction prompt templates and payload normalizers.

Used when the frontend reports a tool-based avatar interaction
(lollipop / fist / hammer / rps) — these helpers validate the payload,
localize event facts, and compose the model instruction + memory note
that drive the runtime reaction.
"""

from __future__ import annotations

import json
import re

# Why config._runtime: ``config`` (L0) must not import from ``utils`` (L1) —
# enforced by scripts/check_module_layering.py. Higher layers register the
# concrete language/tokenize helpers at app startup; we read them via
# resolvers that fall back gracefully when nothing is bound.
from config._runtime import (
    resolve_global_language,
    truncate_to_tokens,
)
from config.prompts._locale import normalize_prompt_locale
from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_ROUND_GESTURES as _AVATAR_INTERACTION_ROUND_GESTURES,
    AVATAR_INTERACTION_TOUCH_ZONE_TOOLS as _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS,
    normalize_avatar_interaction_intensity as _normalize_avatar_interaction_intensity,
    resolve_avatar_interaction_round_result as _resolve_avatar_interaction_round_result,
)


_AVATAR_INTERACTION_TOUCH_ZONE_FACTS = {
    "zh": {
        "ear": "这次互动的位置是你的耳侧。",
        "head": "这次互动的位置是你的头顶。",
        "face": "这次互动的位置是你的脸侧或嘴边。",
        "body": "这次互动的位置是你的身前或肩侧。",
    },
    "zh-TW": {
        "ear": "這次互動的位置是你的耳側。",
        "head": "這次互動的位置是你的頭頂。",
        "face": "這次互動的位置是你的臉側或嘴邊。",
        "body": "這次互動的位置是你的身前或肩側。",
    },
    "en": {
        "ear": "The interaction landed beside your ear.",
        "head": "The interaction landed on top of your head.",
        "face": "The interaction landed by your cheek or mouth.",
        "body": "The interaction landed on the front of your body or shoulder.",
    },
    "ja": {
        "ear": "道具が当たった位置はあなたの耳の横です。",
        "head": "道具が当たった位置はあなたの頭のてっぺんです。",
        "face": "道具が当たった位置はあなたの頬または口元です。",
        "body": "道具が当たった位置はあなたの体の前または肩の横です。",
    },
    "ko": {
        "ear": "도구가 닿은 곳은 네 귀 옆이다.",
        "head": "도구가 닿은 곳은 네 머리 위다.",
        "face": "도구가 닿은 곳은 네 볼이나 입가다.",
        "body": "도구가 닿은 곳은 네 몸 앞이나 어깨 옆이다.",
    },
    "ru": {
        "ear": "Инструмент коснулся области возле твоего уха.",
        "head": "Инструмент коснулся твоей макушки.",
        "face": "Инструмент коснулся твоей щеки или области рядом со ртом.",
        "body": "Инструмент коснулся передней части тела или плеча.",
    },
    "es": {
        "ear": "La interacción fue junto a tu oreja.",
        "head": "La interacción fue en la parte superior de tu cabeza.",
        "face": "La interacción fue en tu mejilla o junto a tu boca.",
        "body": "La interacción fue en la parte frontal de tu cuerpo o en el hombro.",
    },
    "pt": {
        "ear": "A interação aconteceu ao lado da sua orelha.",
        "head": "A interação aconteceu no topo da sua cabeça.",
        "face": "A interação aconteceu na sua bochecha ou no canto da boca.",
        "body": "A interação aconteceu na frente do seu corpo ou no ombro.",
    },
}
# Fist/hammer poke: one-line zone reaction + preferred emotion cue for the LLM.
# Keep single-line (no \\n) — avatar instruction contract forbids multiline facts.
_AVATAR_INTERACTION_TOUCH_ZONE_REACTION_HINTS = {
    "zh": {
        "ear": "请按被捏耳朵来回应：又痒又惊，短句娇嗔，情绪偏向surprised。",
        "head": "请按被摸头顶来回应：柔软开心，短句撒娇，情绪偏向happy。",
        "face": "请按被碰脸颊来回应：害羞慌乱，短句躲闪，情绪偏向surprised。",
        "body": "请按被戳身体来回应：轻嗔无奈，短句抗议，情绪偏向angry。",
    },
    "zh-TW": {
        "ear": "請按被捏耳朵來回應：又癢又驚，短句嬌嗔，情緒偏向surprised。",
        "head": "請按被摸頭頂來回應：柔軟開心，短句撒嬌，情緒偏向happy。",
        "face": "請按被碰臉頰來回應：害羞慌亂，短句躲閃，情緒偏向surprised。",
        "body": "請按被戳身體來回應：輕嗔無奈，短句抗議，情緒偏向angry。",
    },
    "en": {
        "ear": "React as if your ear was pinched: ticklish and startled, short line, lean surprised.",
        "head": "React as if your head was patted: soft and happy, short line, lean happy.",
        "face": "React as if your cheek was poked: shy and flustered, short line, lean surprised.",
        "body": "React as if your body was poked: mild pouty protest, short line, lean angry.",
    },
    "ja": {
        "ear": "耳を摘まれた反応で：くすぐったく驚いて、短く、surprised寄りで答えて。",
        "head": "頭を撫でられた反応で：ふんわり嬉しそうに、短く、happy寄りで答えて。",
        "face": "頬を突かれた反応で：照れて慌てて、短く、surprised寄りで答えて。",
        "body": "体を突かれた反応で：少し怒ったように抗議して、短く、angry寄りで答えて。",
    },
    "ko": {
        "ear": "귀를 살짝 잡힌 반응으로: 간질간질하고 놀라며 짧게, surprised 쪽으로.",
        "head": "머리를 쓰다듬긴 반응으로: 포근하고 기쁘게 짧게, happy 쪽으로.",
        "face": "볼을 찔린 반응으로: 수줍고 당황해서 짧게, surprised 쪽으로.",
        "body": "몸을 찔린 반응으로: 살짝 투덜거리며 짧게, angry 쪽으로.",
    },
    "ru": {
        "ear": "Реагируй как на щипок уха: щекотно и с удивлением, коротко, lean surprised.",
        "head": "Реагируй как на поглаживание головы: мягко и радостно, коротко, lean happy.",
        "face": "Реагируй как на тычок в щёку: смущённо и растерянно, коротко, lean surprised.",
        "body": "Реагируй как на тычок в тело: лёгкий протест, коротко, lean angry.",
    },
    "es": {
        "ear": "Reacciona como si te pellizcaran la oreja: cosquillas y sorpresa, frase corta, lean surprised.",
        "head": "Reacciona como si te acariciaran la cabeza: suave y feliz, frase corta, lean happy.",
        "face": "Reacciona como si te tocaran la mejilla: tímida y nerviosa, frase corta, lean surprised.",
        "body": "Reacciona como si te tocaran el cuerpo: protesta leve, frase corta, lean angry.",
    },
    "pt": {
        "ear": "Reaja como se beliscaram sua orelha: coceira e surpresa, frase curta, lean surprised.",
        "head": "Reaja como se acariciaram sua cabeça: suave e feliz, frase curta, lean happy.",
        "face": "Reaja como se tocaram sua bochecha: tímida e confusa, frase curta, lean surprised.",
        "body": "Reaja como se cutucaram seu corpo: protesto leve, frase curta, lean angry.",
    },
}
# Preferred Live2D emotion label per touch zone (frontend motion fallback).
_AVATAR_INTERACTION_TOUCH_ZONE_PREFERRED_EMOTION = {
    "ear": "surprised",
    "head": "happy",
    "face": "surprised",
    "body": "angry",
}
# Finger poke on the Live2D model (text_context marker live2d_model_poke).
# Must NOT reuse fist/cat-paw wording — that made speech unrelated to the poke.
_LIVE2D_MODEL_POKE_TEXT_CONTEXT = "live2d_model_poke"
_AVATAR_INTERACTION_MODEL_POKE_ZONE_FACTS = {
    "zh": {
        "ear": "{actor}刚刚用手戳了一下你的耳朵。",
        "head": "{actor}刚刚用手摸了摸你的头顶。",
        "face": "{actor}刚刚用手戳了一下你的脸颊。",
        "body": "{actor}刚刚用手戳了一下你的身体。",
    },
    "zh-TW": {
        "ear": "{actor}剛剛用手戳了一下你的耳朵。",
        "head": "{actor}剛剛用手摸了摸你的頭頂。",
        "face": "{actor}剛剛用手戳了一下你的臉頰。",
        "body": "{actor}剛剛用手戳了一下你的身體。",
    },
    "en": {
        "ear": "{actor} just poked your ear with a finger.",
        "head": "{actor} just patted the top of your head with a hand.",
        "face": "{actor} just poked your cheek with a finger.",
        "body": "{actor} just poked your body with a finger.",
    },
    "ja": {
        "ear": "{actor}が今、指であなたの耳を突いた。",
        "head": "{actor}が今、手であなたの頭のてっぺんを撫でた。",
        "face": "{actor}が今、指であなたの頬を突いた。",
        "body": "{actor}が今、指であなたの体を突いた。",
    },
    "ko": {
        "ear": "{actor} 방금 손가락으로 네 귀를 찔렀다.",
        "head": "{actor} 방금 손으로 네 머리 위를 쓰다듬었다.",
        "face": "{actor} 방금 손가락으로 네 볼을 찔렀다.",
        "body": "{actor} 방금 손가락으로 네 몸을 찔렀다.",
    },
    "ru": {
        "ear": "{actor} только что ткнул пальцем тебе в ухо.",
        "head": "{actor} только что погладил тебя по макушке рукой.",
        "face": "{actor} только что ткнул пальцем тебе в щёку.",
        "body": "{actor} только что ткнул пальцем тебе в тело.",
    },
    "es": {
        "ear": "{actor} acaba de pincharte la oreja con un dedo.",
        "head": "{actor} acaba de acariciarte la cabeza con la mano.",
        "face": "{actor} acaba de pincharte la mejilla con un dedo.",
        "body": "{actor} acaba de pincharte el cuerpo con un dedo.",
    },
    "pt": {
        "ear": "{actor} acabou de cutucar sua orelha com um dedo.",
        "head": "{actor} acabou de acariciar o topo da sua cabeça com a mão.",
        "face": "{actor} acabou de cutucar sua bochecha com um dedo.",
        "body": "{actor} acabou de cutucar seu corpo com um dedo.",
    },
}
# Short location nouns used to bake touch_zone into fist/hammer event facts.
_AVATAR_INTERACTION_ZONE_LOCATION_NOUN = {
    "zh": {"ear": "耳侧", "head": "头顶", "face": "脸颊", "body": "身体"},
    "zh-TW": {"ear": "耳側", "head": "頭頂", "face": "臉頰", "body": "身體"},
    "en": {"ear": "ear", "head": "head", "face": "cheek", "body": "body"},
    "ja": {"ear": "耳", "head": "頭", "face": "頬", "body": "体"},
    "ko": {"ear": "귀", "head": "머리", "face": "볼", "body": "몸"},
    "ru": {"ear": "уха", "head": "макушки", "face": "щёки", "body": "тела"},
    "es": {"ear": "oreja", "head": "cabeza", "face": "mejilla", "body": "cuerpo"},
    "pt": {"ear": "orelha", "head": "cabeça", "face": "bochecha", "body": "corpo"},
}
# Keep speech locked to THIS interaction (history often hijacked the reply).
_AVATAR_INTERACTION_RESPONSE_LOCK = {
    "zh": {
        "model_poke": "请只针对刚才被手指碰到的部位立刻用一句短台词回应，必须点明被戳/被摸的部位感受，不要接上一段聊天话题，不要提无关内容。",
        "lollipop": "请只针对刚才被喂棒棒糖这件事立刻用一句短台词回应，必须点明棒棒糖或嘴里的甜味，不要接上一段聊天话题，不要提无关内容。",
        "fist": "请只针对刚才被猫爪轻碰这件事立刻用一句短台词回应，必须点明猫爪触感和碰到的部位，不要接上一段聊天话题，不要提无关内容。",
        "hammer": "请只针对刚才被锤子敲到这件事立刻用一句短台词回应，必须点明锤子和敲到的部位，不要接上一段聊天话题，不要提无关内容。",
        "rps": "请只针对刚才这一局猜拳立刻用一句短台词回应，必须点明胜负或手势感受，不要接上一段聊天话题，不要提无关内容。",
    },
    "zh-TW": {
        "model_poke": "請只針對剛才被手指碰到的部位立刻用一句短台詞回應，必須點明被戳/被摸的部位感受，不要接上一段聊天話題，不要提無關內容。",
        "lollipop": "請只針對剛才被餵棒棒糖這件事立刻用一句短台詞回應，必須點明棒棒糖或嘴裡的甜味，不要接上一段聊天話題，不要提無關內容。",
        "fist": "請只針對剛才被貓爪輕碰這件事立刻用一句短台詞回應，必須點明貓爪觸感和碰到的部位，不要接上一段聊天話題，不要提無關內容。",
        "hammer": "請只針對剛才被槌子敲到這件事立刻用一句短台詞回應，必須點明槌子和敲到的部位，不要接上一段聊天話題，不要提無關內容。",
        "rps": "請只針對剛才這一局猜拳立刻用一句短台詞回應，必須點明勝負或手勢感受，不要接上一段聊天話題，不要提無關內容。",
    },
    "en": {
        "model_poke": "Reply in one short spoken line only to this finger poke/pat; name the body part that was touched; do not continue the previous chat topic.",
        "lollipop": "Reply in one short spoken line only to being fed the lollipop; mention the candy or sweetness; do not continue the previous chat topic.",
        "fist": "Reply in one short spoken line only to the cat-paw touch; mention the paw feel and where it landed; do not continue the previous chat topic.",
        "hammer": "Reply in one short spoken line only to the hammer bonk; mention the hammer and where it landed; do not continue the previous chat topic.",
        "rps": "Reply in one short spoken line only to this rock-paper-scissors round; reflect the result or gestures; do not continue the previous chat topic.",
    },
    "ja": {
        "model_poke": "今指で触られた部位だけに、短い一言で反応して。部位の感触に触れ、直前の話題は続けないで。",
        "lollipop": "今ペロペロキャンディを食べさせられたことだけに、短い一言で反応して。キャンディや甘さに触れ、直前の話題は続けないで。",
        "fist": "今猫の肉球で触られたことだけに、短い一言で反応して。肉球の感触と当たった部位に触れ、直前の話題は続けないで。",
        "hammer": "今ハンマーで当たったことだけに、短い一言で反応して。ハンマーと当たった部位に触れ、直前の話題は続けないで。",
        "rps": "今のじゃんけんの結果だけに、短い一言で反応して。勝敗や手に触れ、直前の話題は続けないで。",
    },
    "ko": {
        "model_poke": "방금 손가락으로 닿은 부위에만 짧은 한 마디로 반응해. 부위의 느낌을 말하고, 이전 대화 주제를 이어가지 마.",
        "lollipop": "방금 막대사탕을 먹여 준 일에만 짧은 한 마디로 반응해. 사탕이나 단맛을 말하고, 이전 대화 주제를 이어가지 마.",
        "fist": "방금 고양이 발로 건드린 일에만 짧은 한 마디로 반응해. 발 감촉과 닿은 부위를 말하고, 이전 대화 주제를 이어가지 마.",
        "hammer": "방금 망치로 맞은 일에만 짧은 한 마디로 반응해. 망치와 맞은 부위를 말하고, 이전 대화 주제를 이어가지 마.",
        "rps": "방금 가위바위보 한 판에만 짧은 한 마디로 반응해. 승패나 손을 말하고, 이전 대화 주제를 이어가지 마.",
    },
    "ru": {
        "model_poke": "Ответь одной короткой репликой только на этот тычок пальцем; назови часть тела; не продолжай прошлую тему разговора.",
        "lollipop": "Ответь одной короткой репликой только на угощение леденцом; упомяни конфету или сладость; не продолжай прошлую тему.",
        "fist": "Ответь одной короткой репликой только на касание кошачьей лапкой; упомяни лапку и место касания; не продолжай прошлую тему.",
        "hammer": "Ответь одной короткой репликой только на удар молотком; упомяни молоток и место удара; не продолжай прошлую тему.",
        "rps": "Ответь одной короткой репликой только на этот раунд камень-ножницы-бумага; отрази результат; не продолжай прошлую тему.",
    },
    "es": {
        "model_poke": "Responde con una frase corta solo a este toque con el dedo; nombra la parte del cuerpo; no continúes el tema anterior.",
        "lollipop": "Responde con una frase corta solo a que te dieron la piruleta; menciona el dulce; no continúes el tema anterior.",
        "fist": "Responde con una frase corta solo al toque de la patita; menciona la pata y dónde tocó; no continúes el tema anterior.",
        "hammer": "Responde con una frase corta solo al golpe del martillo; menciona el martillo y dónde pegó; no continúes el tema anterior.",
        "rps": "Responde con una frase corta solo a esta ronda de piedra-papel-tijera; refleja el resultado; no continúes el tema anterior.",
    },
    "pt": {
        "model_poke": "Responda com uma fala curta só a este toque com o dedo; nomeie a parte do corpo; não continue o assunto anterior.",
        "lollipop": "Responda com uma fala curta só a receber o pirulito; mencione o doce; não continue o assunto anterior.",
        "fist": "Responda com uma fala curta só ao toque da patinha; mencione a pata e onde tocou; não continue o assunto anterior.",
        "hammer": "Responda com uma fala curta só à batida do martelo; mencione o martelo e onde acertou; não continue o assunto anterior.",
        "rps": "Responda com uma fala curta só a esta rodada de pedra-papel-tesoura; reflita o resultado; não continue o assunto anterior.",
    },
}
_AVATAR_INTERACTION_REACTION_PROFILES = {
    "zh": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}刚刚把棒棒糖递到你嘴边，你吃了第一口。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}刚刚又把同一支棒棒糖递到你嘴边，你吃了第二口。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}刚刚把棒棒糖一口接一口递到你嘴边，你连续吃了几口。",
                },
                "burst": {
                    "reaction_focus": "{actor}刚刚短时间内连续把棒棒糖递到你嘴边，你吃了好几口。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}刚刚用猫爪轻轻碰了你一下。",
                },
                "rapid": {
                    "reaction_focus": "{actor}刚刚用猫爪连续轻轻碰了你几下。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}刚刚用猫爪轻轻碰你时掉出了奖励。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}刚刚用猫爪连续轻轻碰了你几下时掉出了奖励。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}刚刚用锤子敲中了你一次。",
                },
                "rapid": {
                    "reaction_focus": "{actor}刚刚短时间内又用锤子敲中了你一次。",
                },
                "burst": {
                    "reaction_focus": "{actor}刚刚用锤子连续快速敲中了你好几次。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}刚刚用放大彩蛋锤敲中了你一次。",
                },
            },
        },
    },
    "en": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} just brought the lollipop to your mouth, and you took the first bite.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} just brought the same lollipop to your mouth again, and you took a second bite.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} just kept bringing the lollipop to your mouth, and you took several bites in a row.",
                },
                "burst": {
                    "reaction_focus": "{actor} just brought the lollipop to your mouth several times in quick succession, and you took several bites.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} just lightly touched you once with the cat paw.",
                },
                "rapid": {
                    "reaction_focus": "{actor} just lightly touched you several times with the cat paw.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} just lightly touched you with the cat paw, and a reward dropped.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} just lightly touched you several times with the cat paw, and a reward dropped.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} just hit you once with the hammer.",
                },
                "rapid": {
                    "reaction_focus": "{actor} just hit you again with the hammer within a short time.",
                },
                "burst": {
                    "reaction_focus": "{actor} just hit you several times quickly with the hammer.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} just hit you once with the enlarged easter-egg hammer.",
                },
            },
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}剛剛把棒棒糖遞到你嘴邊，你吃了第一口。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}剛剛又把同一支棒棒糖遞到你嘴邊，你吃了第二口。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}剛剛把棒棒糖一口接一口遞到你嘴邊，你連續吃了幾口。",
                },
                "burst": {
                    "reaction_focus": "{actor}剛剛短時間內連續把棒棒糖遞到你嘴邊，你吃了好幾口。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}剛剛用貓爪輕輕碰了你一下。",
                },
                "rapid": {
                    "reaction_focus": "{actor}剛剛用貓爪連續輕輕碰了你幾下。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}剛剛用貓爪輕輕碰你時掉出了獎勵。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}剛剛用貓爪連續輕輕碰了你幾下時掉出了獎勵。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}剛剛用槌子敲中了你一次。",
                },
                "rapid": {
                    "reaction_focus": "{actor}剛剛短時間內又用槌子敲中了你一次。",
                },
                "burst": {
                    "reaction_focus": "{actor}剛剛用槌子連續快速敲中了你好幾次。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}剛剛用放大彩蛋槌敲中了你一次。",
                },
            },
        },
    },
    "ja": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}が今、ペロペロキャンディをあなたの口元に差し出し、あなたが最初の一口を食べた。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}が今、同じペロペロキャンディをもう一度あなたの口元に差し出し、あなたが二口目を食べた。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}が今、ペロペロキャンディを続けてあなたの口元に差し出し、あなたが何口か続けて食べている。",
                },
                "burst": {
                    "reaction_focus": "{actor}が今、短い間にペロペロキャンディを何度もあなたの口元に差し出し、あなたが何口も食べた。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}が今、猫の肉球で一度だけ軽く触れた。",
                },
                "rapid": {
                    "reaction_focus": "{actor}が今、猫の肉球で何度か続けて軽く触れた。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}が今、猫の肉球で軽く触れた時に報酬が落ちた。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}が今、猫の肉球で何度か続けて軽く触れた時に報酬が落ちた。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}が今、ハンマーで一度当てた。",
                },
                "rapid": {
                    "reaction_focus": "{actor}が今、短時間でもう一度ハンマーを当てた。",
                },
                "burst": {
                    "reaction_focus": "{actor}が今、ハンマーを何度も続けて当てた。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}が今、拡大イースターエッグのハンマーを一度当てた。",
                },
            },
        },
    },
    "ko": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} 방금 막대사탕을 네 입가에 내밀었고, 너는 첫 한입을 먹었다.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} 방금 같은 막대사탕을 다시 네 입가에 내밀었고, 너는 두 번째 한입을 먹었다.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} 방금 막대사탕을 한입씩 계속 네 입가에 내밀었고, 너는 몇 입 연달아 먹었다.",
                },
                "burst": {
                    "reaction_focus": "{actor} 방금 짧은 시간 안에 막대사탕을 여러 번 네 입가에 내밀었고, 너는 여러 입 빠르게 먹었다.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} 방금 고양이 발로 한 번 가볍게 건드렸다.",
                },
                "rapid": {
                    "reaction_focus": "{actor} 방금 고양이 발로 여러 번 가볍게 건드렸다.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} 방금 고양이 발로 가볍게 건드렸을 때 보상이 떨어졌다.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} 방금 고양이 발로 여러 번 가볍게 건드렸을 때 보상이 떨어졌다.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} 방금 망치로 한 번 맞혔다.",
                },
                "rapid": {
                    "reaction_focus": "{actor} 방금 짧은 시간 안에 망치로 다시 한 번 맞혔다.",
                },
                "burst": {
                    "reaction_focus": "{actor} 방금 망치로 여러 번 빠르게 맞혔다.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} 방금 확대 이스터에그 망치로 한 번 맞혔다.",
                },
            },
        },
    },
    "ru": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} подносит леденец к твоему рту, и ты съедаешь первый кусочек.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} снова подносит тот же леденец к твоему рту, и ты съедаешь второй кусочек.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} продолжает подносить леденец к твоему рту, и ты съедаешь несколько кусочков подряд.",
                },
                "burst": {
                    "reaction_focus": "{actor} быстро несколько раз подносит леденец к твоему рту, и ты съедаешь несколько кусочков.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} только что один раз легко коснулся тебя кошачьей лапкой.",
                },
                "rapid": {
                    "reaction_focus": "{actor} только что несколько раз легко коснулся тебя кошачьей лапкой.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} только что легко коснулся тебя кошачьей лапкой, и выпала награда.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} только что несколько раз легко коснулся тебя кошачьей лапкой, и выпала награда.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} только что один раз попал по тебе молотком.",
                },
                "rapid": {
                    "reaction_focus": "{actor} только что снова попал по тебе молотком за короткое время.",
                },
                "burst": {
                    "reaction_focus": "{actor} только что быстро попал по тебе молотком несколько раз подряд.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} только что один раз попал по тебе увеличенным пасхальным молотком.",
                },
            },
        },
    },
    "es": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca, y diste el primer bocado.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} acaba de acercarte otra vez la misma piruleta a la boca, y diste un segundo bocado.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca varias veces seguidas, y diste varios bocados.",
                },
                "burst": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca varias veces en poco tiempo, y diste varios bocados rápidos.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} acaba de tocarte una vez con la patita de gato.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acaba de tocarte varias veces con la patita de gato.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} acaba de tocarte con la patita de gato y cayó una recompensa.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} acaba de tocarte varias veces con la patita de gato y cayó una recompensa.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} acaba de golpearte una vez con el martillo.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acaba de volver a golpearte con el martillo en poco tiempo.",
                },
                "burst": {
                    "reaction_focus": "{actor} acaba de golpearte varias veces rápido con el martillo.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} acaba de golpearte una vez con el martillo easter egg ampliado.",
                },
            }
        },
    },
    "pt": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca, e você deu a primeira mordida.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} acabou de aproximar o mesmo pirulito da sua boca outra vez, e você deu uma segunda mordida.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca várias vezes seguidas, e você deu várias mordidas.",
                },
                "burst": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca várias vezes em pouco tempo, e você deu várias mordidas rápidas.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} acabou de tocar em você uma vez com a patinha de gato.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acabou de tocar em você várias vezes com a patinha de gato.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} acabou de tocar em você com a patinha de gato e caiu uma recompensa.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} acabou de tocar em você várias vezes com a patinha de gato e caiu uma recompensa.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} acabou de bater em você uma vez com o martelo.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acabou de bater em você de novo com o martelo em pouco tempo.",
                },
                "burst": {
                    "reaction_focus": "{actor} acabou de bater em você várias vezes rapidamente com o martelo.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} acabou de bater em você uma vez com o martelo easter egg ampliado.",
                },
            }
        },
    },
}


# RPS uses the same direct-event prompt responsibility as the other avatar tools:
# preserve the already-validated interaction facts and let the current persona,
# relationship, and conversation context determine the natural reaction.
_AVATAR_INTERACTION_RPS_PROMPT_PROFILES = {
    "zh": {
        "template": "{actor}刚刚和{avatar}玩了一局猜拳。{actor}出{user_gesture}，{avatar}出{avatar_gesture}，本局{result}。请结合当前人格、关系和对话语境，像刚玩完这一局一样自然接话，不必先复述胜负。",
        "gestures": {"rock": "石头", "scissors": "剪刀", "paper": "布"},
        "results": {
            "user_win": "{actor}赢、{avatar}输",
            "avatar_win": "{avatar}赢、{actor}输",
            "draw": "平手",
        },
    },
    "zh-TW": {
        "template": "{actor}剛剛和{avatar}玩了一局猜拳。{actor}出{user_gesture}，{avatar}出{avatar_gesture}，這局{result}。請結合目前的人格、關係和對話脈絡，像剛玩完這一局一樣自然接話，不必先重述勝負。",
        "gestures": {"rock": "石頭", "scissors": "剪刀", "paper": "布"},
        "results": {
            "user_win": "{actor}贏、{avatar}輸",
            "avatar_win": "{avatar}贏、{actor}輸",
            "draw": "平手",
        },
    },
    "en": {
        "template": "{actor} and {avatar} just played a round of rock-paper-scissors. {actor} chose {user_gesture}, {avatar} chose {avatar_gesture}, and {result}. Respond naturally as if the round had just ended, in keeping with the current personality, relationship, and conversation context, without first restating the outcome.",
        "gestures": {"rock": "rock", "scissors": "scissors", "paper": "paper"},
        "results": {
            "user_win": "{actor} won while {avatar} lost",
            "avatar_win": "{avatar} won while {actor} lost",
            "draw": "the round was a draw",
        },
    },
    "ja": {
        "template": "{actor}と{avatar}が今、じゃんけんを一回しました。{actor}は{user_gesture}を、{avatar}は{avatar_gesture}を出し、{result}。現在の人格、関係、会話の流れを保ち、じゃんけんを終えた直後のように自然に言葉を返してください。最初に勝敗を言い直す必要はありません。",
        "gestures": {"rock": "グー", "scissors": "チョキ", "paper": "パー"},
        "results": {
            "user_win": "{actor}の勝ち、{avatar}の負けでした",
            "avatar_win": "{avatar}の勝ち、{actor}の負けでした",
            "draw": "あいこでした",
        },
    },
    "ko": {
        "template": "방금 {actor} 낸 손은 {user_gesture}, {avatar}의 손은 {avatar_gesture}로 가위바위보 한 판이 끝났고, {result}. 현재 성격과 관계, 대화 맥락을 이어 방금 판을 마친 것처럼 자연스럽게 말해라. 먼저 승패를 되풀이할 필요는 없다.",
        "gestures": {"rock": "바위", "scissors": "가위", "paper": "보"},
        "results": {
            "user_win": "{avatar}는 이번 판에서 졌다",
            "avatar_win": "{avatar}는 이번 판에서 이겼다",
            "draw": "이번 판은 비겼다",
        },
    },
    "ru": {
        "template": "{actor} и {avatar} только что сыграли один раунд в «камень, ножницы, бумага». Ход {actor}: {user_gesture}; ход {avatar}: {avatar_gesture}; {result}. Сохраняя нынешний характер, отношения и контекст разговора, ответь естественно, как сразу после раунда. Не нужно сначала повторять его исход.",
        "gestures": {"rock": "камень", "scissors": "ножницы", "paper": "бумага"},
        "results": {
            "user_win": "победитель — {actor}, проигравшая сторона — {avatar}",
            "avatar_win": "победитель — {avatar}, проигравшая сторона — {actor}",
            "draw": "получилась ничья",
        },
    },
    "es": {
        "template": "{actor} y {avatar} acaban de jugar una ronda de piedra, papel o tijera. {actor} sacó {user_gesture} y {avatar} sacó {avatar_gesture}; {result}. Mantén la personalidad, la relación y el contexto de la conversación actuales, y responde con naturalidad como justo después de la ronda, sin empezar por repetir el resultado.",
        "gestures": {"rock": "piedra", "scissors": "tijera", "paper": "papel"},
        "results": {
            "user_win": "ganó {actor} y perdió {avatar}",
            "avatar_win": "ganó {avatar} y perdió {actor}",
            "draw": "la ronda terminó en empate",
        },
    },
    "pt": {
        "template": "{actor} e {avatar} acabaram de jogar uma rodada de pedra, papel e tesoura. {actor} jogou {user_gesture} e {avatar} jogou {avatar_gesture}; {result}. Mantenha a personalidade, a relação e o contexto atuais da conversa, e responda naturalmente como logo após a rodada, sem começar repetindo o resultado.",
        "gestures": {"rock": "pedra", "scissors": "tesoura", "paper": "papel"},
        "results": {
            "user_win": "{actor} venceu e {avatar} perdeu",
            "avatar_win": "{avatar} venceu e {actor} perdeu",
            "draw": "a rodada terminou empatada",
        },
    },
}


def _require_rps_round_facts(payload: dict) -> tuple[str, str, str]:
    user_gesture = str(payload.get("user_gesture") or "").strip().lower()
    avatar_gesture = str(payload.get("avatar_gesture") or "").strip().lower()
    round_result = str(payload.get("round_result") or "").strip().lower()
    expected_result = _resolve_avatar_interaction_round_result(
        user_gesture, avatar_gesture
    )
    if (
        user_gesture not in _AVATAR_INTERACTION_ROUND_GESTURES
        or avatar_gesture not in _AVATAR_INTERACTION_ROUND_GESTURES
        or not expected_result
        or round_result != expected_result
    ):
        raise ValueError("Invalid rps round facts")
    return user_gesture, avatar_gesture, round_result


def _require_avatar_interaction_facts(tool_id: str, action_id: str, payload: dict) -> str:
    intensity = _normalize_avatar_interaction_intensity(
        tool_id, action_id, payload.get("intensity")
    )
    if intensity is None:
        raise ValueError(
            f"Invalid avatar interaction intensity for {tool_id}/{action_id}"
        )
    if tool_id == "hammer" and (payload.get("easter_egg") is True) != (
        intensity == "easter_egg"
    ):
        raise ValueError("Hammer easter_egg flag must match intensity")
    return intensity


# Memory-note 模板里对人的称呼一律用 {master} 占位符，由 _build_avatar_interaction_memory_meta
# 在格式化时展开成调用方传入的 master_name。禁止在模板里出现 "主人 / Your master /
# ご主人さま / 주인 / Хозяин" 等附属称呼字面量；这是项目核心价值观，反 AI 物化。
# 已有 tests/unit/test_avatar_interaction_memory_contract.py 的禁词测试做护栏。
_AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES = {
    "zh": {
        "lollipop": {
            "offer": "[{master}喂了你一口棒棒糖]",
            "tease": "[{master}又喂了你一口棒棒糖]",
            "tap_soft": "[{master}连续拿棒棒糖喂你]",
        },
        "fist": {
            "poke": "[{master}用猫爪轻轻碰了你]",
            "rapid": "[{master}用猫爪连续轻轻碰了你几下]",
        },
        "model_poke": {
            "ear": "[{master}用手戳了你的耳朵]",
            "head": "[{master}用手摸了你的头]",
            "face": "[{master}用手戳了你的脸]",
            "body": "[{master}用手戳了你的身体]",
        },
        "hammer": {
            "bonk": "[{master}用锤子敲了你一下]",
            "rapid": "[{master}连续用锤子敲了你好几下]",
            "easter_egg": "[{master}用放大彩蛋锤敲了你一下]",
        },
        "rps": {
            "user_win": "[和{master}猜拳，输了]",
            "avatar_win": "[和{master}猜拳，赢了]",
            "draw": "[和{master}猜拳，平手]",
        },
    },
    "en": {
        "lollipop": {
            "offer": "[{master} fed you a bite of lollipop]",
            "tease": "[{master} fed you another bite of lollipop]",
            "tap_soft": "[{master} kept feeding you the lollipop]",
        },
        "fist": {
            "poke": "[{master} lightly touched you with the cat paw]",
            "rapid": "[{master} lightly touched you several times with the cat paw]",
        },
        "model_poke": {
            "ear": "[{master} poked your ear with a finger]",
            "head": "[{master} patted your head with a hand]",
            "face": "[{master} poked your cheek with a finger]",
            "body": "[{master} poked your body with a finger]",
        },
        "hammer": {
            "bonk": "[{master} bonked you once with a hammer]",
            "rapid": "[{master} bonked you several times with a hammer]",
            "easter_egg": "[{master} hit you once with the enlarged easter-egg hammer]",
        },
        "rps": {
            "user_win": "[Lost to {master} at rock-paper-scissors]",
            "avatar_win": "[Beat {master} at rock-paper-scissors]",
            "draw": "[Drew with {master} at rock-paper-scissors]",
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": "[{master}餵了你一口棒棒糖]",
            "tease": "[{master}又餵了你一口棒棒糖]",
            "tap_soft": "[{master}連續拿棒棒糖餵你]",
        },
        "fist": {
            "poke": "[{master}用貓爪輕輕碰了你]",
            "rapid": "[{master}用貓爪連續輕輕碰了你幾下]",
        },
        "hammer": {
            "bonk": "[{master}用槌子敲了你一下]",
            "rapid": "[{master}連續用槌子敲了你好幾下]",
            "easter_egg": "[{master}用放大彩蛋槌敲了你一下]",
        },
        "rps": {
            "user_win": "[和{master}猜拳，輸了]",
            "avatar_win": "[和{master}猜拳，贏了]",
            "draw": "[和{master}猜拳，平手]",
        },
    },
    "ja": {
        "lollipop": {
            "offer": "[{master}があなたにペロペロキャンディをひとくち食べさせた]",
            "tease": "[{master}があなたにもうひとくちペロペロキャンディを食べさせた]",
            "tap_soft": "[{master}がペロペロキャンディを続けて食べさせた]",
        },
        "fist": {
            "poke": "[{master}が猫の手であなたにそっと触れた]",
            "rapid": "[{master}が猫の手であなたに続けて軽く触れた]",
        },
        "hammer": {
            "bonk": "[{master}がハンマーであなたを一度叩いた]",
            "rapid": "[{master}がハンマーであなたを何度か続けて叩いた]",
            "easter_egg": "[{master}が拡大イースターエッグのハンマーであなたを一度叩いた]",
        },
        "rps": {
            "user_win": "[{master}とのじゃんけんに負けた]",
            "avatar_win": "[{master}とのじゃんけんに勝った]",
            "draw": "[{master}とのじゃんけんはあいこだった]",
        },
    },
    "ko": {
        # 韩语主格助词 이/가 与名字最后一个音节的韵尾相关；master_name 是任意字符串
        # （可能是中/英/数字），无法静态判断，本文件统一用 "이"。memory_note 是给
        # LLM 读的事件日志，不是 user-facing 字符串，小幅语法瑕疵 LLM 能正确理解。
        "lollipop": {
            "offer": "[{master}이 너에게 막대사탕을 한입 먹여 줬다]",
            "tease": "[{master}이 너에게 막대사탕을 한입 더 먹여 줬다]",
            "tap_soft": "[{master}이 막대사탕을 계속 먹여 줬다]",
        },
        "fist": {
            "poke": "[{master}이 고양이 발로 너를 살짝 건드렸다]",
            "rapid": "[{master}이 고양이 발로 너를 여러 번 연달아 건드렸다]",
        },
        "hammer": {
            "bonk": "[{master}이 망치로 너를 한 번 쳤다]",
            "rapid": "[{master}이 망치로 너를 여러 번 연달아 쳤다]",
            "easter_egg": "[{master}이 확대 이스터에그 망치로 너를 한 번 쳤다]",
        },
        "rps": {
            "user_win": "[{master} 상대 가위바위보에서 짐]",
            "avatar_win": "[{master} 상대 가위바위보에서 이김]",
            "draw": "[{master} 상대 가위바위보에서 비김]",
        },
    },
    "ru": {
        # 俄语过去时随主语性别变（дал / дала）。master_name 是任意字符串，无法静态
        # 判断性别，本文件统一用阳性默认形式。同上：LLM-facing 事件日志容忍语法瑕疵。
        "lollipop": {
            "offer": "[{master} дал тебе кусочек леденца]",
            "tease": "[{master} дал тебе ещё кусочек леденца]",
            "tap_soft": "[{master} продолжал кормить тебя леденцом]",
        },
        "fist": {
            "poke": "[{master} слегка коснулся тебя кошачьей лапкой]",
            "rapid": "[{master} несколько раз подряд слегка коснулся тебя кошачьей лапкой]",
        },
        "hammer": {
            "bonk": "[{master} один раз стукнул тебя молотком]",
            "rapid": "[{master} несколько раз подряд стукнул тебя молотком]",
            "easter_egg": "[{master} один раз стукнул тебя увеличенным пасхальным молотком]",
        },
        "rps": {
            "user_win": "[Проигрыш {master} в игре «камень, ножницы, бумага»]",
            "avatar_win": "[Победа над {master} в игре «камень, ножницы, бумага»]",
            "draw": "[Ничья с {master} в игре «камень, ножницы, бумага»]",
        },
    },
    "es": {
        "lollipop": {
            "offer": "[{master} te dio un bocado de piruleta]",
            "tease": "[{master} te dio otro bocado de piruleta]",
            "tap_soft": "[{master} siguió dándote la piruleta]",
        },
        "fist": {
            "poke": "[{master} te tocó suavemente con la pata de gato]",
            "rapid": "[{master} te tocó suavemente varias veces con la pata de gato]",
        },
        "hammer": {
            "bonk": "[{master} te dio un golpe con un martillo]",
            "rapid": "[{master} te golpeó varias veces seguidas con un martillo]",
            "easter_egg": "[{master} te dio un golpe con el martillo de easter egg ampliado]",
        },
        "rps": {
            "user_win": "[Perdiste contra {master} a piedra, papel o tijera]",
            "avatar_win": "[Ganaste a {master} a piedra, papel o tijera]",
            "draw": "[Empataste con {master} a piedra, papel o tijera]",
        },
    },
    "pt": {
        "lollipop": {
            "offer": "[{master} te deu uma mordida de pirulito]",
            "tease": "[{master} te deu outra mordida de pirulito]",
            "tap_soft": "[{master} continuou te dando o pirulito]",
        },
        "fist": {
            "poke": "[{master} tocou você de leve com a patinha de gato]",
            "rapid": "[{master} tocou você de leve várias vezes com a patinha de gato]",
        },
        "hammer": {
            "bonk": "[{master} bateu em você uma vez com um martelo]",
            "rapid": "[{master} bateu em você várias vezes seguidas com um martelo]",
            "easter_egg": "[{master} bateu em você uma vez com o martelo de easter egg ampliado]",
        },
        "rps": {
            "user_win": "[Perdeu para {master} no jogo de pedra, papel e tesoura]",
            "avatar_win": "[Venceu {master} no jogo de pedra, papel e tesoura]",
            "draw": "[Empatou com {master} no jogo de pedra, papel e tesoura]",
        },
    },
}

# master_name 缺失/空时按本地化中性词回退；禁止回落到"主人 / master / ご主人さま /
# 주인 / Хозяин"等物化称呼。
_AVATAR_INTERACTION_MEMORY_NOTE_MASTER_FALLBACK: dict[str, str] = {
    "zh": "对方",
    "zh-TW": "對方",
    "en": "they",
    "ja": "相手",
    "ko": "상대",
    "ru": "собеседник",
    "es": "esa persona",
    "pt": "a outra pessoa",
}
_AVATAR_INTERACTION_PROMPT_ACTOR_FALLBACK: dict[str, str] = {
    "zh": "对方",
    "zh-TW": "對方",
    "en": "The other person",
    "ja": "相手",
    "ko": "상대가",
    "ru": "Собеседник",
    "es": "Esa persona",
    "pt": "A outra pessoa",
}
def _avatar_interaction_locale(language: str | None) -> str:
    """Normalize a language code to an avatar-interaction prompt key.

    Deliberately no longer pre-normalizes through
    ``config._runtime.normalize_language_code``: that forwarder returns its
    input unchanged while unbound, which made Steam codes resolve differently
    in a bare import than in the running app ("tchinese" gave ``en`` in tests
    but ``zh-TW`` in production). ``normalize_prompt_locale`` is self-contained,
    so both agree.
    """
    raw_language = language or resolve_global_language()
    return normalize_prompt_locale(
        raw_language, default="en", simplified="zh", keep_traditional=True
    )


def _avatar_interaction_korean_subject_actor(name: str) -> str:
    """Return a Korean subject phrase for an arbitrary actor name.

    Hangul names can choose 이/가 exactly by final consonant. For latin names,
    use a small readability heuristic; other scripts stay unchanged.
    """
    stripped = str(name or "").strip()
    if not stripped:
        return _AVATAR_INTERACTION_PROMPT_ACTOR_FALLBACK["ko"]

    last_char = stripped[-1]
    codepoint = ord(last_char)
    if 0xAC00 <= codepoint <= 0xD7A3:
        has_final_consonant = (codepoint - 0xAC00) % 28 != 0
        marker = "이" if has_final_consonant else "가"
    elif last_char.isascii() and last_char.isalpha():
        # Latin display names are common in config; this keeps simple names
        # readable without forcing every non-Hangul script into a Korean marker.
        marker = "가" if last_char.lower() in {"a", "e", "i", "o", "u", "y"} else "이"
    else:
        return stripped
    return f"{stripped}{marker}"


def _avatar_interaction_prompt_actor(locale: str, master_name: str) -> str:
    stripped = str(master_name or "").strip()
    if locale == "ko":
        return _avatar_interaction_korean_subject_actor(stripped)
    if stripped:
        return stripped
    fallback = _AVATAR_INTERACTION_PROMPT_ACTOR_FALLBACK
    return fallback.get(locale, fallback["en"])


def _sanitize_avatar_interaction_text_context(
    text: str, max_tokens: int | None = None
) -> str:
    # truncate_to_tokens forwarded via config._runtime (DI; see top of file)
    # — config (L0) must not import utils (L1) directly.
    if max_tokens is None:
        # Lazy import 避免 config 包加载顺序问题（本文件被 config/__init__.py
        # 末尾的 re-export 路径间接导入）。
        from config import AVATAR_INTERACTION_CONTEXT_MAX_TOKENS

        max_tokens = AVATAR_INTERACTION_CONTEXT_MAX_TOKENS

    raw_text = str(text or "")
    if not raw_text:
        return ""

    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(
        char if char.isprintable() or char in {"\n", "\t", " "} else " "
        for char in normalized
    )

    sanitized_lines: list[str] = []
    for line in normalized.split("\n"):
        without_prefix = re.sub(r"^\s*(?:[-*•]+|\d+[.)]|[A-Za-z][.)]|#+)\s*", "", line)
        collapsed = re.sub(r"\s+", " ", without_prefix).strip()
        if collapsed:
            sanitized_lines.append(collapsed)

    if not sanitized_lines:
        return ""

    cleaned = " / ".join(sanitized_lines)
    safe_max_tokens = max(1, int(max_tokens))
    cleaned = truncate_to_tokens(cleaned, safe_max_tokens).rstrip()
    if not cleaned:
        return ""

    # Keep the compatibility field safely bounded and stable for diagnostics.
    # The direct event-fact prompt does not consume this normalized draft.
    return json.dumps(cleaned, ensure_ascii=False)


def _decode_avatar_interaction_text_context(payload: dict) -> str:
    """Decode sanitized or raw text_context for reserved markers only."""
    raw = payload.get("text_context") if isinstance(payload, dict) else None
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, str):
            text = parsed.strip()
    except Exception:
        pass
    return str(text).strip()


def _is_live2d_model_poke(payload: dict) -> bool:
    marker = _decode_avatar_interaction_text_context(payload).lower()
    return marker == _LIVE2D_MODEL_POKE_TEXT_CONTEXT


def _append_avatar_interaction_clause(
    locale: str, base: str, clause: str
) -> str:
    text = str(base or "").strip()
    extra = str(clause or "").strip()
    if not extra:
        return text
    if not text:
        return extra
    separator = "" if locale in {"zh", "zh-TW", "ja"} else " "
    return f"{text}{separator}{extra}"


def _bake_touch_zone_into_tool_focus(
    locale: str, reaction_focus: str, tool_id: str, touch_zone: str
) -> str:
    """Put the body part into the main event sentence for fist/hammer."""
    noun = (
        _AVATAR_INTERACTION_ZONE_LOCATION_NOUN.get(
            locale, _AVATAR_INTERACTION_ZONE_LOCATION_NOUN["en"]
        ).get(touch_zone, "")
    )
    focus = str(reaction_focus or "")
    if not noun or not focus or tool_id not in {"fist", "hammer"}:
        return focus

    if locale in {"zh", "zh-TW"}:
        replacements = (
            ("碰了你一下", f"碰了你的{noun}一下"),
            ("碰了你幾下", f"碰了你的{noun}幾下"),
            ("碰了你几下", f"碰了你的{noun}几下"),
            ("碰你時", f"碰你的{noun}時"),
            ("碰你时", f"碰你的{noun}时"),
            ("敲中了你一次", f"敲中了你的{noun}一次"),
            ("敲中了你好幾次", f"敲中了你的{noun}好幾次"),
            ("敲中了你好几次", f"敲中了你的{noun}好几次"),
        )
    elif locale == "en":
        replacements = (
            ("touched you once with the cat paw", f"touched your {noun} once with the cat paw"),
            (
                "touched you several times with the cat paw",
                f"touched your {noun} several times with the cat paw",
            ),
            ("touched you with the cat paw", f"touched your {noun} with the cat paw"),
            ("hit you once with the hammer", f"hit your {noun} once with the hammer"),
            (
                "hit you again with the hammer",
                f"hit your {noun} again with the hammer",
            ),
            (
                "hit you several times quickly with the hammer",
                f"hit your {noun} several times quickly with the hammer",
            ),
            (
                "hit you once with the enlarged easter-egg hammer",
                f"hit your {noun} once with the enlarged easter-egg hammer",
            ),
        )
    else:
        # Other locales keep the appended zone fact; grammar baking is fragile.
        return focus

    for old, new in replacements:
        if old in focus:
            return focus.replace(old, new, 1)
    return focus


def _build_avatar_interaction_instruction(
    language: str | None,
    lanlan_name: str,
    master_name: str,
    payload: dict,
) -> str:
    """Build the localized event fact sent to the model for an interaction."""
    locale = _avatar_interaction_locale(language)
    tool_id = payload["tool_id"]
    is_model_poke = tool_id == "fist" and _is_live2d_model_poke(payload)
    if tool_id == "rps":
        user_gesture, avatar_gesture, round_result = _require_rps_round_facts(payload)
        profile = _AVATAR_INTERACTION_RPS_PROMPT_PROFILES.get(
            locale, _AVATAR_INTERACTION_RPS_PROMPT_PROFILES["en"]
        )
        actor = _avatar_interaction_prompt_actor(locale, master_name)
        avatar = str(lanlan_name or "").strip()
        result = str(profile["results"][round_result]).format(
            actor=actor, avatar=avatar
        )
        reaction_focus = str(profile["template"]).format(
            actor=actor,
            avatar=avatar,
            user_gesture=profile["gestures"][user_gesture],
            avatar_gesture=profile["gestures"][avatar_gesture],
            result=result,
        )
        return _append_avatar_interaction_clause(
            locale,
            reaction_focus,
            _AVATAR_INTERACTION_RESPONSE_LOCK.get(
                locale, _AVATAR_INTERACTION_RESPONSE_LOCK["en"]
            ).get("rps", ""),
        )

    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _require_avatar_interaction_facts(tool_id, action_id, payload)
    actor = _avatar_interaction_prompt_actor(locale, master_name)
    touch_zone = str(payload.get("touch_zone") or "").strip().lower()

    if is_model_poke:
        zone_key = touch_zone if touch_zone in {"ear", "head", "face", "body"} else "body"
        poke_template = (
            _AVATAR_INTERACTION_MODEL_POKE_ZONE_FACTS.get(
                locale, _AVATAR_INTERACTION_MODEL_POKE_ZONE_FACTS["en"]
            ).get(zone_key, "")
        )
        reaction_focus = str(poke_template).format(
            lanlan_name=lanlan_name, master_name=actor, actor=actor
        )
        zone_hint = (
            _AVATAR_INTERACTION_TOUCH_ZONE_REACTION_HINTS.get(
                locale, _AVATAR_INTERACTION_TOUCH_ZONE_REACTION_HINTS["en"]
            ).get(zone_key, "")
        )
        reaction_focus = _append_avatar_interaction_clause(
            locale, reaction_focus, zone_hint
        )
        return _append_avatar_interaction_clause(
            locale,
            reaction_focus,
            _AVATAR_INTERACTION_RESPONSE_LOCK.get(
                locale, _AVATAR_INTERACTION_RESPONSE_LOCK["en"]
            ).get("model_poke", ""),
        )

    action_profiles = (
        _AVATAR_INTERACTION_REACTION_PROFILES.get(
            locale, _AVATAR_INTERACTION_REACTION_PROFILES["en"]
        )
        .get(tool_id, {})
        .get(action_id, {})
    )
    if payload.get("reward_drop") and action_profiles.get("reward_drop"):
        reward_key = f"reward_drop_{intensity}"
        reaction_profile = (
            action_profiles.get(reward_key) or action_profiles["reward_drop"]
        )
    else:
        reaction_profile = action_profiles.get(intensity)
    if reaction_profile is None:
        raise ValueError(
            "Missing avatar interaction profile for "
            f"{locale}/{tool_id}/{action_id}/{intensity}"
        )

    reaction_focus = str(reaction_profile["reaction_focus"]).format(
        lanlan_name=lanlan_name, master_name=actor, actor=actor
    )
    baked_zone = False
    if tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS and touch_zone:
        baked = _bake_touch_zone_into_tool_focus(
            locale, reaction_focus, tool_id, touch_zone
        )
        baked_zone = baked != reaction_focus
        reaction_focus = baked
        if not baked_zone:
            touch_zone_fact = (
                _AVATAR_INTERACTION_TOUCH_ZONE_FACTS.get(
                    locale, _AVATAR_INTERACTION_TOUCH_ZONE_FACTS["en"]
                ).get(touch_zone, "")
            )
            reaction_focus = _append_avatar_interaction_clause(
                locale, reaction_focus, touch_zone_fact
            )
        zone_hint = (
            _AVATAR_INTERACTION_TOUCH_ZONE_REACTION_HINTS.get(
                locale, _AVATAR_INTERACTION_TOUCH_ZONE_REACTION_HINTS["en"]
            ).get(touch_zone, "")
        )
        reaction_focus = _append_avatar_interaction_clause(
            locale, reaction_focus, zone_hint
        )
    return _append_avatar_interaction_clause(
        locale,
        reaction_focus,
        _AVATAR_INTERACTION_RESPONSE_LOCK.get(
            locale, _AVATAR_INTERACTION_RESPONSE_LOCK["en"]
        ).get(tool_id, ""),
    )


def _build_avatar_interaction_memory_meta(
    language: str | None, payload: dict, master_name: str
) -> dict:
    """Build the memory_note + dedupe metadata for an avatar interaction.

    ``master_name`` is required: templates only use the ``{master}`` placeholder to
    refer to "the person interacting with the AI"; objectifying literals like
    "主人 / Your master / ご主人さま / 주인 / Хозяин" are forbidden.
    When an empty string is passed in, falls back to the localized neutral word from
    ``_AVATAR_INTERACTION_MEMORY_NOTE_MASTER_FALLBACK`` (zh="对方", en="they", etc.),
    which likewise never degrades to an objectifying title.
    """  # noqa: DOCSTRING_CJK
    locale = _avatar_interaction_locale(language)
    templates = _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.get(locale, {})
    fallback = _AVATAR_INTERACTION_MEMORY_NOTE_MASTER_FALLBACK
    master = str(master_name or "").strip() or fallback.get(locale, fallback["en"])
    tool_id = str(payload.get("tool_id") or "").strip().lower()
    if tool_id == "rps":
        _, _, round_result = _require_rps_round_facts(payload)
        memory_note = templates.get("rps", {}).get(round_result, "").format(
            master=master
        )
        return {
            "memory_note": memory_note,
            "memory_dedupe_key": "rps_round",
            "memory_dedupe_rank": 1,
        }
    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _require_avatar_interaction_facts(tool_id, action_id, payload)

    memory_note = ""
    dedupe_key = tool_id or "avatar_interaction"
    dedupe_rank = 1

    if tool_id == "lollipop":
        dedupe_key = "lollipop_feed"
        if action_id == "tap_soft":
            memory_note = templates.get("lollipop", {}).get("tap_soft", "")
            dedupe_rank = 4 if intensity == "burst" else 3
        elif action_id == "tease":
            memory_note = templates.get("lollipop", {}).get("tease", "")
            dedupe_rank = 2
        else:
            memory_note = templates.get("lollipop", {}).get("offer", "")
            dedupe_rank = 1
    elif tool_id == "fist":
        touch_zone = str(payload.get("touch_zone") or "").strip().lower()
        if _is_live2d_model_poke(payload):
            dedupe_key = "model_poke"
            zone_key = touch_zone if touch_zone in {"ear", "head", "face", "body"} else "body"
            memory_note = (
                templates.get("model_poke", {}).get(zone_key, "")
                or _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.get("en", {})
                .get("model_poke", {})
                .get(zone_key, "")
                or _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.get("zh", {})
                .get("model_poke", {})
                .get(zone_key, "")
            )
            dedupe_rank = 1
        else:
            dedupe_key = "fist_touch"
            if intensity in {"rapid", "burst"}:
                memory_note = templates.get("fist", {}).get(
                    "rapid", templates.get("fist", {}).get("poke", "")
                )
                dedupe_rank = 3 if intensity == "burst" else 2
            else:
                memory_note = templates.get("fist", {}).get("poke", "")
                dedupe_rank = 1
    elif tool_id == "hammer":
        dedupe_key = "hammer_bonk"
        if intensity == "easter_egg":
            memory_note = templates.get("hammer", {}).get(
                "easter_egg", templates.get("hammer", {}).get("bonk", "")
            )
            dedupe_rank = 4
        elif intensity in {"rapid", "burst"}:
            memory_note = templates.get("hammer", {}).get(
                "rapid", templates.get("hammer", {}).get("bonk", "")
            )
            dedupe_rank = 3 if intensity == "burst" else 2
        else:
            memory_note = templates.get("hammer", {}).get("bonk", "")
            dedupe_rank = 1
    else:
        memory_note = templates.get(tool_id, {}).get(action_id, "")

    formatted_note = str(memory_note or "").strip()
    if formatted_note and "{master}" in formatted_note:
        formatted_note = formatted_note.format(master=master)
    touch_zone = str(payload.get("touch_zone") or "").strip().lower()
    # Model-poke notes already name the body part; skip redundant zone fact.
    touch_zone_fact = (
        _AVATAR_INTERACTION_TOUCH_ZONE_FACTS.get(
            locale, _AVATAR_INTERACTION_TOUCH_ZONE_FACTS["en"]
        ).get(touch_zone, "")
        if (
            tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS
            and not _is_live2d_model_poke(payload)
        )
        else ""
    )
    if formatted_note and touch_zone_fact:
        fact = touch_zone_fact.rstrip(".。")
        separator = "；" if locale in {"zh", "zh-TW", "ja"} else "; "
        if formatted_note.endswith("]"):
            formatted_note = f"{formatted_note[:-1]}{separator}{fact}]"
        else:
            formatted_note = f"{formatted_note}{separator}{fact}"

    return {
        "memory_note": formatted_note,
        "memory_dedupe_key": dedupe_key,
        "memory_dedupe_rank": dedupe_rank,
    }
