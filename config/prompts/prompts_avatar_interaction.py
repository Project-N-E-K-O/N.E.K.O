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
(lollipop / fist / hammer) — these helpers validate the payload,
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
    normalize_language_code,
    resolve_global_language,
    truncate_to_tokens,
)
from config.prompts.avatar_interaction_contract import (
    AVATAR_INTERACTION_TOUCH_ZONE_TOOLS as _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS,
    normalize_avatar_interaction_intensity as _normalize_avatar_interaction_intensity,
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
        "hammer": {
            "bonk": "[{master}用锤子敲了你一下]",
            "rapid": "[{master}连续用锤子敲了你好几下]",
            "easter_egg": "[{master}用放大彩蛋锤敲了你一下]",
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
        "hammer": {
            "bonk": "[{master} bonked you once with a hammer]",
            "rapid": "[{master} bonked you several times with a hammer]",
            "easter_egg": "[{master} hit you once with the enlarged easter-egg hammer]",
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
    raw_language = language or resolve_global_language()
    normalized = normalize_language_code(raw_language, format="full")
    locale = str(normalized or "en").strip().lower()
    if locale.startswith("zh"):
        if "tw" in locale or "hant" in locale or "hk" in locale:
            return "zh-TW"
        return "zh"
    if locale.startswith("ja"):
        return "ja"
    if locale.startswith("ko"):
        return "ko"
    if locale.startswith("ru"):
        return "ru"
    if locale.startswith("es"):
        return "es"
    if locale.startswith("pt"):
        return "pt"
    return "en"


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


def _build_avatar_interaction_instruction(
    language: str | None,
    lanlan_name: str,
    master_name: str,
    payload: dict,
) -> str:
    """Build the localized event fact sent to the model for an interaction."""
    locale = _avatar_interaction_locale(language)
    tool_id = payload["tool_id"]
    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _require_avatar_interaction_facts(tool_id, action_id, payload)

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

    actor = _avatar_interaction_prompt_actor(locale, master_name)
    reaction_focus = str(reaction_profile["reaction_focus"]).format(
        lanlan_name=lanlan_name, master_name=actor, actor=actor
    )
    touch_zone = str(payload.get("touch_zone") or "").strip().lower()
    touch_zone_fact = (
        _AVATAR_INTERACTION_TOUCH_ZONE_FACTS.get(
            locale, _AVATAR_INTERACTION_TOUCH_ZONE_FACTS["en"]
        ).get(touch_zone, "")
        if tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS
        else ""
    )
    if touch_zone_fact:
        fact_separator = "" if locale in {"zh", "zh-TW", "ja"} else " "
        reaction_focus = f"{reaction_focus}{fact_separator}{touch_zone_fact}"
    return reaction_focus


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
    touch_zone_fact = (
        _AVATAR_INTERACTION_TOUCH_ZONE_FACTS.get(
            locale, _AVATAR_INTERACTION_TOUCH_ZONE_FACTS["en"]
        ).get(touch_zone, "")
        if tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS
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
