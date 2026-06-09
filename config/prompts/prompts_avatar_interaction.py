"""
Avatar-interaction prompt templates and payload normalizers.

Used when the frontend reports a tool-based avatar interaction
(lollipop / fist / hammer) — these helpers validate the payload,
localize labels, and compose the system instruction + memory note
that drive the runtime reaction.
"""

from __future__ import annotations

import json
import re
import time
import math
from typing import Optional

# Why config._runtime: ``config`` (L0) must not import from ``utils`` (L1) —
# enforced by scripts/check_module_layering.py. Higher layers register the
# concrete language/tokenize helpers at app startup; we read them via
# resolvers that fall back gracefully when nothing is bound.
from config._runtime import (
    normalize_language_code,
    resolve_global_language,
    truncate_to_tokens,
)


_AVATAR_INTERACTION_ALLOWED_ACTIONS = {
    "lollipop": {"offer", "tease", "tap_soft"},
    "fist": {"poke"},
    "hammer": {"bonk"},
}
_AVATAR_INTERACTION_ALLOWED_INTENSITIES = {"normal", "rapid", "burst", "easter_egg"}
_AVATAR_INTERACTION_ALLOWED_INTENSITY_COMBINATIONS = {
    "lollipop": {
        "offer": {"normal"},
        "tease": {"normal"},
        "tap_soft": {"rapid", "burst"},
    },
    "fist": {
        "poke": {"normal", "rapid"},
    },
    "hammer": {
        "bonk": {"normal", "rapid", "burst", "easter_egg"},
    },
}
_AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES = {"ear", "head", "face", "body"}
_AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS = {"fist", "hammer"}
_AVATAR_INTERACTION_TOOL_LABELS = {
    "zh": {
        "lollipop": "棒棒糖",
        "fist": "猫爪",
        "hammer": "锤子",
    },
    "zh-TW": {
        "lollipop": "棒棒糖",
        "fist": "貓爪",
        "hammer": "槌子",
    },
    "en": {
        "lollipop": "lollipop",
        "fist": "cat paw",
        "hammer": "hammer",
    },
    "ja": {
        "lollipop": "ペロペロキャンディ",
        "fist": "猫の肉球",
        "hammer": "ハンマー",
    },
    "ko": {
        "lollipop": "막대사탕",
        "fist": "고양이 발",
        "hammer": "망치",
    },
    "ru": {
        "lollipop": "леденец",
        "fist": "кошачья лапка",
        "hammer": "молоток",
    },
    "es": {"lollipop": "piruleta", "fist": "patita de gato", "hammer": "martillo"},
    "pt": {"lollipop": "pirulito", "fist": "patinha de gato", "hammer": "martelo"},
}
_AVATAR_INTERACTION_ACTION_LABELS = {
    "zh": {
        "lollipop": {
            "offer": "第一口",
            "tease": "第二口",
            "tap_soft": "连续投喂",
        },
        "fist": {
            "poke": "轻触",
        },
        "hammer": {
            "bonk": "锤击",
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": "第一口",
            "tease": "第二口",
            "tap_soft": "連續投餵",
        },
        "fist": {
            "poke": "輕觸",
        },
        "hammer": {
            "bonk": "槌擊",
        },
    },
    "en": {
        "lollipop": {
            "offer": "first bite",
            "tease": "second bite",
            "tap_soft": "repeated feeding",
        },
        "fist": {
            "poke": "light touch",
        },
        "hammer": {
            "bonk": "hammer hit",
        },
    },
    "ja": {
        "lollipop": {
            "offer": "ひとくち目",
            "tease": "ふたくち目",
            "tap_soft": "連続で食べさせる",
        },
        "fist": {
            "poke": "軽く触れる",
        },
        "hammer": {
            "bonk": "ハンマーでゴツン",
        },
    },
    "ko": {
        "lollipop": {
            "offer": "첫 한입",
            "tease": "두 번째 한입",
            "tap_soft": "연속 먹이기",
        },
        "fist": {
            "poke": "가볍게 톡",
        },
        "hammer": {
            "bonk": "망치질",
        },
    },
    "ru": {
        "lollipop": {
            "offer": "первый кусочек",
            "tease": "второй кусочек",
            "tap_soft": "повторное угощение",
        },
        "fist": {
            "poke": "лёгкое касание",
        },
        "hammer": {
            "bonk": "удар молотком",
        },
    },
    "es": {
        "lollipop": {
            "offer": "primer bocado",
            "tease": "segundo bocado",
            "tap_soft": "alimentación repetida",
        },
        "fist": {"poke": "toque ligero"},
        "hammer": {"bonk": "golpe de martillo"},
    },
    "pt": {
        "lollipop": {
            "offer": "primeira mordida",
            "tease": "segunda mordida",
            "tap_soft": "alimentação repetida",
        },
        "fist": {"poke": "toque leve"},
        "hammer": {"bonk": "batida de martelo"},
    },
}
_AVATAR_INTERACTION_INTENSITY_LABELS = {
    "zh": {
        "normal": "正常",
        "rapid": "偏高频",
        "burst": "连续爆发",
        "easter_egg": "彩蛋爆发",
    },
    "zh-TW": {
        "normal": "正常",
        "rapid": "偏高頻",
        "burst": "連續爆發",
        "easter_egg": "彩蛋爆發",
    },
    "en": {
        "normal": "normal",
        "rapid": "rapid",
        "burst": "burst",
        "easter_egg": "easter egg",
    },
    "ja": {
        "normal": "通常",
        "rapid": "高頻度",
        "burst": "連続ラッシュ",
        "easter_egg": "イースターエッグ",
    },
    "ko": {
        "normal": "보통",
        "rapid": "빠름",
        "burst": "연속 폭발",
        "easter_egg": "이스터에그",
    },
    "ru": {
        "normal": "обычно",
        "rapid": "часто",
        "burst": "серия",
        "easter_egg": "пасхалка",
    },
    "es": {
        "normal": "normal",
        "rapid": "rápido",
        "burst": "ráfaga",
        "easter_egg": "easter egg",
    },
    "pt": {
        "normal": "normal",
        "rapid": "rápido",
        "burst": "rajada",
        "easter_egg": "easter egg",
    },
}
_AVATAR_INTERACTION_TOUCH_ZONE_LABELS = {
    "zh": {
        "ear": "耳侧",
        "head": "头顶",
        "face": "脸侧/嘴边",
        "body": "身前/肩侧",
    },
    "zh-TW": {
        "ear": "耳側",
        "head": "頭頂",
        "face": "臉側/嘴邊",
        "body": "身前/肩側",
    },
    "en": {
        "ear": "ear side",
        "head": "top of the head",
        "face": "cheek / mouth side",
        "body": "front body / shoulder side",
    },
    "ja": {
        "ear": "耳の横",
        "head": "頭のてっぺん",
        "face": "頬 / 口元",
        "body": "体の前 / 肩の横",
    },
    "ko": {
        "ear": "귀 옆",
        "head": "머리 위",
        "face": "볼 / 입가",
        "body": "몸 앞 / 어깨 옆",
    },
    "ru": {
        "ear": "возле уха",
        "head": "макушка",
        "face": "щека / край рта",
        "body": "перед корпусом / плечо",
    },
    "es": {
        "ear": "lado de la oreja",
        "head": "parte superior de la cabeza",
        "face": "mejilla / junto a la boca",
        "body": "frente del cuerpo / hombro",
    },
    "pt": {
        "ear": "lado da orelha",
        "head": "topo da cabeça",
        "face": "bochecha / canto da boca",
        "body": "frente do corpo / ombro",
    },
}
_AVATAR_INTERACTION_SYSTEM_WRAPPER = {
    "zh": {
        "prefix": "",
        "suffix": "",
    },
    "zh-TW": {
        "prefix": "",
        "suffix": "",
    },
    "en": {
        "prefix": "======[System notice: the following tool interaction just happened. Treat it as an immediate interaction cue and do not repeat field names or system wording]======",
        "suffix": "======[System notice end: respond directly in character with the immediate reaction only]======",
    },
    "ja": {
        "prefix": "======[システム通知: 以下はたった今発生した道具インタラクションです。即時の反応のきっかけとして扱い、項目名やシステム文言をそのまま繰り返さないでください]======",
        "suffix": "======[システム通知終了: 現在のキャラクター口調で即時反応だけを返してください]======",
    },
    "ko": {
        "prefix": "======[시스템 알림: 아래는 방금 발생한 도구 상호작용입니다. 즉시 반응해야 하는 단서로만 사용하고, 항목명이나 시스템 문구를 그대로 반복하지 마세요]======",
        "suffix": "======[시스템 알림 종료: 현재 캐릭터 말투로 즉각적인 반응만 출력하세요]======",
    },
    "ru": {
        "prefix": "======[Системное уведомление: ниже описано только что произошедшее взаимодействие с инструментом. Считайте это сигналом для мгновенной реакции и не повторяйте названия полей или системные формулировки]======",
        "suffix": "======[Конец системного уведомления: ответьте только мгновенной реакцией в текущем образе персонажа]======",
    },
    "es": {
        "prefix": "======[Aviso del sistema: acaba de ocurrir la siguiente interacción con herramienta. Trátala como una señal de interacción inmediata y no repitas nombres de campos ni texto del sistema]======",
        "suffix": "======[Fin del aviso del sistema: responde directamente en personaje solo con la reacción inmediata]======",
    },
    "pt": {
        "prefix": "======[Aviso do sistema: a seguinte interação com ferramenta acabou de acontecer. Trate-a como um sinal de interação imediata e não repita nomes de campos nem texto do sistema]======",
        "suffix": "======[Fim do aviso do sistema: responda diretamente no personagem apenas com a reação imediata]======",
    },
}
_AVATAR_INTERACTION_REACTION_PROFILES = {
    "zh": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}刚刚把棒棒糖递到你嘴边，你吃了第一口。",
                    "style_hint": "第一口棒棒糖。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}刚刚又把同一支棒棒糖递到你嘴边，你吃了第二口。",
                    "style_hint": "同一支棒棒糖又一口。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}刚刚把棒棒糖一口接一口递到你嘴边，你连续吃了几口。",
                    "style_hint": "连续几口棒棒糖。",
                },
                "burst": {
                    "reaction_focus": "{actor}刚刚短时间内连续把棒棒糖递到你嘴边，你吃了好几口。",
                    "style_hint": "短时间连续几口棒棒糖。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}刚刚用猫爪轻轻碰了你一下。",
                    "style_hint": "猫爪轻碰。",
                },
                "rapid": {
                    "reaction_focus": "{actor}刚刚用猫爪连续轻轻碰了你几下。",
                    "style_hint": "猫爪连续轻碰。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}刚刚用猫爪轻轻碰你时掉出了奖励。",
                    "style_hint": "猫爪轻碰并掉出奖励。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}刚刚用猫爪连续轻轻碰了你几下时掉出了奖励。",
                    "style_hint": "猫爪连续轻碰并掉出奖励。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}刚刚用锤子敲中了你一次。",
                    "style_hint": "锤子敲中一次。",
                },
                "rapid": {
                    "reaction_focus": "{actor}刚刚短时间内又用锤子敲中了你一次。",
                    "style_hint": "锤子再次敲中。",
                },
                "burst": {
                    "reaction_focus": "{actor}刚刚用锤子连续快速敲中了你好几次。",
                    "style_hint": "锤子连续快速敲中。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}刚刚用放大彩蛋锤敲中了你一次。",
                    "style_hint": "放大彩蛋锤敲中。",
                },
            },
        },
    },
    "en": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} just brought the lollipop to your mouth, and you took the first bite.",
                    "style_hint": "First lollipop bite.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} just brought the same lollipop to your mouth again, and you took a second bite.",
                    "style_hint": "Same lollipop, another bite.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} just kept bringing the lollipop to your mouth, and you took several bites in a row.",
                    "style_hint": "Several lollipop bites in a row.",
                },
                "burst": {
                    "reaction_focus": "{actor} just brought the lollipop to your mouth several times in quick succession, and you took several bites.",
                    "style_hint": "Several quick lollipop bites.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} just lightly touched you once with the cat paw.",
                    "style_hint": "Single cat-paw touch.",
                },
                "rapid": {
                    "reaction_focus": "{actor} just lightly touched you several times with the cat paw.",
                    "style_hint": "Repeated cat-paw touches.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} just lightly touched you with the cat paw, and a reward dropped.",
                    "style_hint": "Cat-paw touch with reward drop.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} just lightly touched you several times with the cat paw, and a reward dropped.",
                    "style_hint": "Repeated cat-paw touches with reward drop.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} just hit you once with the hammer.",
                    "style_hint": "Single hammer hit.",
                },
                "rapid": {
                    "reaction_focus": "{actor} just hit you again with the hammer within a short time.",
                    "style_hint": "Second hammer hit.",
                },
                "burst": {
                    "reaction_focus": "{actor} just hit you several times quickly with the hammer.",
                    "style_hint": "Rapid repeated hammer hits.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} just hit you once with the enlarged easter-egg hammer.",
                    "style_hint": "Enlarged easter-egg hammer hit.",
                },
            },
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}剛剛把棒棒糖遞到你嘴邊，你吃了第一口。",
                    "style_hint": "第一口棒棒糖。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}剛剛又把同一支棒棒糖遞到你嘴邊，你吃了第二口。",
                    "style_hint": "同一支棒棒糖又一口。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}剛剛把棒棒糖一口接一口遞到你嘴邊，你連續吃了幾口。",
                    "style_hint": "連續幾口棒棒糖。",
                },
                "burst": {
                    "reaction_focus": "{actor}剛剛短時間內連續把棒棒糖遞到你嘴邊，你吃了好幾口。",
                    "style_hint": "短時間連續幾口棒棒糖。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}剛剛用貓爪輕輕碰了你一下。",
                    "style_hint": "貓爪輕碰。",
                },
                "rapid": {
                    "reaction_focus": "{actor}剛剛用貓爪連續輕輕碰了你幾下。",
                    "style_hint": "貓爪連續輕碰。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}剛剛用貓爪輕輕碰你時掉出了獎勵。",
                    "style_hint": "貓爪輕碰並掉出獎勵。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}剛剛用貓爪連續輕輕碰了你幾下時掉出了獎勵。",
                    "style_hint": "貓爪連續輕碰並掉出獎勵。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}剛剛用槌子敲中了你一次。",
                    "style_hint": "槌子敲中一次。",
                },
                "rapid": {
                    "reaction_focus": "{actor}剛剛短時間內又用槌子敲中了你一次。",
                    "style_hint": "槌子再次敲中。",
                },
                "burst": {
                    "reaction_focus": "{actor}剛剛用槌子連續快速敲中了你好幾次。",
                    "style_hint": "槌子連續快速敲中。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}剛剛用放大彩蛋槌敲中了你一次。",
                    "style_hint": "放大彩蛋槌敲中。",
                },
            },
        },
    },
    "ja": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor}が今、ペロペロキャンディをあなたの口元に差し出し、あなたが最初の一口を食べた。",
                    "style_hint": "ペロペロキャンディの最初の一口。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor}が今、同じペロペロキャンディをもう一度あなたの口元に差し出し、あなたが二口目を食べた。",
                    "style_hint": "同じペロペロキャンディをもう一口。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor}が今、ペロペロキャンディを続けてあなたの口元に差し出し、あなたが何口か続けて食べている。",
                    "style_hint": "ペロペロキャンディを一口ずつ。",
                },
                "burst": {
                    "reaction_focus": "{actor}が今、短い間にペロペロキャンディを何度もあなたの口元に差し出し、あなたが何口も食べた。",
                    "style_hint": "ペロペロキャンディを短時間で何口も。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor}が今、猫の肉球で一度だけ軽く触れた。",
                    "style_hint": "猫の肉球の一回接触。",
                },
                "rapid": {
                    "reaction_focus": "{actor}が今、猫の肉球で何度か続けて軽く触れた。",
                    "style_hint": "猫の肉球の連続接触。",
                },
                "reward_drop": {
                    "reaction_focus": "{actor}が今、猫の肉球で軽く触れた時に報酬が落ちた。",
                    "style_hint": "猫の肉球接触と報酬ドロップ。",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor}が今、猫の肉球で何度か続けて軽く触れた時に報酬が落ちた。",
                    "style_hint": "猫の肉球の連続接触と報酬ドロップ。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor}が今、ハンマーで一度当てた。",
                    "style_hint": "ハンマーの一回命中。",
                },
                "rapid": {
                    "reaction_focus": "{actor}が今、短時間でもう一度ハンマーを当てた。",
                    "style_hint": "ハンマーの二回目命中。",
                },
                "burst": {
                    "reaction_focus": "{actor}が今、ハンマーを何度も続けて当てた。",
                    "style_hint": "ハンマーの高速連続命中。",
                },
                "easter_egg": {
                    "reaction_focus": "{actor}が今、拡大イースターエッグのハンマーを一度当てた。",
                    "style_hint": "拡大イースターエッグのハンマー命中。",
                },
            },
        },
    },
    "ko": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} 방금 막대사탕을 네 입가에 내밀었고, 너는 첫 한입을 먹었다.",
                    "style_hint": "막대사탕 첫 한입.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} 방금 같은 막대사탕을 다시 네 입가에 내밀었고, 너는 두 번째 한입을 먹었다.",
                    "style_hint": "같은 막대사탕 한입 더.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} 방금 막대사탕을 한입씩 계속 네 입가에 내밀었고, 너는 몇 입 연달아 먹었다.",
                    "style_hint": "막대사탕 한입씩 계속.",
                },
                "burst": {
                    "reaction_focus": "{actor} 방금 짧은 시간 안에 막대사탕을 여러 번 네 입가에 내밀었고, 너는 여러 입 빠르게 먹었다.",
                    "style_hint": "막대사탕 짧은 시간 여러 입.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} 방금 고양이 발로 한 번 가볍게 건드렸다.",
                    "style_hint": "고양이 발 한 번 터치.",
                },
                "rapid": {
                    "reaction_focus": "{actor} 방금 고양이 발로 여러 번 가볍게 건드렸다.",
                    "style_hint": "고양이 발 연속 터치.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} 방금 고양이 발로 가볍게 건드렸을 때 보상이 떨어졌다.",
                    "style_hint": "고양이 발 터치와 보상 드롭.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} 방금 고양이 발로 여러 번 가볍게 건드렸을 때 보상이 떨어졌다.",
                    "style_hint": "고양이 발 연속 터치와 보상 드롭.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} 방금 망치로 한 번 맞혔다.",
                    "style_hint": "망치 한 번 명중.",
                },
                "rapid": {
                    "reaction_focus": "{actor} 방금 짧은 시간 안에 망치로 다시 한 번 맞혔다.",
                    "style_hint": "망치 두 번째 명중.",
                },
                "burst": {
                    "reaction_focus": "{actor} 방금 망치로 여러 번 빠르게 맞혔다.",
                    "style_hint": "망치 빠른 연속 명중.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} 방금 확대 이스터에그 망치로 한 번 맞혔다.",
                    "style_hint": "확대 이스터에그 망치 명중.",
                },
            },
        },
    },
    "ru": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} подносит леденец к твоему рту, и ты съедаешь первый кусочек.",
                    "style_hint": "Первый кусочек леденца.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} снова подносит тот же леденец к твоему рту, и ты съедаешь второй кусочек.",
                    "style_hint": "Ещё кусочек того же леденца.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} продолжает подносить леденец к твоему рту, и ты съедаешь несколько кусочков подряд.",
                    "style_hint": "Леденец кусочек за кусочком.",
                },
                "burst": {
                    "reaction_focus": "{actor} быстро несколько раз подносит леденец к твоему рту, и ты съедаешь несколько кусочков.",
                    "style_hint": "Несколько быстрых кусочков леденца.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} только что один раз легко коснулся тебя кошачьей лапкой.",
                    "style_hint": "Одно касание кошачьей лапкой.",
                },
                "rapid": {
                    "reaction_focus": "{actor} только что несколько раз легко коснулся тебя кошачьей лапкой.",
                    "style_hint": "Повторяющиеся касания кошачьей лапкой.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} только что легко коснулся тебя кошачьей лапкой, и выпала награда.",
                    "style_hint": "Касание кошачьей лапкой и выпадение награды.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} только что несколько раз легко коснулся тебя кошачьей лапкой, и выпала награда.",
                    "style_hint": "Повторяющиеся касания кошачьей лапкой и выпадение награды.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} только что один раз попал по тебе молотком.",
                    "style_hint": "Одно попадание молотком.",
                },
                "rapid": {
                    "reaction_focus": "{actor} только что снова попал по тебе молотком за короткое время.",
                    "style_hint": "Второе попадание молотком.",
                },
                "burst": {
                    "reaction_focus": "{actor} только что быстро попал по тебе молотком несколько раз подряд.",
                    "style_hint": "Быстрые повторяющиеся попадания молотком.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} только что один раз попал по тебе увеличенным пасхальным молотком.",
                    "style_hint": "Попадание увеличенным пасхальным молотком.",
                },
            },
        },
    },
    "es": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca, y diste el primer bocado.",
                    "style_hint": "Primer bocado de piruleta.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} acaba de acercarte otra vez la misma piruleta a la boca, y diste un segundo bocado.",
                    "style_hint": "Otro bocado de la misma piruleta.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca varias veces seguidas, y diste varios bocados.",
                    "style_hint": "Piruleta bocado tras bocado.",
                },
                "burst": {
                    "reaction_focus": "{actor} acaba de acercarte la piruleta a la boca varias veces en poco tiempo, y diste varios bocados rápidos.",
                    "style_hint": "Varios bocados rápidos de piruleta.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} acaba de tocarte una vez con la patita de gato.",
                    "style_hint": "Un toque de patita de gato.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acaba de tocarte varias veces con la patita de gato.",
                    "style_hint": "Toques repetidos de patita de gato.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} acaba de tocarte con la patita de gato y cayó una recompensa.",
                    "style_hint": "Toque de patita de gato con recompensa.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} acaba de tocarte varias veces con la patita de gato y cayó una recompensa.",
                    "style_hint": "Toques repetidos de patita de gato con recompensa.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} acaba de golpearte una vez con el martillo.",
                    "style_hint": "Un golpe de martillo.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acaba de volver a golpearte con el martillo en poco tiempo.",
                    "style_hint": "Segundo golpe de martillo.",
                },
                "burst": {
                    "reaction_focus": "{actor} acaba de golpearte varias veces rápido con el martillo.",
                    "style_hint": "Golpes rápidos repetidos de martillo.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} acaba de golpearte una vez con el martillo easter egg ampliado.",
                    "style_hint": "Golpe de martillo easter egg ampliado.",
                },
            }
        },
    },
    "pt": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca, e você deu a primeira mordida.",
                    "style_hint": "Primeira mordida de pirulito.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "{actor} acabou de aproximar o mesmo pirulito da sua boca outra vez, e você deu uma segunda mordida.",
                    "style_hint": "Outra mordida do mesmo pirulito.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca várias vezes seguidas, e você deu várias mordidas.",
                    "style_hint": "Pirulito mordida após mordida.",
                },
                "burst": {
                    "reaction_focus": "{actor} acabou de aproximar o pirulito da sua boca várias vezes em pouco tempo, e você deu várias mordidas rápidas.",
                    "style_hint": "Várias mordidas rápidas de pirulito.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "{actor} acabou de tocar em você uma vez com a patinha de gato.",
                    "style_hint": "Um toque de patinha de gato.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acabou de tocar em você várias vezes com a patinha de gato.",
                    "style_hint": "Toques repetidos de patinha de gato.",
                },
                "reward_drop": {
                    "reaction_focus": "{actor} acabou de tocar em você com a patinha de gato e caiu uma recompensa.",
                    "style_hint": "Toque de patinha de gato com recompensa.",
                },
                "reward_drop_rapid": {
                    "reaction_focus": "{actor} acabou de tocar em você várias vezes com a patinha de gato e caiu uma recompensa.",
                    "style_hint": "Toques repetidos de patinha de gato com recompensa.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "{actor} acabou de bater em você uma vez com o martelo.",
                    "style_hint": "Um golpe de martelo.",
                },
                "rapid": {
                    "reaction_focus": "{actor} acabou de bater em você de novo com o martelo em pouco tempo.",
                    "style_hint": "Segundo golpe de martelo.",
                },
                "burst": {
                    "reaction_focus": "{actor} acabou de bater em você várias vezes rapidamente com o martelo.",
                    "style_hint": "Golpes rápidos repetidos de martelo.",
                },
                "easter_egg": {
                    "reaction_focus": "{actor} acabou de bater em você uma vez com o martelo easter egg ampliado.",
                    "style_hint": "Golpe de martelo easter egg ampliado.",
                },
            }
        },
    },
}
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
            "poke": "[{master}摸了摸你的头]",
            "rapid": "[{master}连续摸了摸你的头]",
        },
        "hammer": {
            "bonk": "[{master}用锤子敲了敲你的头]",
            "rapid": "[{master}连续敲了你好几下]",
            "easter_egg": "[{master}用锤子重重敲了你的头]",
        },
    },
    "en": {
        "lollipop": {
            "offer": "[{master} fed you a bite of lollipop]",
            "tease": "[{master} fed you another bite of lollipop]",
            "tap_soft": "[{master} kept feeding you the lollipop]",
        },
        "fist": {
            "poke": "[{master} gave your head a gentle pat]",
            "rapid": "[{master} repeatedly patted your head]",
        },
        "hammer": {
            "bonk": "[{master} bonked your head with a hammer]",
            "rapid": "[{master} bonked you several times]",
            "easter_egg": "[{master} hit your head hard with a hammer]",
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": "[{master}餵了你一口棒棒糖]",
            "tease": "[{master}又餵了你一口棒棒糖]",
            "tap_soft": "[{master}連續拿棒棒糖餵你]",
        },
        "fist": {
            "poke": "[{master}摸了摸你的頭]",
            "rapid": "[{master}連續摸了摸你的頭]",
        },
        "hammer": {
            "bonk": "[{master}用槌子敲了敲你的頭]",
            "rapid": "[{master}連續敲了你好幾下]",
            "easter_egg": "[{master}用槌子重重敲了你的頭]",
        },
    },
    "ja": {
        "lollipop": {
            "offer": "[{master}があなたにペロペロキャンディをひとくち食べさせた]",
            "tease": "[{master}があなたにもうひとくちペロペロキャンディを食べさせた]",
            "tap_soft": "[{master}がペロペロキャンディを続けて食べさせた]",
        },
        "fist": {
            "poke": "[{master}があなたの頭にそっと触れた]",
            "rapid": "[{master}があなたの頭を続けて軽く触れた]",
        },
        "hammer": {
            "bonk": "[{master}がハンマーであなたの頭をこつんと叩いた]",
            "rapid": "[{master}があなたを何度か続けて叩いた]",
            "easter_egg": "[{master}がハンマーであなたの頭を強く叩いた]",
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
            "poke": "[{master}이 네 머리를 살짝 만져 줬다]",
            "rapid": "[{master}이 네 머리를 여러 번 연달아 만져 줬다]",
        },
        "hammer": {
            "bonk": "[{master}이 망치로 네 머리를 콩 쳤다]",
            "rapid": "[{master}이 너를 여러 번 연달아 쳤다]",
            "easter_egg": "[{master}이 망치로 네 머리를 세게 쳤다]",
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
            "poke": "[{master} мягко погладил тебя по голове]",
            "rapid": "[{master} несколько раз подряд погладил тебя по голове]",
        },
        "hammer": {
            "bonk": "[{master} стукнул тебя молотком по голове]",
            "rapid": "[{master} несколько раз подряд ударил тебя]",
            "easter_egg": "[{master} сильно ударил тебя молотком по голове]",
        },
    },
    "es": {
        "lollipop": {
            "offer": "[{master} te dio un bocado de piruleta]",
            "tease": "[{master} te dio otro bocado de piruleta]",
            "tap_soft": "[{master} siguió dándote la piruleta]",
        },
        "fist": {
            "poke": "[{master} te dio una caricia suave en la cabeza]",
            "rapid": "[{master} te acarició la cabeza varias veces]",
        },
        "hammer": {
            "bonk": "[{master} te dio un golpecito en la cabeza con un martillo]",
            "rapid": "[{master} te golpeó varias veces seguidas]",
            "easter_egg": "[{master} te golpeó fuerte la cabeza con un martillo]",
        },
    },
    "pt": {
        "lollipop": {
            "offer": "[{master} te deu uma mordida de pirulito]",
            "tease": "[{master} te deu outra mordida de pirulito]",
            "tap_soft": "[{master} continuou te dando o pirulito]",
        },
        "fist": {
            "poke": "[{master} fez um carinho leve na sua cabeça]",
            "rapid": "[{master} fez carinho várias vezes na sua cabeça]",
        },
        "hammer": {
            "bonk": "[{master} bateu de leve na sua cabeça com um martelo]",
            "rapid": "[{master} bateu em você várias vezes seguidas]",
            "easter_egg": "[{master} bateu forte na sua cabeça com um martelo]",
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
_AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES = {
    "zh": {
        "reaction_focus": "保持即时、贴合角色的反应。",
        "style_hint": "短促、自然、贴合当场反应。",
    },
    "en": {
        "reaction_focus": "Keep the reaction immediate and in character.",
        "style_hint": "Short, natural, and grounded in the moment.",
    },
    "zh-TW": {
        "reaction_focus": "保持即時、貼合角色的反應。",
        "style_hint": "短促、自然、貼合當場反應。",
    },
    "ja": {
        "reaction_focus": "反応は即時で、キャラクターらしさを保つこと。",
        "style_hint": "短く、自然で、その場に根ざした反応にすること。",
    },
    "ko": {
        "reaction_focus": "반응은 즉각적이고 캐릭터에 맞아야 한다.",
        "style_hint": "짧고 자연스럽게, 지금 순간에 붙어 있는 반응으로 간다.",
    },
    "ru": {
        "reaction_focus": "Реакция должна быть мгновенной и в образе персонажа.",
        "style_hint": "Коротко, естественно и с ощущением текущего момента.",
    },
    "es": {
        "reaction_focus": "Mantén la reacción inmediata y en personaje.",
        "style_hint": "Breve, natural y situada en el momento.",
    },
    "pt": {
        "reaction_focus": "Mantenha a reação imediata e no personagem.",
        "style_hint": "Curta, natural e situada no momento.",
    },
}
_AVATAR_INTERACTION_PROMPT_TEXT = {
    "zh": {
        "actor_line": "",
        "interaction_intro": "",
        "lollipop_intro": "",
        "compact_fields": True,
        "tool_field": "道具",
        "action_field": "动作",
        "intensity_field": "强度",
        "event_fact_field": "刚发生",
        "expression_field": "表达倾向",
        "touch_area_field": "接触位置",
        "reward_drop_line": "- 附加结果：本次互动同时触发了掉落奖励。",
        "easter_egg_line": "- 附加结果：本次互动触发了放大彩蛋。",
        "text_context_line": "- 输入框草稿：{text_context}（仅作语境参考，不是正式用户消息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只输出猫娘当下会说的一句回复。",
            "2. 回复接住上面的道具事件事实。",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "",
    },
    "zh-TW": {
        "actor_line": "",
        "interaction_intro": "",
        "lollipop_intro": "",
        "compact_fields": True,
        "tool_field": "道具",
        "action_field": "動作",
        "intensity_field": "強度",
        "event_fact_field": "剛發生",
        "expression_field": "表達傾向",
        "touch_area_field": "接觸位置",
        "reward_drop_line": "- 附加結果：本次互動同時觸發了掉落獎勵。",
        "easter_egg_line": "- 附加結果：本次互動觸發了放大彩蛋。",
        "text_context_line": "- 輸入框草稿：{text_context}（僅作語境參考，不是正式使用者訊息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只輸出貓娘當下會說的一句回覆。",
            "2. 回覆接住上面的道具事件事實。",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "",
    },
    "en": {
        "actor_line": "You are {lanlan_name}, reacting to an interaction from {master_name}.",
        "interaction_intro": "The frontend just recorded a tool interaction that has already happened. The lines below describe only the confirmed facts of this interaction; reply from those facts.",
        "lollipop_intro": "The frontend just recorded a lollipop-feeding interaction that has already happened. The lines below describe only the confirmed facts of this interaction; reply from those facts.",
        "compact_fields": True,
        "tool_field": "Tool",
        "action_field": "Action",
        "intensity_field": "Intensity",
        "event_fact_field": "Event fact",
        "expression_field": "Expression tendency",
        "touch_area_field": "Touch area",
        "reward_drop_line": "- Additional result: this interaction also triggered a reward drop.",
        "easter_egg_line": "- Additional result: this interaction triggered the enlarged easter-egg effect.",
        "text_context_line": "- Draft text in the input box: {text_context} (context only, not a formal user message)",
        "requirements_header": "Requirements:",
        "requirements": [
            "1. Output only what the catgirl would say right now.",
            "2. Reply from the facts above only; do not invent actions, distance changes, relationship escalation, or extra plot that did not happen.",
            "3. Keep it as an immediate short reaction; the exact sentence count can stay natural.",
            "4. The draft text is not a formal user message; use it only as light context if it fits naturally and never quote it verbatim.",
            "5. Do not mention coordinates, probabilities, payloads, or backend rules.",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. This is lollipop feeding, not petting, soothing, or a generic touch.",
    },
    "ja": {
        "actor_line": "あなたは{lanlan_name}で、{master_name}からのやり取りに反応しています。",
        "interaction_intro": "フロントエンドが、すでに起きた道具インタラクションを記録しました。以下には、このインタラクションで確認できた事実だけを示します。その事実に基づいて即座に反応してください。",
        "lollipop_intro": "フロントエンドが、すでに起きたペロペロキャンディを食べさせるインタラクションを記録しました。以下には、このインタラクションで確認できた事実だけを示します。その事実に基づいて即座に反応してください。",
        "compact_fields": True,
        "tool_field": "道具",
        "action_field": "動作",
        "intensity_field": "強度",
        "event_fact_field": "事実",
        "expression_field": "表現の傾向",
        "touch_area_field": "接触位置",
        "reward_drop_line": "- 追加結果: このインタラクションでは報酬ドロップも発生した。",
        "easter_egg_line": "- 追加結果: このインタラクションでは拡大イースターエッグも発生した。",
        "text_context_line": "- 入力欄の下書き: {text_context}（文脈の参考用であり、正式なユーザーメッセージではない）",
        "requirements_header": "要件:",
        "requirements": [
            "1. 今この瞬間に猫娘が口にする台詞だけを出力してください。",
            "2. 上の事実だけから反応し、起きていない動作、距離の変化、関係の進展、余計な筋書きを補わないでください。",
            "3. その場の短い反応を優先し、文数は自然で構いません。",
            "4. text_context は正式なユーザーメッセージではありません。自然な場合だけ軽く参考にし、逐語的に繰り返さないでください。",
            "5. 座標、確率、payload、バックエンドのルールには触れないでください。",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. これはペロペロキャンディを食べさせるやり取りであり、頭なで、軽い接触、なだめる行為、一般的なスキンシップとして書かないでください。",
    },
    "ko": {
        "actor_line": "너는 {lanlan_name}이고, {master_name}의 상호작용에 반응하고 있다.",
        "interaction_intro": "프런트엔드가 이미 발생한 도구 상호작용을 방금 기록했다. 아래에는 이번 상호작용에서 확인된 사실만 주어진다. 그 사실만 바탕으로 즉시 반응하라.",
        "lollipop_intro": "프런트엔드가 이미 발생한 막대사탕 먹이기 상호작용을 방금 기록했다. 아래에는 이번 상호작용에서 확인된 사실만 주어진다. 그 사실만 바탕으로 즉시 반응하라.",
        "compact_fields": True,
        "tool_field": "도구",
        "action_field": "동작",
        "intensity_field": "강도",
        "event_fact_field": "사실",
        "expression_field": "표현 경향",
        "touch_area_field": "접촉 위치",
        "reward_drop_line": "- 추가 결과: 이번 상호작용은 보상 드롭도 함께 일으켰다.",
        "easter_egg_line": "- 추가 결과: 이번 상호작용은 확대 이스터에그도 함께 일으켰다.",
        "text_context_line": "- 입력창 초안: {text_context} (맥락 참고용일 뿐, 정식 사용자 메시지는 아니다)",
        "requirements_header": "요구사항:",
        "requirements": [
            "1. 지금 이 순간 고양이 소녀가 할 말만 출력한다.",
            "2. 위 사실만 바탕으로 반응하고, 일어나지 않은 동작, 거리 변화, 관계 진전, 추가 서사를 지어내지 않는다.",
            "3. 즉각적인 짧은 반응을 우선하고, 문장 수는 자연스러우면 된다.",
            "4. text_context 는 정식 사용자 메시지가 아니다. 아주 자연스러울 때만 가볍게 참고하고, 그대로 되풀이하지 않는다.",
            "5. 좌표, 확률, payload, 백엔드 규칙은 언급하지 않는다.",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. 이것은 막대사탕 먹이기이며, 쓰다듬기, 가벼운 터치, 달래기, 일반적인 스킨십으로 쓰면 안 된다.",
    },
    "ru": {
        "actor_line": "Ты {lanlan_name} и реагируешь на взаимодействие от {master_name}.",
        "interaction_intro": "Фронтенд только что зафиксировал уже произошедшее взаимодействие с инструментом. Ниже перечислены только подтверждённые факты этого эпизода; отвечай, опираясь только на них.",
        "lollipop_intro": "Фронтенд только что зафиксировал уже произошедшее кормление леденцом. Ниже перечислены только подтверждённые факты этого эпизода; отвечай, опираясь только на них.",
        "compact_fields": True,
        "tool_field": "Инструмент",
        "action_field": "Действие",
        "intensity_field": "Интенсивность",
        "event_fact_field": "Факт события",
        "expression_field": "Тон реакции",
        "touch_area_field": "Зона касания",
        "reward_drop_line": "- Дополнительный результат: это взаимодействие также вызвало выпадение награды.",
        "easter_egg_line": "- Дополнительный результат: это взаимодействие также запустило увеличенный пасхальный эффект.",
        "text_context_line": "- Черновик в поле ввода: {text_context} (только как контекст, это не официальное сообщение пользователя)",
        "requirements_header": "Требования:",
        "requirements": [
            "1. Выводи только то, что кошкодевочка сказала бы прямо сейчас.",
            "2. Отвечай только по фактам выше; не придумывай действий, изменения дистанции, развития отношений или дополнительного сюжета, которых не было.",
            "3. Сохраняй формат короткой мгновенной реакции; точное число фраз может оставаться естественным.",
            "4. Черновик текста не является официальным сообщением пользователя; используй его лишь как лёгкий контекст, если это естественно, и никогда не цитируй дословно.",
            "5. Не упоминай координаты, вероятности, payload или правила бэкенда.",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. Это кормление леденцом, а не поглаживание, успокаивание или просто абстрактное касание.",
    },
    "es": {
        "actor_line": "Eres {lanlan_name}, reaccionando a una interacción de {master_name}.",
        "interaction_intro": "El frontend acaba de registrar una interacción con herramienta que ya ocurrió. Las líneas siguientes describen solo los hechos confirmados; responde desde esos hechos.",
        "lollipop_intro": "El frontend acaba de registrar una interacción de alimentación con piruleta que ya ocurrió. Las líneas siguientes describen solo los hechos confirmados; responde desde esos hechos.",
        "compact_fields": True,
        "tool_field": "Herramienta",
        "action_field": "Acción",
        "intensity_field": "Intensidad",
        "event_fact_field": "Hecho del evento",
        "expression_field": "Tendencia expresiva",
        "touch_area_field": "Área de toque",
        "reward_drop_line": "- Resultado adicional: esta interacción también soltó una recompensa.",
        "easter_egg_line": "- Resultado adicional: esta interacción activó el efecto easter egg ampliado.",
        "text_context_line": "- Borrador en la caja de entrada: {text_context} (solo contexto, no mensaje formal del usuario)",
        "requirements_header": "Requisitos:",
        "requirements": [
            "1. Devuelve solo lo que la chica gato diría ahora mismo.",
            "2. Responde solo desde los hechos anteriores; no inventes acciones, cambios de distancia, avances de relación ni trama adicional que no ocurrió.",
            "3. Mantén una reacción breve e inmediata; el número exacto de frases puede ser natural.",
            "4. El borrador no es un mensaje formal del usuario; úsalo solo como contexto ligero si encaja y nunca lo cites literalmente.",
            "5. No menciones coordenadas, probabilidades, payloads ni reglas del backend.",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. Esto es alimentación con piruleta, no lo conviertas en caricias, toques, consuelo o palmaditas.",
    },
    "pt": {
        "actor_line": "Você é {lanlan_name}, reagindo a uma interação de {master_name}.",
        "interaction_intro": "O frontend acabou de registrar uma interação com ferramenta que já aconteceu. As linhas abaixo descrevem apenas os fatos confirmados; responda a partir desses fatos.",
        "lollipop_intro": "O frontend acabou de registrar uma interação de alimentação com pirulito que já aconteceu. As linhas abaixo descrevem apenas os fatos confirmados; responda a partir desses fatos.",
        "compact_fields": True,
        "tool_field": "Ferramenta",
        "action_field": "Ação",
        "intensity_field": "Intensidade",
        "event_fact_field": "Fato do evento",
        "expression_field": "Tendência de expressão",
        "touch_area_field": "Área de toque",
        "reward_drop_line": "- Resultado adicional: esta interação também gerou uma recompensa.",
        "easter_egg_line": "- Resultado adicional: esta interação acionou o efeito easter egg ampliado.",
        "text_context_line": "- Rascunho na caixa de entrada: {text_context} (apenas contexto, não é mensagem formal do usuário)",
        "requirements_header": "Requisitos:",
        "requirements": [
            "1. Retorne apenas o que a garota gato diria agora.",
            "2. Responda apenas a partir dos fatos acima; não invente ações, mudanças de distância, evolução de relação ou trama extra que não aconteceu.",
            "3. Mantenha como reação curta e imediata; a contagem exata de frases pode ser natural.",
            "4. O rascunho não é uma mensagem formal do usuário; use apenas como contexto leve se couber e nunca cite literalmente.",
            "5. Não mencione coordenadas, probabilidades, payloads ou regras do backend.",
        ],
        "compact_reply_line": "",
        "lollipop_requirement": "6. Isto é alimentação com pirulito, não transforme em carinho, toque, consolo ou afago.",
    },
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

    # JSON-style quoting keeps the user draft clearly bounded when interpolated
    # into a system instruction and safely escapes embedded quotes or separators.
    return json.dumps(cleaned, ensure_ascii=False)


def _normalize_avatar_interaction_intensity(
    tool_id: str, action_id: str, intensity: str | None
) -> str:
    normalized = str(intensity or "").strip().lower()
    if normalized not in _AVATAR_INTERACTION_ALLOWED_INTENSITIES:
        return "normal"

    allowed = _AVATAR_INTERACTION_ALLOWED_INTENSITY_COMBINATIONS.get(tool_id, {}).get(
        action_id
    )
    if not allowed or normalized not in allowed:
        return "normal"

    return normalized


def _parse_avatar_interaction_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return default


def _get_avatar_interaction_payload_value(
    payload: dict, snake_key: str, camel_key: str, default=None
):
    if snake_key in payload and payload.get(snake_key) is not None:
        return payload.get(snake_key)
    if camel_key in payload and payload.get(camel_key) is not None:
        return payload.get(camel_key)
    return default


def _normalize_avatar_interaction_payload(payload: dict) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None

    interaction_id = str(
        payload.get("interaction_id") or payload.get("interactionId") or ""
    ).strip()
    tool_id = str(payload.get("tool_id") or payload.get("toolId") or "").strip().lower()
    action_id = (
        str(payload.get("action_id") or payload.get("actionId") or "").strip().lower()
    )
    target = str(payload.get("target") or "").strip().lower()

    if not interaction_id or target != "avatar":
        return None
    if tool_id not in _AVATAR_INTERACTION_ALLOWED_ACTIONS:
        return None
    if action_id not in _AVATAR_INTERACTION_ALLOWED_ACTIONS[tool_id]:
        return None

    raw_intensity = str(payload.get("intensity") or "").strip().lower()
    intensity = _normalize_avatar_interaction_intensity(
        tool_id, action_id, raw_intensity
    )

    reward_drop = (
        _parse_avatar_interaction_bool(
            _get_avatar_interaction_payload_value(
                payload, "reward_drop", "rewardDrop", False
            )
        )
        if tool_id == "fist"
        else False
    )
    easter_egg = (
        _parse_avatar_interaction_bool(
            _get_avatar_interaction_payload_value(
                payload, "easter_egg", "easterEgg", False
            )
        )
        if tool_id == "hammer"
        else False
    )
    # 归一：flag 和 intensity 任一指向彩蛋，两个都抬成彩蛋态。
    # 否则 intensity="easter_egg" + flag=False 会让 memory 落彩蛋模板，
    # 但 prompt 少了"触发放大彩蛋"这行，字段语义互相打架。
    if tool_id == "hammer" and (easter_egg or intensity == "easter_egg"):
        easter_egg = True
        intensity = _normalize_avatar_interaction_intensity(
            tool_id, action_id, "easter_egg"
        )

    raw_touch_zone = (
        str(payload.get("touch_zone") or payload.get("touchZone") or "").strip().lower()
    )
    touch_zone = (
        raw_touch_zone
        if raw_touch_zone in _AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES
        else ""
    )

    pointer_payload = payload.get("pointer")
    pointer: Optional[dict[str, float]] = None
    if isinstance(pointer_payload, dict):
        # dict.get(key, default) 只在 key 缺失时走 default；如果 client_x 显式
        # 传成 None，就不会回落到 clientX。显式判断两个键，真 None 也能降级。
        raw_x = pointer_payload.get("client_x")
        if raw_x is None:
            raw_x = pointer_payload.get("clientX")
        raw_y = pointer_payload.get("client_y")
        if raw_y is None:
            raw_y = pointer_payload.get("clientY")
        try:
            client_x = float(raw_x)
            client_y = float(raw_y)
            if math.isfinite(client_x) and math.isfinite(client_y):
                pointer = {
                    "client_x": client_x,
                    "client_y": client_y,
                }
        except (TypeError, ValueError):
            pointer = None

    timestamp = payload.get("timestamp")
    try:
        timestamp_value = int(float(timestamp))
    except (TypeError, ValueError, OverflowError):
        timestamp_value = int(time.time() * 1000)

    return {
        "interaction_id": interaction_id,
        "tool_id": tool_id,
        "action_id": action_id,
        "target": "avatar",
        "text_context": _sanitize_avatar_interaction_text_context(
            _get_avatar_interaction_payload_value(
                payload, "text_context", "textContext", ""
            )
        ),
        "timestamp": timestamp_value,
        "intensity": intensity,
        "reward_drop": reward_drop,
        "easter_egg": easter_egg,
        "touch_zone": touch_zone,
        "pointer": pointer,
    }


def _build_avatar_interaction_instruction(
    language: str | None,
    lanlan_name: str,
    master_name: str,
    payload: dict,
) -> str:
    locale = _avatar_interaction_locale(language)
    tool_id = payload["tool_id"]
    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _normalize_avatar_interaction_intensity(
        tool_id, action_id, payload.get("intensity")
    )
    if tool_id == "hammer" and payload.get("easter_egg"):
        intensity = _normalize_avatar_interaction_intensity(
            tool_id, action_id, "easter_egg"
        )
    prompt_text = _AVATAR_INTERACTION_PROMPT_TEXT.get(
        locale, _AVATAR_INTERACTION_PROMPT_TEXT["en"]
    )
    tool_label = _AVATAR_INTERACTION_TOOL_LABELS.get(
        locale, _AVATAR_INTERACTION_TOOL_LABELS["en"]
    ).get(payload["tool_id"], payload["tool_id"])
    action_label = (
        _AVATAR_INTERACTION_ACTION_LABELS.get(
            locale, _AVATAR_INTERACTION_ACTION_LABELS["en"]
        )
        .get(payload["tool_id"], {})
        .get(action_id, action_id)
    )
    intensity_label = _AVATAR_INTERACTION_INTENSITY_LABELS.get(
        locale, _AVATAR_INTERACTION_INTENSITY_LABELS["en"]
    ).get(
        intensity,
        intensity,
    )
    text_context = payload.get("text_context", "")
    touch_zone = str(payload.get("touch_zone") or "").strip().lower()
    touch_zone_label = (
        _AVATAR_INTERACTION_TOUCH_ZONE_LABELS.get(
            locale, _AVATAR_INTERACTION_TOUCH_ZONE_LABELS["en"]
        ).get(touch_zone, "")
        if tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS
        else ""
    )
    wrapper = _AVATAR_INTERACTION_SYSTEM_WRAPPER.get(
        locale, _AVATAR_INTERACTION_SYSTEM_WRAPPER["en"]
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
        reaction_profile = action_profiles.get(reward_key) or action_profiles["reward_drop"]
    else:
        reaction_profile = (
            action_profiles.get(intensity)
            or action_profiles.get("normal")
            or _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES.get(
                locale, _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES["en"]
            )
        )
    actor = _avatar_interaction_prompt_actor(locale, master_name)
    reaction_focus = str(reaction_profile["reaction_focus"]).format(
        lanlan_name=lanlan_name, master_name=actor, actor=actor
    )
    style_hint = str(reaction_profile["style_hint"]).format(
        lanlan_name=lanlan_name, master_name=actor, actor=actor
    )

    interaction_intro = (
        prompt_text["lollipop_intro"]
        if tool_id == "lollipop"
        else prompt_text["interaction_intro"]
    )
    if prompt_text.get("compact_fields"):
        compact_reply_line = prompt_text["compact_reply_line"].format(
            lanlan_name=lanlan_name, master_name=actor, actor=actor
        )
        # Empty by design: keep the compact prompt as one event fact to avoid reply-shape templates.
        if not compact_reply_line:
            return reaction_focus
        compact_separator = "" if locale in {"zh", "zh-TW", "ja"} else " "
        return f"{reaction_focus}{compact_separator}{compact_reply_line}"

    lines = [
        wrapper["prefix"],
        prompt_text["actor_line"].format(
            lanlan_name=lanlan_name, master_name=actor
        ),
    ]
    if interaction_intro:
        lines.append(interaction_intro)
    lines.extend(
        [
            f"- {prompt_text['tool_field']}: {tool_label}",
            f"- {prompt_text['action_field']}: {action_label}",
            f"- {prompt_text['intensity_field']}: {intensity_label}",
            f"- {prompt_text['event_fact_field']}: {reaction_focus}",
            f"- {prompt_text['expression_field']}: {style_hint}",
        ]
    )
    if touch_zone_label:
        lines.append(f"- {prompt_text['touch_area_field']}: {touch_zone_label}")
    if payload.get("reward_drop"):
        lines.append(prompt_text["reward_drop_line"])
    if payload.get("easter_egg"):
        lines.append(prompt_text["easter_egg_line"])
    if text_context:
        lines.append(prompt_text["text_context_line"].format(text_context=text_context))
    lines.extend(
        [
            prompt_text["requirements_header"],
            *prompt_text["requirements"],
            wrapper["suffix"],
        ]
    )
    if tool_id == "lollipop" and prompt_text.get("lollipop_requirement"):
        lines.insert(-1, prompt_text["lollipop_requirement"])
    return "\n".join(lines)


def _build_avatar_interaction_memory_note(
    language: str | None, payload: dict, master_name: str
) -> str:
    return _build_avatar_interaction_memory_meta(language, payload, master_name)[
        "memory_note"
    ]


def _build_avatar_interaction_memory_meta(
    language: str | None, payload: dict, master_name: str
) -> dict:
    """生成 avatar 互动的 memory_note + dedupe 元信息。

    ``master_name`` 必传：模板内只用 ``{master}`` 占位符表达"对 AI 做事的人"，
    禁止字面量"主人 / Your master / ご主人さま / 주인 / Хозяин"等物化称呼。
    传入空串时按 ``_AVATAR_INTERACTION_MEMORY_NOTE_MASTER_FALLBACK`` 本地化
    中性词兜底（zh="对方"、en="they" 等），同样不会回落到物化称呼。
    """
    locale = _avatar_interaction_locale(language)
    templates = _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.get(locale, {})
    fallback = _AVATAR_INTERACTION_MEMORY_NOTE_MASTER_FALLBACK
    master = str(master_name or "").strip() or fallback.get(locale, fallback["en"])
    tool_id = str(payload.get("tool_id") or "").strip().lower()
    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _normalize_avatar_interaction_intensity(
        tool_id, action_id, payload.get("intensity") or "normal"
    )
    if tool_id == "hammer" and payload.get("easter_egg"):
        intensity = _normalize_avatar_interaction_intensity(
            tool_id, action_id, "easter_egg"
        )

    memory_note = ""
    dedupe_key = tool_id or "avatar_interaction"
    dedupe_rank = 1

    if tool_id == "lollipop":
        dedupe_key = "lollipop_feed"
        if action_id == "tap_soft":
            # 前端设计上 tap_soft 只会发 rapid/burst；但 intensity normalizer 在拿到
            # 非法值时会降级成 "normal"，之前的代码会把这种异常路径落到 offer 分支，
            # 和真正的第一口 offer 互相覆盖 dedupe rank。此处按 action_id 先分，
            # 保证"连续投喂"语义始终走 tap_soft 模板。
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

    return {
        "memory_note": formatted_note,
        "memory_dedupe_key": dedupe_key,
        "memory_dedupe_rank": dedupe_rank,
    }
