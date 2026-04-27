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

from utils.language_utils import normalize_language_code, get_global_language_full


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
}
_AVATAR_INTERACTION_SYSTEM_WRAPPER = {
    "zh": {
        "prefix": "======[系统通知：以下是一次刚刚发生的道具互动，请将其视为即时互动引导，不要直接复述字段名或系统描述]======",
        "suffix": "======[系统通知结束：请直接以当前角色口吻输出即时反应]======",
    },
    "zh-TW": {
        "prefix": "======[系統通知：以下是一次剛剛發生的道具互動，請將其視為即時互動引導，不要直接複述欄位名或系統描述]======",
        "suffix": "======[系統通知結束：請直接以當前角色口吻輸出即時反應]======",
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
}
_AVATAR_INTERACTION_REACTION_PROFILES = {
    "zh": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "棒棒糖第一次完成入口接触。",
                    "style_hint": "可以带一点初次入口后的停顿感、尝味感，语气自然偏轻。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "棒棒糖已完成第一口，本次是紧接着的第二口接触。",
                    "style_hint": "比第一口更顺一点、更接得上上一拍，语气保持自然。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "第三阶段后继续投喂，前端已表现为爱心上飘；本次属于连续喂食中的一次。",
                    "style_hint": "节奏可以更快、分句可以更短，像连续被打断中的即时反应。",
                },
                "burst": {
                    "reaction_focus": "短时间内连续多次投喂，属于更高频的连续喂食。",
                    "style_hint": "允许更碎一点、更急一点，保持当场反应感。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "猫爪产生一次短促轻触。",
                    "style_hint": "短、轻、柔，根据部位差异自然带出细微区别。",
                },
                "rapid": {
                    "reaction_focus": "短时间内连续多次轻触。",
                    "style_hint": "可以更连贯一点、更快一点，保持轻触感。",
                },
                "reward_drop": {
                    "reaction_focus": "本次轻触同时触发奖励掉落。",
                    "style_hint": "先接住轻触，再顺手带一句掉落物。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "完成一次完整锤击流程并进入命中结果。",
                    "style_hint": "短促、带一点冲击停顿感，像被打断后的第一反应。",
                },
                "rapid": {
                    "reaction_focus": "短时间内再次完成锤击，已形成连续敲击。",
                    "style_hint": "可以更直接一点，体现连续敲击后的累积感。",
                },
                "burst": {
                    "reaction_focus": "连续锤击次数进一步增加，本次属于更高强度结果。",
                    "style_hint": "反应幅度可以更大一些，但仍保持即时、短促。",
                },
                "easter_egg": {
                    "reaction_focus": "本次锤击触发放大彩蛋，命中结果明显强于普通锤击。",
                    "style_hint": "可以更夸张、更有戏剧停顿，但仍保持角色口吻。",
                },
            },
        },
    },
    "en": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "The lollipop completes its first mouth contact.",
                    "style_hint": "A slight first-taste pause or flavor-noticing beat is fine; keep it naturally light.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "The first bite has already happened, and this interaction is the immediate second bite.",
                    "style_hint": "Let it feel a little smoother and more continuous than the first bite, while staying natural.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "After the third-stage state, another feeding motion happens while hearts are already floating; this is one instance within repeated feeding.",
                    "style_hint": "The rhythm can be quicker and the phrasing shorter, like an immediate response inside repeated feeding.",
                },
                "burst": {
                    "reaction_focus": "Multiple feeding motions happen in a short window, forming a higher-frequency repeated feeding event.",
                    "style_hint": "A more rushed or more fragmented rhythm is fine; keep it in-the-moment.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "The cat paw produces one short light touch.",
                    "style_hint": "Short, light, and soft, with small differences depending on the touched area.",
                },
                "rapid": {
                    "reaction_focus": "Several light touches happen within a short window.",
                    "style_hint": "It can feel a little quicker and more continuous while still staying light.",
                },
                "reward_drop": {
                    "reaction_focus": "This light touch also triggers a reward drop.",
                    "style_hint": "Catch the touch first, then mention the drop in passing.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "One full hammer-hit sequence completes and reaches the impact result.",
                    "style_hint": "Short and impact-forward, with a slight post-hit pause or interruption beat.",
                },
                "rapid": {
                    "reaction_focus": "Another hammer hit completes within a short window, forming repeated strikes.",
                    "style_hint": "It can be a little more direct and show accumulated impact from the repeated hits.",
                },
                "burst": {
                    "reaction_focus": "The number of repeated hammer hits increases further, making this a higher-intensity result.",
                    "style_hint": "A bigger reaction is fine as long as it still feels immediate and short.",
                },
                "easter_egg": {
                    "reaction_focus": "This hammer hit triggers the enlarged easter-egg effect, making the impact result stronger than normal.",
                    "style_hint": "It can be more dramatic, with a stronger pause or exclamation, while keeping the character voice.",
                },
            },
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "棒棒糖第一次完成入口接觸。",
                    "style_hint": "可以帶一點初次入口後的停頓感、嚐味感，語氣自然偏輕。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "棒棒糖已完成第一口，本次是緊接著的第二口接觸。",
                    "style_hint": "比第一口更順一點、更接得上上一拍，語氣保持自然。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "第三階段後繼續投餵，前端已表現為愛心上飄；本次屬於連續餵食中的一次。",
                    "style_hint": "節奏可以更快、分句可以更短，像連續被打斷中的即時反應。",
                },
                "burst": {
                    "reaction_focus": "短時間內連續多次投餵，屬於更高頻的連續餵食。",
                    "style_hint": "允許更碎一點、更急一點，保持當場反應感。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "貓爪產生一次短促輕觸。",
                    "style_hint": "短、輕、柔，依照部位差異自然帶出細微區別。",
                },
                "rapid": {
                    "reaction_focus": "短時間內連續多次輕觸。",
                    "style_hint": "可以更連貫一點、更快一點，保持輕觸感。",
                },
                "reward_drop": {
                    "reaction_focus": "本次輕觸同時觸發獎勵掉落。",
                    "style_hint": "先接住輕觸，再順手帶一句掉落物。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "完成一次完整槌擊流程並進入命中結果。",
                    "style_hint": "短促、帶一點衝擊停頓感，像被打斷後的第一反應。",
                },
                "rapid": {
                    "reaction_focus": "短時間內再次完成槌擊，已形成連續敲擊。",
                    "style_hint": "可以更直接一點，體現連續敲擊後的累積感。",
                },
                "burst": {
                    "reaction_focus": "連續槌擊次數進一步增加，本次屬於更高強度結果。",
                    "style_hint": "反應幅度可以更大一些，但仍保持即時、短促。",
                },
                "easter_egg": {
                    "reaction_focus": "本次槌擊觸發放大彩蛋，命中結果明顯強於普通槌擊。",
                    "style_hint": "可以更誇張、更有戲劇停頓，但仍保持角色口吻。",
                },
            },
        },
    },
    "ja": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "ペロペロキャンディが初めて口元に触れた。",
                    "style_hint": "最初のひとくち後の小さな間や味を確かめる感じがあってよい。軽く自然に。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "一口目はすでに終わっていて、今回はその直後の二口目の接触。",
                    "style_hint": "一口目より少し滑らかで、前の流れをそのまま受ける感じに。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "第三段階のあとも食べさせる動きが続き、前段ではハートが浮いている。今回は連続で食べさせる流れの一回分。",
                    "style_hint": "テンポを少し速め、文を短めにして、続けて遮られる中の即時反応らしく。",
                },
                "burst": {
                    "reaction_focus": "短時間で何度も続けて食べさせられ、より高頻度の連続給餌になっている。",
                    "style_hint": "少し途切れ気味で慌ただしくてもよいが、その場の反応感を保つ。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "猫の肉球が一度だけ短く軽く触れた。",
                    "style_hint": "短く、軽く、やわらかく。触れた部位の違いは自然ににじませる。",
                },
                "rapid": {
                    "reaction_focus": "短時間のうちに軽い接触が何度か続いた。",
                    "style_hint": "少し速く、少し連続的でもよいが、軽さは保つ。",
                },
                "reward_drop": {
                    "reaction_focus": "今回の軽い接触は報酬ドロップも同時に発生させた。",
                    "style_hint": "まず触れた感覚を受けて、そのついでに落ちたものへ軽く触れる。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "一連のハンマー打撃が完了し、命中結果に入った。",
                    "style_hint": "短く、衝撃を前に出しつつ、打たれた直後の小さな間を含めてもよい。",
                },
                "rapid": {
                    "reaction_focus": "短時間のうちにもう一度ハンマーが当たり、連続打撃になっている。",
                    "style_hint": "少し直接的にして、連続で当たる蓄積感を出してよい。",
                },
                "burst": {
                    "reaction_focus": "連続ハンマーの回数がさらに増え、今回はより強い結果になっている。",
                    "style_hint": "反応を少し大きくしてもよいが、即時で短い調子は保つ。",
                },
                "easter_egg": {
                    "reaction_focus": "今回のハンマーは拡大イースターエッグを起こし、通常より強い命中結果になっている。",
                    "style_hint": "少し大げさで劇的な間があってもよいが、キャラクターの口調は崩さない。",
                },
            },
        },
    },
    "ko": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "막대사탕이 처음으로 입가에 닿았다.",
                    "style_hint": "처음 맛보는 순간의 작은 멈칫함이나 맛을 느끼는 기색이 있어도 좋고, 가볍고 자연스럽게 간다.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "첫 한입은 이미 끝났고, 이번 상호작용은 바로 이어지는 두 번째 한입이다.",
                    "style_hint": "첫 한입보다 조금 더 자연스럽고 이어지는 느낌으로 간다.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "세 번째 단계 뒤에도 먹여 주는 동작이 이어지고 있고, 앞단에서는 하트가 떠오른 상태다. 이번은 연속 먹이기 흐름 중 한 번이다.",
                    "style_hint": "호흡을 더 빠르게 하고 문장을 더 짧게 해서, 연달아 끊기는 와중의 즉각 반응처럼 간다.",
                },
                "burst": {
                    "reaction_focus": "짧은 시간 안에 여러 번 연속으로 먹여서 더 높은 빈도의 연속 먹이기가 됐다.",
                    "style_hint": "조금 더 잘게 끊기고 급해져도 괜찮지만, 현장감은 유지한다.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "고양이 발이 한 번 짧고 가볍게 닿았다.",
                    "style_hint": "짧고, 가볍고, 부드럽게. 닿은 부위에 따라 미세한 차이를 자연스럽게 드러낸다.",
                },
                "rapid": {
                    "reaction_focus": "짧은 시간 안에 가벼운 터치가 여러 번 이어졌다.",
                    "style_hint": "조금 더 빠르고 이어져도 되지만, 가벼운 느낌은 유지한다.",
                },
                "reward_drop": {
                    "reaction_focus": "이번 가벼운 터치는 보상 드롭도 함께 발생시켰다.",
                    "style_hint": "먼저 터치를 받아들이고, 곁들여서 떨어진 보상을 한마디 언급한다.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "한 번의 완전한 망치 타격이 끝나고 명중 결과에 들어갔다.",
                    "style_hint": "짧고 충격을 앞세우되, 맞은 직후의 작은 멈춤을 넣어도 좋다.",
                },
                "rapid": {
                    "reaction_focus": "짧은 시간 안에 망치가 다시 맞아 연속 타격이 형성됐다.",
                    "style_hint": "조금 더 직접적으로, 연속해서 맞은 누적감을 드러내도 좋다.",
                },
                "burst": {
                    "reaction_focus": "연속 망치 횟수가 더 늘어나 이번은 더 강한 결과가 됐다.",
                    "style_hint": "반응 폭을 조금 키워도 되지만, 즉각적이고 짧은 느낌은 유지한다.",
                },
                "easter_egg": {
                    "reaction_focus": "이번 망치 타격은 확대 이스터에그를 일으켜 평소보다 훨씬 강한 명중 결과가 됐다.",
                    "style_hint": "조금 더 과장되고 극적인 멈춤이 있어도 되지만, 캐릭터 말투는 유지한다.",
                },
            },
        },
    },
    "ru": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "Леденец впервые коснулся рта.",
                    "style_hint": "Подойдет лёгкая пауза первого вкуса или короткое ощущение распробовать вкус; держите тон естественно мягким.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "Первый кусочек уже был, а это взаимодействие стало немедленным вторым кусочком.",
                    "style_hint": "Пусть это ощущается немного плавнее и естественнее, чем первый кусочек, сохраняя живую реакцию.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "После третьей стадии кормление продолжается, на фронтенде уже парят сердечки; этот эпизод является одной из повторяющихся подач леденца.",
                    "style_hint": "Темп может быть быстрее, а фразы короче, словно это мгновенная реакция внутри серии кормлений.",
                },
                "burst": {
                    "reaction_focus": "За короткое время происходит несколько подряд кормлений, формируя более частую серию угощения.",
                    "style_hint": "Ритм может стать более дробным и торопливым, но должен оставаться реакцией на месте.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "Кошачья лапка один раз коротко и легко касается.",
                    "style_hint": "Коротко, легко и мягко, с естественными мелкими отличиями в зависимости от зоны касания.",
                },
                "rapid": {
                    "reaction_focus": "За короткое время происходит несколько лёгких касаний подряд.",
                    "style_hint": "Можно сделать реакцию чуть быстрее и слитнее, но сохранить ощущение лёгкого касания.",
                },
                "reward_drop": {
                    "reaction_focus": "Это лёгкое касание одновременно запускает выпадение награды.",
                    "style_hint": "Сначала откликнитесь на касание, затем мимоходом упомяните выпавшую награду.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "Один полный удар молотком завершился и дошёл до результата попадания.",
                    "style_hint": "Коротко, с упором на удар и с небольшой паузой сразу после попадания.",
                },
                "rapid": {
                    "reaction_focus": "Ещё один удар молотком завершается в коротком окне, формируя серию попаданий.",
                    "style_hint": "Можно говорить чуть прямее, показывая накопившийся эффект от повторных ударов.",
                },
                "burst": {
                    "reaction_focus": "Количество подряд идущих ударов молотком увеличивается, и это уже более интенсивный результат.",
                    "style_hint": "Реакция может быть немного сильнее, если она всё ещё остаётся мгновенной и короткой.",
                },
                "easter_egg": {
                    "reaction_focus": "Этот удар молотком запускает увеличенный пасхальный эффект, поэтому результат попадания заметно сильнее обычного.",
                    "style_hint": "Можно сделать реакцию более драматичной и с более выраженной паузой, сохраняя голос персонажа.",
                },
            },
        },
    },
}
_AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES = {
    "zh": {
        "lollipop": {
            "offer": "[主人喂了你一口棒棒糖]",
            "tease": "[主人又喂了你一口棒棒糖]",
            "tap_soft": "[主人连续拿棒棒糖喂你]",
        },
        "fist": {
            "poke": "[主人摸了摸你的头]",
            "rapid": "[主人连续摸了摸你的头]",
        },
        "hammer": {
            "bonk": "[主人用锤子敲了敲你的头]",
            "rapid": "[主人连续敲了你好几下]",
            "easter_egg": "[主人用锤子重重敲了你的头]",
        },
    },
    "en": {
        "lollipop": {
            "offer": "[Your master fed you a bite of lollipop]",
            "tease": "[Your master fed you another bite of lollipop]",
            "tap_soft": "[Your master kept feeding you the lollipop]",
        },
        "fist": {
            "poke": "[Your master gave your head a gentle pat]",
            "rapid": "[Your master repeatedly patted your head]",
        },
        "hammer": {
            "bonk": "[Your master bonked your head with a hammer]",
            "rapid": "[Your master bonked you several times]",
            "easter_egg": "[Your master hit your head hard with a hammer]",
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": "[主人餵了你一口棒棒糖]",
            "tease": "[主人又餵了你一口棒棒糖]",
            "tap_soft": "[主人連續拿棒棒糖餵你]",
        },
        "fist": {
            "poke": "[主人摸了摸你的頭]",
            "rapid": "[主人連續摸了摸你的頭]",
        },
        "hammer": {
            "bonk": "[主人用槌子敲了敲你的頭]",
            "rapid": "[主人連續敲了你好幾下]",
            "easter_egg": "[主人用槌子重重敲了你的頭]",
        },
    },
    "ja": {
        "lollipop": {
            "offer": "[ご主人さまがあなたにペロペロキャンディをひとくち食べさせた]",
            "tease": "[ご主人さまがあなたにもうひとくちペロペロキャンディを食べさせた]",
            "tap_soft": "[ご主人さまがペロペロキャンディを続けて食べさせた]",
        },
        "fist": {
            "poke": "[ご主人さまがあなたの頭にそっと触れた]",
            "rapid": "[ご主人さまがあなたの頭を続けて軽く触れた]",
        },
        "hammer": {
            "bonk": "[ご主人さまがハンマーであなたの頭をこつんと叩いた]",
            "rapid": "[ご主人さまがあなたを何度か続けて叩いた]",
            "easter_egg": "[ご主人さまがハンマーであなたの頭を強く叩いた]",
        },
    },
    "ko": {
        "lollipop": {
            "offer": "[주인이 너에게 막대사탕을 한입 먹여 줬다]",
            "tease": "[주인이 너에게 막대사탕을 한입 더 먹여 줬다]",
            "tap_soft": "[주인이 막대사탕을 계속 먹여 줬다]",
        },
        "fist": {
            "poke": "[주인이 네 머리를 살짝 만져 줬다]",
            "rapid": "[주인이 네 머리를 여러 번 연달아 만져 줬다]",
        },
        "hammer": {
            "bonk": "[주인이 망치로 네 머리를 콩 쳤다]",
            "rapid": "[주인이 너를 여러 번 연달아 쳤다]",
            "easter_egg": "[주인이 망치로 네 머리를 세게 쳤다]",
        },
    },
    "ru": {
        "lollipop": {
            "offer": "[Хозяин дал тебе кусочек леденца]",
            "tease": "[Хозяин дал тебе ещё кусочек леденца]",
            "tap_soft": "[Хозяин продолжал кормить тебя леденцом]",
        },
        "fist": {
            "poke": "[Хозяин мягко погладил тебя по голове]",
            "rapid": "[Хозяин несколько раз подряд погладил тебя по голове]",
        },
        "hammer": {
            "bonk": "[Хозяин стукнул тебя молотком по голове]",
            "rapid": "[Хозяин несколько раз подряд ударил тебя]",
            "easter_egg": "[Хозяин сильно ударил тебя молотком по голове]",
        },
    },
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
}
_AVATAR_INTERACTION_PROMPT_TEXT = {
    "zh": {
        "actor_line": "你是{lanlan_name}，正在和{master_name}互动。",
        "interaction_intro": "前端刚刚记录到一次已经发生的道具互动。下面只给出这次互动确认发生的事实，请据此做出即时回应。",
        "lollipop_intro": "前端刚刚记录到一次已经发生的棒棒糖投喂互动。下面只给出这次互动确认发生的事实，请据此做出即时回应。",
        "tool_field": "道具",
        "action_field": "动作",
        "intensity_field": "强度",
        "event_fact_field": "事件事实",
        "expression_field": "表达倾向",
        "touch_area_field": "接触位置",
        "reward_drop_line": "- 附加结果：本次互动同时触发了掉落奖励。",
        "easter_egg_line": "- 附加结果：本次互动触发了放大彩蛋。",
        "text_context_line": "- 输入框草稿：{text_context}（仅作语境参考，不是正式用户消息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只输出猫娘当下会说的话，不要解释系统或复述字段名。",
            "2. 根据上面已经发生的事实作答，不补写未发生的动作、距离变化、关系升级或额外剧情。",
            "3. 以即时短回应为主，句数自然即可，不必机械统一。",
            "4. text_context 不是正式用户消息，只有在非常自然时才能轻微借用，不能逐字复述。",
            "5. 不要提范围外点击、坐标、概率、payload 或后台逻辑。",
        ],
        "lollipop_requirement": "6. 这是棒棒糖投喂，不要写成摸头、轻触、安抚或抚摸。",
    },
    "zh-TW": {
        "actor_line": "你是{lanlan_name}，正在和{master_name}互動。",
        "interaction_intro": "前端剛剛記錄到一次已經發生的道具互動。下面只給出這次互動確認發生的事實，請據此做出即時回應。",
        "lollipop_intro": "前端剛剛記錄到一次已經發生的棒棒糖投餵互動。下面只給出這次互動確認發生的事實，請據此做出即時回應。",
        "tool_field": "道具",
        "action_field": "動作",
        "intensity_field": "強度",
        "event_fact_field": "事件事實",
        "expression_field": "表達傾向",
        "touch_area_field": "接觸位置",
        "reward_drop_line": "- 附加結果：本次互動同時觸發了掉落獎勵。",
        "easter_egg_line": "- 附加結果：本次互動觸發了放大彩蛋。",
        "text_context_line": "- 輸入框草稿：{text_context}（僅作語境參考，不是正式使用者訊息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只輸出貓娘當下會說的話，不要解釋系統或複述欄位名。",
            "2. 根據上面已經發生的事實作答，不補寫未發生的動作、距離變化、關係升級或額外劇情。",
            "3. 以即時短回應為主，句數自然即可，不必機械統一。",
            "4. text_context 不是正式使用者訊息，只有在非常自然時才能輕微借用，不能逐字複述。",
            "5. 不要提範圍外點擊、座標、機率、payload 或後台邏輯。",
        ],
        "lollipop_requirement": "6. 這是棒棒糖投餵，不要寫成摸頭、輕觸、安撫或撫摸。",
    },
    "en": {
        "actor_line": "You are {lanlan_name}, reacting to an interaction from {master_name}.",
        "interaction_intro": "The frontend just recorded a tool interaction that has already happened. The lines below describe only the confirmed facts of this interaction; reply from those facts.",
        "lollipop_intro": "The frontend just recorded a lollipop-feeding interaction that has already happened. The lines below describe only the confirmed facts of this interaction; reply from those facts.",
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
        "lollipop_requirement": "6. This is lollipop feeding, not petting, soothing, or a generic touch.",
    },
    "ja": {
        "actor_line": "あなたは{lanlan_name}で、{master_name}からのやり取りに反応しています。",
        "interaction_intro": "フロントエンドが、すでに起きた道具インタラクションを記録しました。以下には、このインタラクションで確認できた事実だけを示します。その事実に基づいて即座に反応してください。",
        "lollipop_intro": "フロントエンドが、すでに起きたペロペロキャンディを食べさせるインタラクションを記録しました。以下には、このインタラクションで確認できた事実だけを示します。その事実に基づいて即座に反応してください。",
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
        "lollipop_requirement": "6. これはペロペロキャンディを食べさせるやり取りであり、頭なで、軽い接触、なだめる行為、一般的なスキンシップとして書かないでください。",
    },
    "ko": {
        "actor_line": "너는 {lanlan_name}이고, {master_name}의 상호작용에 반응하고 있다.",
        "interaction_intro": "프런트엔드가 이미 발생한 도구 상호작용을 방금 기록했다. 아래에는 이번 상호작용에서 확인된 사실만 주어진다. 그 사실만 바탕으로 즉시 반응하라.",
        "lollipop_intro": "프런트엔드가 이미 발생한 막대사탕 먹이기 상호작용을 방금 기록했다. 아래에는 이번 상호작용에서 확인된 사실만 주어진다. 그 사실만 바탕으로 즉시 반응하라.",
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
        "lollipop_requirement": "6. 이것은 막대사탕 먹이기이며, 쓰다듬기, 가벼운 터치, 달래기, 일반적인 스킨십으로 쓰면 안 된다.",
    },
    "ru": {
        "actor_line": "Ты {lanlan_name} и реагируешь на взаимодействие от {master_name}.",
        "interaction_intro": "Фронтенд только что зафиксировал уже произошедшее взаимодействие с инструментом. Ниже перечислены только подтверждённые факты этого эпизода; отвечай, опираясь только на них.",
        "lollipop_intro": "Фронтенд только что зафиксировал уже произошедшее кормление леденцом. Ниже перечислены только подтверждённые факты этого эпизода; отвечай, опираясь только на них.",
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
        "lollipop_requirement": "6. Это кормление леденцом, а не поглаживание, успокаивание или просто абстрактное касание.",
    },
}


def _avatar_interaction_locale(language: str | None) -> str:
    raw_language = language or get_global_language_full()
    normalized = normalize_language_code(raw_language, format='full')
    locale = str(normalized or "zh-CN").strip().lower()
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
    return "en"


def _sanitize_avatar_interaction_text_context(text: str, max_tokens: int | None = None) -> str:
    from utils.tokenize import truncate_to_tokens
    if max_tokens is None:
        # Lazy import 避免 config 包加载顺序问题（本文件被 config/__init__.py
        # 末尾的 re-export 路径间接导入）。
        from config import AVATAR_INTERACTION_CONTEXT_MAX_TOKENS
        max_tokens = AVATAR_INTERACTION_CONTEXT_MAX_TOKENS

    raw_text = str(text or '')
    if not raw_text:
        return ''

    normalized = raw_text.replace('\r\n', '\n').replace('\r', '\n')
    normalized = ''.join(
        char if char.isprintable() or char in {'\n', '\t', ' '} else ' '
        for char in normalized
    )

    sanitized_lines: list[str] = []
    for line in normalized.split('\n'):
        without_prefix = re.sub(r'^\s*(?:[-*•]+|\d+[.)]|[A-Za-z][.)]|#+)\s*', '', line)
        collapsed = re.sub(r'\s+', ' ', without_prefix).strip()
        if collapsed:
            sanitized_lines.append(collapsed)

    if not sanitized_lines:
        return ''

    cleaned = ' / '.join(sanitized_lines)
    safe_max_tokens = max(1, int(max_tokens))
    cleaned = truncate_to_tokens(cleaned, safe_max_tokens).rstrip()
    if not cleaned:
        return ''

    # JSON-style quoting keeps the user draft clearly bounded when interpolated
    # into a system instruction and safely escapes embedded quotes or separators.
    return json.dumps(cleaned, ensure_ascii=False)


def _normalize_avatar_interaction_intensity(tool_id: str, action_id: str, intensity: str | None) -> str:
    normalized = str(intensity or "").strip().lower()
    if normalized not in _AVATAR_INTERACTION_ALLOWED_INTENSITIES:
        return "normal"

    allowed = _AVATAR_INTERACTION_ALLOWED_INTENSITY_COMBINATIONS.get(tool_id, {}).get(action_id)
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


def _get_avatar_interaction_payload_value(payload: dict, snake_key: str, camel_key: str, default=None):
    if snake_key in payload and payload.get(snake_key) is not None:
        return payload.get(snake_key)
    if camel_key in payload and payload.get(camel_key) is not None:
        return payload.get(camel_key)
    return default


def _normalize_avatar_interaction_payload(payload: dict) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None

    interaction_id = str(payload.get("interaction_id") or payload.get("interactionId") or "").strip()
    tool_id = str(payload.get("tool_id") or payload.get("toolId") or "").strip().lower()
    action_id = str(payload.get("action_id") or payload.get("actionId") or "").strip().lower()
    target = str(payload.get("target") or "").strip().lower()

    if not interaction_id or target != "avatar":
        return None
    if tool_id not in _AVATAR_INTERACTION_ALLOWED_ACTIONS:
        return None
    if action_id not in _AVATAR_INTERACTION_ALLOWED_ACTIONS[tool_id]:
        return None

    raw_intensity = str(payload.get("intensity") or "").strip().lower()
    intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, raw_intensity)

    reward_drop = (
        _parse_avatar_interaction_bool(
            _get_avatar_interaction_payload_value(payload, "reward_drop", "rewardDrop", False)
        )
        if tool_id == "fist" else False
    )
    easter_egg = (
        _parse_avatar_interaction_bool(
            _get_avatar_interaction_payload_value(payload, "easter_egg", "easterEgg", False)
        )
        if tool_id == "hammer" else False
    )
    # 归一：flag 和 intensity 任一指向彩蛋，两个都抬成彩蛋态。
    # 否则 intensity="easter_egg" + flag=False 会让 memory 落彩蛋模板，
    # 但 prompt 少了"触发放大彩蛋"这行，字段语义互相打架。
    if tool_id == "hammer" and (easter_egg or intensity == "easter_egg"):
        easter_egg = True
        intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, "easter_egg")

    raw_touch_zone = str(payload.get("touch_zone") or payload.get("touchZone") or "").strip().lower()
    touch_zone = raw_touch_zone if raw_touch_zone in _AVATAR_INTERACTION_ALLOWED_TOUCH_ZONES else ""

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
            _get_avatar_interaction_payload_value(payload, "text_context", "textContext", "")
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
    intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, payload.get("intensity"))
    if tool_id == "hammer" and payload.get("easter_egg"):
        intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, "easter_egg")
    prompt_text = _AVATAR_INTERACTION_PROMPT_TEXT.get(locale, _AVATAR_INTERACTION_PROMPT_TEXT["en"])
    tool_label = _AVATAR_INTERACTION_TOOL_LABELS.get(locale, _AVATAR_INTERACTION_TOOL_LABELS["en"]).get(payload["tool_id"], payload["tool_id"])
    action_label = (
        _AVATAR_INTERACTION_ACTION_LABELS.get(locale, _AVATAR_INTERACTION_ACTION_LABELS["en"])
        .get(payload["tool_id"], {})
        .get(action_id, action_id)
    )
    intensity_label = _AVATAR_INTERACTION_INTENSITY_LABELS.get(locale, _AVATAR_INTERACTION_INTENSITY_LABELS["en"]).get(
        intensity,
        intensity,
    )
    text_context = payload.get("text_context", "")
    touch_zone = str(payload.get("touch_zone") or "").strip().lower()
    touch_zone_label = (
        _AVATAR_INTERACTION_TOUCH_ZONE_LABELS.get(locale, _AVATAR_INTERACTION_TOUCH_ZONE_LABELS["en"]).get(touch_zone, "")
        if tool_id in _AVATAR_INTERACTION_TOUCH_ZONE_PROMPT_TOOLS else ""
    )
    wrapper = _AVATAR_INTERACTION_SYSTEM_WRAPPER.get(locale, _AVATAR_INTERACTION_SYSTEM_WRAPPER["en"])
    action_profiles = _AVATAR_INTERACTION_REACTION_PROFILES.get(locale, _AVATAR_INTERACTION_REACTION_PROFILES["en"]).get(tool_id, {}).get(action_id, {})
    if payload.get("reward_drop") and action_profiles.get("reward_drop"):
        reaction_profile = action_profiles["reward_drop"]
    else:
        reaction_profile = (
            action_profiles.get(intensity)
            or action_profiles.get("normal")
            or _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES.get(locale, _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES["en"])
        )

    interaction_intro = prompt_text["lollipop_intro"] if tool_id == "lollipop" else prompt_text["interaction_intro"]
    lines = [
        wrapper["prefix"],
        prompt_text["actor_line"].format(lanlan_name=lanlan_name, master_name=master_name),
        interaction_intro,
        f"- {prompt_text['tool_field']}: {tool_label}",
        f"- {prompt_text['action_field']}: {action_label}",
        f"- {prompt_text['intensity_field']}: {intensity_label}",
        f"- {prompt_text['event_fact_field']}: {reaction_profile['reaction_focus']}",
        f"- {prompt_text['expression_field']}: {reaction_profile['style_hint']}",
    ]
    if touch_zone_label:
        lines.append(f"- {prompt_text['touch_area_field']}: {touch_zone_label}")
    if payload.get("reward_drop"):
        lines.append(prompt_text["reward_drop_line"])
    if payload.get("easter_egg"):
        lines.append(prompt_text["easter_egg_line"])
    if text_context:
        lines.append(prompt_text["text_context_line"].format(text_context=text_context))
    lines.extend([
        prompt_text["requirements_header"],
        *prompt_text["requirements"],
        wrapper["suffix"],
    ])
    if tool_id == "lollipop":
        lines.insert(-1, prompt_text["lollipop_requirement"])
    return "\n".join(lines)


def _build_avatar_interaction_memory_note(language: str | None, payload: dict) -> str:
    return _build_avatar_interaction_memory_meta(language, payload)["memory_note"]


def _build_avatar_interaction_memory_meta(language: str | None, payload: dict) -> dict:
    locale = _avatar_interaction_locale(language)
    templates = _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.get(locale, {})
    tool_id = str(payload.get("tool_id") or "").strip().lower()
    action_id = str(payload.get("action_id") or "").strip().lower()
    intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, payload.get("intensity") or "normal")
    if tool_id == "hammer" and payload.get("easter_egg"):
        intensity = _normalize_avatar_interaction_intensity(tool_id, action_id, "easter_egg")

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
            memory_note = templates.get("fist", {}).get("rapid", templates.get("fist", {}).get("poke", ""))
            dedupe_rank = 3 if intensity == "burst" else 2
        else:
            memory_note = templates.get("fist", {}).get("poke", "")
            dedupe_rank = 1
    elif tool_id == "hammer":
        dedupe_key = "hammer_bonk"
        if intensity == "easter_egg":
            memory_note = templates.get("hammer", {}).get("easter_egg", templates.get("hammer", {}).get("bonk", ""))
            dedupe_rank = 4
        elif intensity in {"rapid", "burst"}:
            memory_note = templates.get("hammer", {}).get("rapid", templates.get("hammer", {}).get("bonk", ""))
            dedupe_rank = 3 if intensity == "burst" else 2
        else:
            memory_note = templates.get("hammer", {}).get("bonk", "")
            dedupe_rank = 1
    else:
        memory_note = templates.get(tool_id, {}).get(action_id, "")

    return {
        "memory_note": str(memory_note or "").strip(),
        "memory_dedupe_key": dedupe_key,
        "memory_dedupe_rank": dedupe_rank,
    }
