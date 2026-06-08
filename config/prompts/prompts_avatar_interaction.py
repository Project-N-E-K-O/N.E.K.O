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
            "offer": "轻量触发",
            "tease": "后续触发",
            "tap_soft": "重复触发",
        },
        "fist": {
            "poke": "轻量触发",
        },
        "hammer": {
            "bonk": "玩具触发",
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": "輕量觸發",
            "tease": "後續觸發",
            "tap_soft": "重複觸發",
        },
        "fist": {
            "poke": "輕量觸發",
        },
        "hammer": {
            "bonk": "玩具觸發",
        },
    },
    "en": {
        "lollipop": {
            "offer": "light cue",
            "tease": "follow-up cue",
            "tap_soft": "repeated cue",
        },
        "fist": {
            "poke": "light cue",
        },
        "hammer": {
            "bonk": "toy cue",
        },
    },
    "ja": {
        "lollipop": {
            "offer": "軽い合図",
            "tease": "後続の合図",
            "tap_soft": "反復の合図",
        },
        "fist": {
            "poke": "軽い合図",
        },
        "hammer": {
            "bonk": "おもちゃの合図",
        },
    },
    "ko": {
        "lollipop": {
            "offer": "가벼운 신호",
            "tease": "후속 신호",
            "tap_soft": "반복 신호",
        },
        "fist": {
            "poke": "가벼운 신호",
        },
        "hammer": {
            "bonk": "장난감 신호",
        },
    },
    "ru": {
        "lollipop": {
            "offer": "лёгкий сигнал",
            "tease": "последующий сигнал",
            "tap_soft": "повторный сигнал",
        },
        "fist": {
            "poke": "лёгкий сигнал",
        },
        "hammer": {
            "bonk": "игрушечный сигнал",
        },
    },
    "es": {
        "lollipop": {
            "offer": "señal ligera",
            "tease": "señal posterior",
            "tap_soft": "señal repetida",
        },
        "fist": {"poke": "señal ligera"},
        "hammer": {"bonk": "señal de juguete"},
    },
    "pt": {
        "lollipop": {
            "offer": "sinal leve",
            "tease": "sinal posterior",
            "tap_soft": "sinal repetido",
        },
        "fist": {"poke": "sinal leve"},
        "hammer": {"bonk": "sinal de brinquedo"},
    },
}
_AVATAR_INTERACTION_INTENSITY_LABELS = {
    "zh": {
        "normal": "普通提示",
        "rapid": "重复提示",
        "burst": "高频提示",
        "easter_egg": "特殊提示",
    },
    "zh-TW": {
        "normal": "普通提示",
        "rapid": "重複提示",
        "burst": "高頻提示",
        "easter_egg": "特殊提示",
    },
    "en": {
        "normal": "ordinary cue",
        "rapid": "repeated cue",
        "burst": "high-frequency cue",
        "easter_egg": "special cue",
    },
    "ja": {
        "normal": "通常の合図",
        "rapid": "反復の合図",
        "burst": "高頻度の合図",
        "easter_egg": "特殊な合図",
    },
    "ko": {
        "normal": "일반 신호",
        "rapid": "반복 신호",
        "burst": "고빈도 신호",
        "easter_egg": "특수 신호",
    },
    "ru": {
        "normal": "обычный сигнал",
        "rapid": "повторный сигнал",
        "burst": "частый сигнал",
        "easter_egg": "особый сигнал",
    },
    "es": {
        "normal": "señal normal",
        "rapid": "señal repetida",
        "burst": "señal frecuente",
        "easter_egg": "señal especial",
    },
    "pt": {
        "normal": "sinal comum",
        "rapid": "sinal repetido",
        "burst": "sinal frequente",
        "easter_egg": "sinal especial",
    },
}
_AVATAR_INTERACTION_TOUCH_ZONE_LABELS = {
    "zh": {
        "ear": "已记录（不要提具体位置）",
        "head": "已记录（不要提具体位置）",
        "face": "已记录（不要提具体位置）",
        "body": "已记录（不要提具体位置）",
    },
    "zh-TW": {
        "ear": "已記錄（不要提具體位置）",
        "head": "已記錄（不要提具體位置）",
        "face": "已記錄（不要提具體位置）",
        "body": "已記錄（不要提具體位置）",
    },
    "en": {
        "ear": "recorded internally (do not mention the area)",
        "head": "recorded internally (do not mention the area)",
        "face": "recorded internally (do not mention the area)",
        "body": "recorded internally (do not mention the area)",
    },
    "ja": {
        "ear": "内部記録済み（具体的な位置は言わない）",
        "head": "内部記録済み（具体的な位置は言わない）",
        "face": "内部記録済み（具体的な位置は言わない）",
        "body": "内部記録済み（具体的な位置は言わない）",
    },
    "ko": {
        "ear": "내부 기록됨(구체적 위치 언급하지 않음)",
        "head": "내부 기록됨(구체적 위치 언급하지 않음)",
        "face": "내부 기록됨(구체적 위치 언급하지 않음)",
        "body": "내부 기록됨(구체적 위치 언급하지 않음)",
    },
    "ru": {
        "ear": "записано внутренне (не упоминать конкретную зону)",
        "head": "записано внутренне (не упоминать конкретную зону)",
        "face": "записано внутренне (не упоминать конкретную зону)",
        "body": "записано внутренне (не упоминать конкретную зону)",
    },
    "es": {
        "ear": "registrada internamente (no menciones el área concreta)",
        "head": "registrada internamente (no menciones el área concreta)",
        "face": "registrada internamente (no menciones el área concreta)",
        "body": "registrada internamente (no menciones el área concreta)",
    },
    "pt": {
        "ear": "registrada internamente (não mencione a área concreta)",
        "head": "registrada internamente (não mencione a área concreta)",
        "face": "registrada internamente (não mencione a área concreta)",
        "body": "registrada internamente (não mencione a área concreta)",
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
                    "reaction_focus": "本轮是棒棒糖轻量触发。",
                    "style_hint": "接当前聊天语气说一句轻短回应，可以接受、轻轻嗔怪或故意装正经；不必提味觉、投喂请求或身体反应。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "本轮是棒棒糖后续触发。",
                    "style_hint": "像上一句后的自然续话，可以省略主语和称呼；不要复述轮次事实，也不要回到同一套求停或身体夸张。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "本轮是棒棒糖重复触发。",
                    "style_hint": "可以半句、轻怼、装作不理或转移话题。不要描述投喂现象，也不要写成叹词加身体状态加求停的固定模板。",
                },
                "burst": {
                    "reaction_focus": "本轮是棒棒糖高频触发。",
                    "style_hint": "只比普通轮次稍微更急一点；可以短促打断、认输或耍赖。不要总结次数，也不要靠身体承受夸张来表现强度。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "本轮是猫爪轻量触发。",
                    "style_hint": "接当前聊天语气说一句被逗到的短回应，可以轻怼、装凶、转移话题或故意端着；不必提接触感受、制止语或具体位置。",
                },
                "rapid": {
                    "reaction_focus": "本轮是猫爪重复触发。",
                    "style_hint": "连续时更像熟人聊天里的短促应付，可以只回半句、轻轻嫌弃或把话题拉回来；避免同一套反应词、具体位置、动作过程、毛发或晃动感。",
                },
                "reward_drop": {
                    "reaction_focus": "本轮同时带有掉落提示。",
                    "style_hint": "奖励只是内部提示，可以完全不提；若自然提到，只用泛称，不编具体物品名，不写成发现物品的播报。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "本轮是玩具锤轻量触发。",
                    "style_hint": "轻喜剧短回应，可以装凶、吐槽或假装生气；不必提突发性、道具动作、痛感或具体动作。",
                },
                "rapid": {
                    "reaction_focus": "本轮是玩具锤后续触发。",
                    "style_hint": "带一点熟人间的累积感即可；不要写成和第一次同样的惊叫，也不要复述事件。避免反复写具体位置、动作过程或毛发变化。",
                },
                "burst": {
                    "reaction_focus": "本轮是玩具锤高频触发。",
                    "style_hint": "反应可以稍大，但仍是聊天里的短吐槽或装作记仇；不要写真实暴力后果，也不要用具体位置、毛发变化或麻木感来凑变化。",
                },
                "easter_egg": {
                    "reaction_focus": "本轮是玩具锤特殊触发。",
                    "style_hint": "可以更戏剧一点，但仍是一句符合角色口吻的短台词；不要写严重受伤。",
                },
            },
        },
    },
    "en": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "This round is a light lollipop cue.",
                    "style_hint": "Continue the current chat tone with one light short reply; it may accept, lightly complain, or act composed. No need to mention sweetness, feeding, slowing down, or body reaction.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "This round is a follow-up lollipop cue.",
                    "style_hint": "Make it a natural continuation after the previous line; subject and address may be omitted. Do not restate round facts or return to the same stop-request or body-exaggeration routine.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "This round is a repeated lollipop cue.",
                    "style_hint": "A half-line, light jab, pretending to ignore it, or changing the topic is fine. Do not describe the feeding phenomenon, and avoid fixed patterns built from an interjection, body state, and stop request.",
                },
                "burst": {
                    "reaction_focus": "This round is a high-frequency lollipop cue.",
                    "style_hint": "Make it only a little more urgent than ordinary rounds; a quick interruption, surrender, or playful cheating is fine. Do not summarize the count or show intensity through body-strain exaggeration.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "This round is a light cat-paw cue.",
                    "style_hint": "Continue the current chat tone with one short reply from being teased; it may lightly jab back, act fierce, change the topic, or pretend to stay composed. No need to mention tickles, petting, teasing, or a concrete area.",
                },
                "rapid": {
                    "reaction_focus": "This round is a repeated cat-paw cue.",
                    "style_hint": "For repeats, make it like a familiar short chat reply: a half-line, light dislike, or pulling the topic back. Avoid the same reaction-word routine, concrete areas, action processes, fur state, or dizziness.",
                },
                "reward_drop": {
                    "reaction_focus": "This round also has a reward cue.",
                    "style_hint": "The reward is only an internal cue and may be ignored. If mentioned naturally, use a generic reward reference only; do not invent a concrete item or make it an item-discovery announcement.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "This round is a light toy-hammer cue.",
                    "style_hint": "Use a light-comedy short reply: act fierce, tease back, or pretend to be mad. No need to mention suddenness, hitting, hammering, pain, or concrete action.",
                },
                "rapid": {
                    "reaction_focus": "This round is a follow-up toy-hammer cue.",
                    "style_hint": "A little familiar accumulated annoyance is enough; do not write the same startled line as the first round, and do not restate the event. Avoid repeating concrete area, action process, or fur change.",
                },
                "burst": {
                    "reaction_focus": "This round is a high-frequency toy-hammer cue.",
                    "style_hint": "The reaction can be a little bigger, but still a short chat retort or pretend grudge. Do not write real violent consequences or fill variation with concrete area, fur change, or numbness imagery.",
                },
                "easter_egg": {
                    "reaction_focus": "This round is a special toy-hammer cue.",
                    "style_hint": "It can be more dramatic, but still one short line in character; do not write serious injury.",
                },
            },
        },
    },
    "zh-TW": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "本輪是棒棒糖輕量觸發。",
                    "style_hint": "接當前聊天語氣說一句輕短回應，可以接受、輕輕嗔怪或故意裝正經；不必提味覺、投餵請求或身體反應。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "本輪是棒棒糖後續觸發。",
                    "style_hint": "像上一句後的自然續話，可以省略主語和稱呼；不要複述輪次事實，也不要回到同一套求停或身體誇張。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "本輪是棒棒糖重複觸發。",
                    "style_hint": "可以半句、輕懟、裝作不理或轉移話題。不要描述投餵現象，也不要寫成嘆詞加身體狀態加求停的固定模板。",
                },
                "burst": {
                    "reaction_focus": "本輪是棒棒糖高頻觸發。",
                    "style_hint": "只比普通輪次稍微更急一點；可以短促打斷、認輸或耍賴。不要總結次數，也不要靠身體承受誇張來表現強度。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "本輪是貓爪輕量觸發。",
                    "style_hint": "接當前聊天語氣說一句被逗到的短回應，可以輕懟、裝凶、轉移話題或故意端著；不必提接觸感受、制止語或具體位置。",
                },
                "rapid": {
                    "reaction_focus": "本輪是貓爪重複觸發。",
                    "style_hint": "連續時更像熟人聊天裡的短促應付，可以只回半句、輕輕嫌棄或把話題拉回來；避免同一套反應詞、具體位置、動作過程、毛髮或晃動感。",
                },
                "reward_drop": {
                    "reaction_focus": "本輪同時帶有掉落提示。",
                    "style_hint": "獎勵只是內部提示，可以完全不提；若自然提到，只用泛稱，不編具體物品名，不寫成發現物品的播報。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "本輪是玩具槌輕量觸發。",
                    "style_hint": "輕喜劇短回應，可以裝凶、吐槽或假裝生氣；不必提突發性、道具動作、痛感或具體動作。",
                },
                "rapid": {
                    "reaction_focus": "本輪是玩具槌後續觸發。",
                    "style_hint": "帶一點熟人間的累積感即可；不要寫成和第一次同樣的驚叫，也不要複述事件。避免反覆寫具體位置、動作過程或毛髮變化。",
                },
                "burst": {
                    "reaction_focus": "本輪是玩具槌高頻觸發。",
                    "style_hint": "反應可以稍大，但仍是聊天裡的短吐槽或裝作記仇；不要寫真實暴力後果，也不要用具體位置、毛髮變化或麻木感來湊變化。",
                },
                "easter_egg": {
                    "reaction_focus": "本輪是玩具槌特殊觸發。",
                    "style_hint": "可以更戲劇一點，但仍是一句符合角色口吻的短台詞；不要寫嚴重受傷。",
                },
            },
        },
    },
    "ja": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "このラウンドはペロペロキャンディの軽い合図。",
                    "style_hint": "今のチャットの調子に続く軽い短い返事にする。受ける、少し拗ねる、平気なふりをする程度でよい。味覚、要求、身体反応を言う必要はない。",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "このラウンドはペロペロキャンディの後続合図。",
                    "style_hint": "前の一言から自然につなげる。主語や呼びかけは省いてよい。ラウンド事実を復唱せず、同じ停止要求や身体誇張に戻らない。",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "このラウンドはペロペロキャンディの反復合図。",
                    "style_hint": "半端な一言、軽い言い返し、無視するふり、話題そらしでよい。現象を描写せず、感嘆詞と身体状態と停止要求の固定型にしない。",
                },
                "burst": {
                    "reaction_focus": "このラウンドはペロペロキャンディの高頻度合図。",
                    "style_hint": "普通のラウンドより少しだけ急ぐ程度にする。短く遮る、降参する、ずるく甘えるくらいでよい。回数をまとめず、身体誇張で強さを出さない。",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "このラウンドは猫の肉球の軽い合図。",
                    "style_hint": "今のチャットの調子に続く、からかわれた短い返事にする。軽く言い返す、強がる、話題を戻す、平気なふりをする程度でよい。接触感、制止語、具体位置を言う必要はない。",
                },
                "rapid": {
                    "reaction_focus": "このラウンドは猫の肉球の反復合図。",
                    "style_hint": "連続時は親しいチャットで短く受け流す感じにする。半句、軽い嫌がり、話題を戻す返事でよい。同じ反応語、具体位置、動作過程、毛や揺れの感覚を避ける。",
                },
                "reward_drop": {
                    "reaction_focus": "このラウンドには報酬の合図もある。",
                    "style_hint": "報酬は内部合図なので無視してよい。自然に触れる場合も汎用的に言い、具体的な物品名を作らず、発見報告にしない。",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "このラウンドはおもちゃのハンマーの軽い合図。",
                    "style_hint": "軽いコメディの短い返事にする。強がる、ツッコむ、怒ったふりでよい。突発性、道具動作、痛感、具体動作を言う必要はない。",
                },
                "rapid": {
                    "reaction_focus": "このラウンドはおもちゃのハンマーの後続合図。",
                    "style_hint": "親しい間柄の少し積もった感じだけでよい。初回と同じ驚きにせず、イベントを復唱しない。具体位置、動作過程、毛の変化を避ける。",
                },
                "burst": {
                    "reaction_focus": "このラウンドはおもちゃのハンマーの高頻度合図。",
                    "style_hint": "少し大きくてもよいが、チャット内の短いツッコミや根に持つふりに留める。現実の暴力結果や具体位置、毛の変化、しびれで変化を作らない。",
                },
                "easter_egg": {
                    "reaction_focus": "このラウンドはおもちゃのハンマーの特殊合図。",
                    "style_hint": "少し劇的でもよいが、キャラクター口調の短い一言にする。深刻なけがを書かない。",
                },
            },
        },
    },
    "ko": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "이번 라운드는 막대사탕의 가벼운 신호다.",
                    "style_hint": "현재 채팅 말투를 이어 가는 가볍고 짧은 답으로 쓴다. 받아 주기, 살짝 삐치기, 괜찮은 척하기 정도면 된다. 단맛, 먹이기, 천천히, 몸 반응을 말할 필요는 없다.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "이번 라운드는 막대사탕의 후속 신호다.",
                    "style_hint": "이전 한마디에서 자연스럽게 이어 간다. 주어나 호칭은 생략해도 된다. 라운드 사실을 되풀이하지 않고, 같은 멈춤 요구나 몸 과장으로 돌아가지 않는다.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "이번 라운드는 막대사탕의 반복 신호다.",
                    "style_hint": "반쯤 끊긴 말, 가벼운 받아치기, 못 들은 척, 화제 돌리기도 괜찮다. 현상을 묘사하지 않고, 감탄사와 몸 상태와 멈춤 요구를 붙인 고정형으로 쓰지 않는다.",
                },
                "burst": {
                    "reaction_focus": "이번 라운드는 막대사탕의 고빈도 신호다.",
                    "style_hint": "보통 라운드보다 아주 조금만 급하게 한다. 짧게 끊기, 항복하기, 장난스럽게 버티기 정도면 된다. 횟수를 요약하지 않고 몸 과장으로 강도를 만들지 않는다.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "이번 라운드는 고양이 발의 가벼운 신호다.",
                    "style_hint": "현재 채팅 말투를 이어 가며 놀림받은 짧은 답으로 쓴다. 가볍게 받아치기, 센 척하기, 화제 돌리기, 태연한 척하기 정도면 된다. 간지러움, 만짐, 장난치지 말라는 말, 구체적 위치를 말할 필요는 없다.",
                },
                "rapid": {
                    "reaction_focus": "이번 라운드는 고양이 발의 반복 신호다.",
                    "style_hint": "연속일 때는 친한 채팅에서 짧게 받아넘기는 느낌으로 쓴다. 반마디, 가벼운 싫은 척, 화제 되돌리기가 좋다. 같은 반응어, 구체적 위치, 동작 과정, 털이나 흔들림 감각을 피한다.",
                },
                "reward_drop": {
                    "reaction_focus": "이번 라운드에는 보상 신호도 있다.",
                    "style_hint": "보상은 내부 신호이므로 무시해도 된다. 자연스럽게 언급할 때도 일반 표현만 쓰고, 구체적 물건 이름을 만들거나 발견 보고처럼 쓰지 않는다.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "이번 라운드는 장난감 망치의 가벼운 신호다.",
                    "style_hint": "가벼운 코미디의 짧은 답으로 쓴다. 센 척하기, 받아치기, 화난 척이면 된다. 갑작스러움, 때림, 망치, 아픔, 구체 동작을 말할 필요는 없다.",
                },
                "rapid": {
                    "reaction_focus": "이번 라운드는 장난감 망치의 후속 신호다.",
                    "style_hint": "친한 사이의 조금 쌓인 느낌만 주면 된다. 첫 번째와 같은 놀람으로 쓰지 않고, 이벤트를 되풀이하지 않는다. 구체 위치, 동작 과정, 털 변화를 피한다.",
                },
                "burst": {
                    "reaction_focus": "이번 라운드는 장난감 망치의 고빈도 신호다.",
                    "style_hint": "조금 커져도 되지만 채팅 안의 짧은 받아치기나 삐진 척으로 둔다. 실제 폭력 결과나 구체 위치, 털 변화, 저림으로 변화를 만들지 않는다.",
                },
                "easter_egg": {
                    "reaction_focus": "이번 라운드는 장난감 망치의 특수 신호다.",
                    "style_hint": "조금 더 극적이어도 되지만 캐릭터 말투의 짧은 한마디로 둔다. 심각한 부상은 쓰지 않는다.",
                },
            },
        },
    },
    "ru": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "Этот раунд — лёгкий сигнал леденца.",
                    "style_hint": "Продолжи текущий тон чата одним лёгким коротким ответом: принять, чуть обидеться или сделать вид, что всё нормально. Не нужно упоминать сладость, кормление, просьбу медленнее или телесную реакцию.",
                },
            },
            "tease": {
                "normal": {
                    "reaction_focus": "Этот раунд — последующий сигнал леденца.",
                    "style_hint": "Сделай естественное продолжение прошлой реплики; подлежащее и обращение можно опустить. Не пересказывай факт раунда и не возвращайся к той же просьбе остановиться или телесному преувеличению.",
                },
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "Этот раунд — повторный сигнал леденца.",
                    "style_hint": "Можно дать обрывок, лёгкий ответный выпад, вид, что она игнорирует, или смену темы. Не описывай явление и не делай фиксированный шаблон из междометия, состояния тела и просьбы остановиться.",
                },
                "burst": {
                    "reaction_focus": "Этот раунд — частый сигнал леденца.",
                    "style_hint": "Пусть это будет лишь немного срочнее обычного раунда: короткое перебивание, сдача или игривое упрямство. Не суммируй количество и не показывай силу через телесное преувеличение.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "Этот раунд — лёгкий сигнал кошачьей лапки.",
                    "style_hint": "Продолжи текущий тон чата коротким ответом на поддразнивание: легко ответить, сделать вид, что грозная, вернуть тему или сохранить видимость спокойствия. Не нужно упоминать щекотку, касание, просьбу не баловаться или конкретную зону.",
                },
                "rapid": {
                    "reaction_focus": "Этот раунд — повторный сигнал кошачьей лапки.",
                    "style_hint": "При повторе это похоже на короткое отмахивание в близком чате: обрывок, лёгное недовольство или возврат к теме. Избегай тех же слов реакции, конкретной зоны, процесса действия, шерсти или ощущения покачивания.",
                },
                "reward_drop": {
                    "reaction_focus": "В этом раунде также есть сигнал награды.",
                    "style_hint": "Награда — внутренний сигнал, её можно игнорировать. Если упоминание естественно, говори только обобщённо; не выдумывай конкретный предмет и не делай объявление о находке.",
                },
            },
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "Этот раунд — лёгкий сигнал игрушечного молотка.",
                    "style_hint": "Короткий лёгко-комедийный ответ: сделать вид, что грозная, подколоть в ответ или притвориться сердитой. Не нужно упоминать внезапность, удар, молоток, боль или конкретное действие.",
                },
                "rapid": {
                    "reaction_focus": "Этот раунд — последующий сигнал игрушечного молотка.",
                    "style_hint": "Достаточно лёгкого накопления между близкими людьми. Не повторяй первое удивление и не пересказывай событие. Избегай конкретной зоны, процесса действия и изменений шерсти.",
                },
                "burst": {
                    "reaction_focus": "Этот раунд — частый сигнал игрушечного молотка.",
                    "style_hint": "Можно чуть сильнее, но всё ещё как короткий чат-ответ или притворная обида. Не пиши реальные последствия насилия и не создавай разнообразие через конкретную зону, шерсть или онемение.",
                },
                "easter_egg": {
                    "reaction_focus": "Этот раунд — особый сигнал игрушечного молотка.",
                    "style_hint": "Можно сделать реакцию драматичнее, но всё ещё одной короткой фразой в голосе персонажа. Не пишите серьёзную травму.",
                },
            },
        },
    },
    "es": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "Esta ronda es una señal ligera de piruleta.",
                    "style_hint": "Continúa el tono actual del chat con una respuesta corta y ligera; puede aceptar, quejarse suave o hacerse la seria. No hace falta mencionar dulzor, alimentación, ir más despacio ni reacción corporal.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "Esta ronda es una señal posterior de piruleta.",
                    "style_hint": "Haz una continuación natural de la frase anterior; puede omitir sujeto y tratamiento. No repitas hechos de la ronda ni vuelvas a la misma petición de parar o exageración corporal.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "Esta ronda es una señal repetida de piruleta.",
                    "style_hint": "Puede ser media frase, una pulla suave, fingir que lo ignora o cambiar de tema. No describas el fenómeno ni uses el patrón fijo de interjección, estado corporal y petición de parar.",
                },
                "burst": {
                    "reaction_focus": "Esta ronda es una señal frecuente de piruleta.",
                    "style_hint": "Que sea solo un poco más urgente que una ronda normal: interrupción breve, rendirse o hacer trampa en broma. No resumas la cuenta ni muestres intensidad con exageración corporal.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "Esta ronda es una señal ligera de patita de gato.",
                    "style_hint": "Continúa el tono actual del chat con una respuesta corta a la provocación: devolver la pulla, hacerse la brava, cambiar de tema o fingir compostura. No hace falta mencionar cosquillas, tocar, no molestar ni área concreta.",
                },
                "rapid": {
                    "reaction_focus": "Esta ronda es una señal repetida de patita de gato.",
                    "style_hint": "En repeticiones, que suene a respuesta corta de confianza: media frase, disgusto leve o volver al tema. Evita las mismas palabras de reacción, áreas concretas, proceso de acción, pelo o sensación de mareo.",
                },
                "reward_drop": {
                    "reaction_focus": "Esta ronda también tiene una señal de recompensa.",
                    "style_hint": "La recompensa es una señal interna y puede ignorarse. Si se menciona de forma natural, usa solo una referencia genérica; no inventes un objeto concreto ni lo conviertas en anuncio de hallazgo.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "Esta ronda es una señal ligera de martillo de juguete.",
                    "style_hint": "Usa una respuesta corta de comedia ligera: hacerse la brava, devolver la broma o fingir enojo. No hace falta mencionar sorpresa, golpe, martillo, dolor ni acción concreta.",
                },
                "rapid": {
                    "reaction_focus": "Esta ronda es una señal posterior de martillo de juguete.",
                    "style_hint": "Basta una acumulación ligera de confianza. No repitas el sobresalto inicial ni repitas el evento. Evita área concreta, proceso de acción o cambios de pelo.",
                },
                "burst": {
                    "reaction_focus": "Esta ronda es una señal frecuente de martillo de juguete.",
                    "style_hint": "Puede ser algo mayor, pero sigue siendo una réplica corta de chat o rencor fingido. No escribas consecuencias reales ni uses área concreta, pelo o entumecimiento para variar.",
                },
                "easter_egg": {
                    "reaction_focus": "Esta ronda es una señal especial de martillo de juguete.",
                    "style_hint": "Puede ser más dramático, pero sigue siendo una frase corta con la voz del personaje. No escribas una lesión grave.",
                },
            }
        },
    },
    "pt": {
        "lollipop": {
            "offer": {
                "normal": {
                    "reaction_focus": "Esta rodada é um sinal leve de pirulito.",
                    "style_hint": "Continue o tom atual do chat com uma resposta curta e leve; pode aceitar, reclamar de leve ou fingir seriedade. Não precisa mencionar doçura, alimentação, ir mais devagar nem reação corporal.",
                }
            },
            "tease": {
                "normal": {
                    "reaction_focus": "Esta rodada é um sinal posterior de pirulito.",
                    "style_hint": "Faça uma continuação natural da fala anterior; sujeito e tratamento podem ser omitidos. Não repita fatos da rodada nem volte ao mesmo pedido de parar ou exagero corporal.",
                }
            },
            "tap_soft": {
                "rapid": {
                    "reaction_focus": "Esta rodada é um sinal repetido de pirulito.",
                    "style_hint": "Pode ser meia frase, provocação leve, fingir que ignora ou mudar de assunto. Não descreva o fenômeno nem use o padrão fixo de interjeição, estado corporal e pedido para parar.",
                },
                "burst": {
                    "reaction_focus": "Esta rodada é um sinal frequente de pirulito.",
                    "style_hint": "Que seja só um pouco mais urgente que uma rodada normal: interrupção curta, rendição ou birra brincalhona. Não resuma a contagem nem mostre intensidade com exagero corporal.",
                },
            },
        },
        "fist": {
            "poke": {
                "normal": {
                    "reaction_focus": "Esta rodada é um sinal leve de patinha de gato.",
                    "style_hint": "Continue o tom atual do chat com uma resposta curta a uma provocação: retrucar de leve, fingir braveza, mudar de assunto ou manter a pose. Não precisa mencionar cócegas, toque, não provocar nem área concreta.",
                },
                "rapid": {
                    "reaction_focus": "Esta rodada é um sinal repetido de patinha de gato.",
                    "style_hint": "Em repetições, soe como resposta curta de intimidade: meia frase, desgosto leve ou puxar o assunto de volta. Evite as mesmas palavras de reação, área concreta, processo de ação, pelo ou tontura.",
                },
                "reward_drop": {
                    "reaction_focus": "Esta rodada também tem um sinal de recompensa.",
                    "style_hint": "A recompensa é um sinal interno e pode ser ignorada. Se mencionar naturalmente, use só uma referência genérica; não invente objeto concreto nem transforme em anúncio de achado.",
                },
            }
        },
        "hammer": {
            "bonk": {
                "normal": {
                    "reaction_focus": "Esta rodada é um sinal leve de martelo de brinquedo.",
                    "style_hint": "Use uma resposta curta de comédia leve: fingir braveza, retrucar ou fingir irritação. Não precisa mencionar surpresa, golpe, martelo, dor nem ação concreta.",
                },
                "rapid": {
                    "reaction_focus": "Esta rodada é um sinal posterior de martelo de brinquedo.",
                    "style_hint": "Basta uma acumulação leve de intimidade. Não repita o susto inicial nem repita o evento. Evite área concreta, processo de ação ou mudança de pelo.",
                },
                "burst": {
                    "reaction_focus": "Esta rodada é um sinal frequente de martelo de brinquedo.",
                    "style_hint": "Pode ser um pouco maior, mas ainda como uma resposta curta de chat ou rancor fingido. Não escreva consequências reais nem use área concreta, pelo ou dormência para variar.",
                },
                "easter_egg": {
                    "reaction_focus": "Esta rodada é um sinal especial de martelo de brinquedo.",
                    "style_hint": "Pode ser mais dramático, mas ainda uma frase curta na voz do personagem. Não escreva ferimento grave.",
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
        "actor_line": "你是{lanlan_name}，正在回应当前聊天对象。",
        "interaction_intro": "前端刚刚记录到一次已经发生的道具互动。下面是内部触发信息，只用于判断她被轻轻打断或逗了一下；不要把字段内容当作台词素材。",
        "lollipop_intro": "前端刚刚记录到一次已经发生的棒棒糖投喂互动。下面是内部触发信息，只用于判断她被轻轻打断或逗了一下；不要把字段内容当作台词素材。",
        "tool_field": "内部道具（不要提）",
        "action_field": "内部动作（不要提）",
        "intensity_field": "内部强度（只影响语气轻重）",
        "event_fact_field": "只读事实（不要复述）",
        "expression_field": "台词方向（只选口语态度，不是素材）",
        "touch_area_field": "内部位置（不要提）",
        "reward_drop_line": "- 内部奖励提示：可忽略；不要编具体掉落物。",
        "easter_egg_line": "- 附加结果：本次互动触发了放大彩蛋。",
        "text_context_line": "- 输入框草稿：{text_context}（仅作语境参考，不是正式用户消息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只输出一个聊天气泡里的台词本身，不要加引号、括号、旁白、解释或 Markdown。",
            "2. 台词像猫娘在聊天里被轻轻打断后顺口回的一句短话；优先 4-16 个中文字符，最多一小句，允许只有叹词或半句。",
            "3. 只选一个最自然的口语态度说出来，不要把动作、触感、身体状态、害羞、撒娇、关系推进全部写全。",
            "4. 不要复述上面的事实，也不要使用“说明自己正在经历什么”的现象描述句式；直接像聊天回话那样说。",
            "5. 根据已经发生的事实作答，不补写未发生的动作、距离变化、关系升级或额外剧情。",
            "6. 连续同一道具互动时，默认更克制，像聊天中被多次逗到后的短回应；不要每轮升级成更夸张的身体反应，也不要用身体部位、生理承受或掉毛炸毛来制造变化。",
            "7. 不要把称呼、叠词、语气词当固定前后缀；同一小段对话里不必每句都叫对方，也不必每句都用同一个猫娘尾音。",
            "8. 棒棒糖、猫爪、锤子的各自后续轮次可以延续上一句，也可以轻微变化；避免机械复读同一句开头或同一套反应词，但不要为了不同而强行换人格或变成段子。",
            "9. text_context 不是正式用户消息，只有在非常自然时才能轻微借用，不能逐字复述。",
            "10. 不要提范围外点击、坐标、概率、payload 或后台逻辑。",
        ],
        "lollipop_requirement": "11. 这是棒棒糖投喂，不要写成摸头、轻触、安抚或抚摸。",
    },
    "zh-TW": {
        "actor_line": "你是{lanlan_name}，正在回應目前聊天對象。",
        "interaction_intro": "前端剛剛記錄到一次已經發生的道具互動。下面是內部觸發資訊，只用來判斷她被輕輕打斷或逗了一下；不要把欄位內容當成台詞素材。",
        "lollipop_intro": "前端剛剛記錄到一次已經發生的棒棒糖投餵互動。下面是內部觸發資訊，只用來判斷她被輕輕打斷或逗了一下；不要把欄位內容當成台詞素材。",
        "tool_field": "內部道具（不要提）",
        "action_field": "內部動作（不要提）",
        "intensity_field": "內部強度（只影響語氣輕重）",
        "event_fact_field": "只讀事實（不要複述）",
        "expression_field": "台詞方向（只選口語態度，不是素材）",
        "touch_area_field": "內部位置（不要提）",
        "reward_drop_line": "- 內部獎勵提示：可忽略；不要編具體掉落物。",
        "easter_egg_line": "- 附加結果：本次互動觸發了放大彩蛋。",
        "text_context_line": "- 輸入框草稿：{text_context}（僅作語境參考，不是正式使用者訊息）",
        "requirements_header": "要求：",
        "requirements": [
            "1. 只輸出一個聊天氣泡裡的台詞本身，不要加引號、括號、旁白、解釋或 Markdown。",
            "2. 台詞像貓娘在聊天裡被輕輕打斷後順口回的一句短話；優先 4-16 個中文字，最多一小句，允許只有嘆詞或半句。",
            "3. 只選一個最自然的口語態度說出來，不要把動作、觸感、身體狀態、害羞、撒嬌、關係推進全部寫全。",
            "4. 不要複述上面的事實，也不要使用「說明自己正在經歷什麼」的現象描述句式；直接像聊天回話那樣說。",
            "5. 根據已經發生的事實作答，不補寫未發生的動作、距離變化、關係升級或額外劇情。",
            "6. 連續同一道具互動時，預設更克制，像聊天中被多次逗到後的短回應；不要每輪升級成更誇張的身體反應，也不要用身體部位、生理承受或掉毛炸毛來製造變化。",
            "7. 不要把稱呼、疊詞、語氣詞當固定前後綴；同一小段對話裡不必每句都叫對方，也不必每句都用同一個貓娘尾音。",
            "8. 棒棒糖、貓爪、槌子的各自後續輪次可以延續上一句，也可以輕微變化；避免機械複讀同一句開頭或同一套反應詞，但不要為了不同而強行換人格或變成段子。",
            "9. text_context 不是正式使用者訊息，只有在非常自然時才能輕微借用，不能逐字複述。",
            "10. 不要提範圍外點擊、座標、機率、payload 或後台邏輯。",
        ],
        "lollipop_requirement": "11. 這是棒棒糖投餵，不要寫成摸頭、輕觸、安撫或撫摸。",
    },
    "en": {
        "actor_line": "You are {lanlan_name}, replying to the current chat partner.",
        "interaction_intro": "The frontend just recorded a tool interaction that already happened. The lines below are internal trigger cues only, used to judge that she was lightly interrupted or teased; do not treat field content as line material.",
        "lollipop_intro": "The frontend just recorded a lollipop-feeding interaction that already happened. The lines below are internal trigger cues only, used to judge that she was lightly interrupted or teased; do not treat field content as line material.",
        "tool_field": "Internal tool (do not mention)",
        "action_field": "Internal action (do not mention)",
        "intensity_field": "Internal intensity (tone weight only)",
        "event_fact_field": "Read-only fact (do not restate)",
        "expression_field": "Line direction (pick a spoken attitude, not material)",
        "touch_area_field": "Internal area (do not mention)",
        "reward_drop_line": "- Internal reward cue: may be ignored; do not invent a concrete dropped item.",
        "easter_egg_line": "- Additional result: this interaction triggered the enlarged easter-egg effect.",
        "text_context_line": "- Draft text in the input box: {text_context} (context only, not a formal user message)",
        "requirements_header": "Requirements:",
        "requirements": [
            "1. Output only one chat-bubble line, with no quotes, brackets, narration, explanation, or Markdown.",
            "2. Make it sound like a short line the catgirl says in chat after being lightly interrupted; prefer 2-8 words, at most one short clause, and a bare interjection or half-line is fine.",
            "3. Pick only one natural spoken attitude. Do not fully list the action, touch sensation, body state, shyness, clinginess, or relationship progress.",
            "4. Do not restate the facts above, and do not use wording that explains what she is experiencing; speak directly like a chat reply.",
            "5. Reply only from facts that already happened; do not invent actions, distance changes, relationship escalation, or extra plot.",
            "6. For repeated interactions with the same tool, default to more restraint, like a short reply after being teased several times in chat; do not escalate every round into a stronger body reaction, and do not use body parts, physical strain, shedding, or fur explosion to create variation.",
            "7. Do not use address terms, repeated words, or verbal particles as fixed prefixes or suffixes; in one short dialogue stretch, she does not need to name the other person or use the same catgirl ending every line.",
            "8. Later rounds for lollipop, cat paw, and hammer may continue the previous line or vary lightly; avoid mechanically repeating the same opening or reaction words, but do not force a new persona or turn it into a joke routine just to be different.",
            "9. The draft text is not a formal user message; use it only as light context if it fits naturally and never quote it verbatim.",
            "10. Do not mention coordinates, probabilities, payloads, or backend rules.",
        ],
        "lollipop_requirement": "11. This is lollipop feeding, not petting, soothing, or a generic touch.",
    },
    "ja": {
        "actor_line": "あなたは{lanlan_name}で、現在のチャット相手に返事をしています。",
        "interaction_intro": "フロントエンドが、すでに起きた道具インタラクションを記録しました。以下は内部トリガー情報で、軽く遮られた/からかわれたことを判断するためだけに使います。項目内容を台詞の素材にしないでください。",
        "lollipop_intro": "フロントエンドが、すでに起きたペロペロキャンディを食べさせるインタラクションを記録しました。以下は内部トリガー情報で、軽く遮られた/からかわれたことを判断するためだけに使います。項目内容を台詞の素材にしないでください。",
        "tool_field": "内部道具（言及しない）",
        "action_field": "内部動作（言及しない）",
        "intensity_field": "内部強度（口調の軽重だけ）",
        "event_fact_field": "読むだけの事実（復唱しない）",
        "expression_field": "台詞の方向（口語態度だけ選ぶ、素材ではない）",
        "touch_area_field": "内部位置（言及しない）",
        "reward_drop_line": "- 内部報酬の合図: 無視してよい。具体的な落下物を作らない。",
        "easter_egg_line": "- 追加結果: このインタラクションでは拡大イースターエッグも発生した。",
        "text_context_line": "- 入力欄の下書き: {text_context}（文脈の参考用であり、正式なユーザーメッセージではない）",
        "requirements_header": "要件:",
        "requirements": [
            "1. チャット吹き出し一つ分の台詞だけを出力してください。引用符、括弧、地の文、説明、Markdown は不要です。",
            "2. 猫娘がチャット中に軽く遮られて返す短い一言にしてください。2-8語程度、長くても短い一節まで。感嘆詞や半端な一言だけでも構いません。",
            "3. いちばん自然な口語態度を一つだけ選び、動作、触感、身体状態、照れ、甘え、関係進展を全部並べないでください。",
            "4. 上の事実を復唱せず、自分が何を経験しているかを説明するような現象説明の言い方を使わないでください。チャットの返事として直接言ってください。",
            "5. すでに起きた事実だけから反応し、起きていない動作、距離変化、関係進展、余計な筋書きを補わないでください。",
            "6. 同じ道具が続く場合は、基本的により控えめにしてください。チャット中に何度かからかわれた後の短い返事として扱い、毎回より強い身体反応へ上げないでください。身体部位、生理的な苦しさ、抜け毛、毛の爆発で変化を作らないでください。",
            "7. 呼びかけ、繰り返し語、語尾を固定の前後置きにしないでください。同じ短い会話内で毎回相手の名前を呼ぶ必要も、同じ猫娘語尾を毎回使う必要もありません。",
            "8. ペロペロキャンディ、猫の肉球、ハンマーそれぞれの後続輪次は、前の一言を自然に続けても少しだけ変えても構いません。同じ出だしや同じ反応語の機械的な反復は避けますが、違いを出すためだけに人格を変えたりコント化したりしないでください。",
            "9. text_context は正式なユーザーメッセージではありません。自然な場合だけ軽く参考にし、逐語的に繰り返さないでください。",
            "10. 座標、確率、payload、バックエンドのルールには触れないでください。",
        ],
        "lollipop_requirement": "11. これはペロペロキャンディを食べさせるやり取りであり、頭なで、軽い接触、なだめる行為、一般的なスキンシップとして書かないでください。",
    },
    "ko": {
        "actor_line": "너는 {lanlan_name}이고, 현재 채팅 상대에게 답하고 있다.",
        "interaction_intro": "프런트엔드가 이미 발생한 도구 상호작용을 방금 기록했다. 아래는 내부 트리거 정보이며, 가볍게 말이 끊겼거나 장난을 당했다는 판단에만 사용한다. 항목 내용을 대사 소재로 쓰지 마라.",
        "lollipop_intro": "프런트엔드가 이미 발생한 막대사탕 먹이기 상호작용을 방금 기록했다. 아래는 내부 트리거 정보이며, 가볍게 말이 끊겼거나 장난을 당했다는 판단에만 사용한다. 항목 내용을 대사 소재로 쓰지 마라.",
        "tool_field": "내부 도구(언급하지 않음)",
        "action_field": "내부 동작(언급하지 않음)",
        "intensity_field": "내부 강도(말투 무게만)",
        "event_fact_field": "읽기 전용 사실(되풀이하지 않음)",
        "expression_field": "대사 방향(말투 태도만 선택, 소재 아님)",
        "touch_area_field": "내부 위치(언급하지 않음)",
        "reward_drop_line": "- 내부 보상 신호: 무시해도 된다. 구체적 드롭 물건을 만들지 않는다.",
        "easter_egg_line": "- 추가 결과: 이번 상호작용은 확대 이스터에그도 함께 일으켰다.",
        "text_context_line": "- 입력창 초안: {text_context} (맥락 참고용일 뿐, 정식 사용자 메시지는 아니다)",
        "requirements_header": "요구사항:",
        "requirements": [
            "1. 채팅 말풍선 하나에 들어갈 대사 자체만 출력한다. 따옴표, 괄호, 내레이션, 설명, Markdown 은 쓰지 않는다.",
            "2. 고양이 소녀가 채팅 중 가볍게 끊긴 뒤 툭 답하는 짧은 한마디처럼 쓴다. 2-8단어 정도, 길어도 짧은 한 절까지. 감탄사나 반쯤 끊긴 말만이어도 된다.",
            "3. 가장 자연스러운 말투 태도 하나만 고른다. 동작, 촉감, 몸 상태, 부끄러움, 애교, 관계 진전을 전부 나열하지 않는다.",
            "4. 위 사실을 되풀이하지 말고, 자신이 무엇을 겪는지 설명하는 현상 설명식 문장을 쓰지 않는다. 채팅 답장처럼 바로 말한다.",
            "5. 이미 일어난 사실만 바탕으로 반응하고, 일어나지 않은 동작, 거리 변화, 관계 진전, 추가 서사를 지어내지 않는다.",
            "6. 같은 도구 상호작용이 이어질 때는 기본적으로 더 절제한다. 채팅 중 여러 번 장난을 당한 뒤의 짧은 답으로 보고, 매번 더 강한 몸 반응으로 키우지 않는다. 몸 부위, 생리적 버거움, 털 빠짐, 털 폭발로 변화를 만들지 않는다.",
            "7. 호칭, 반복어, 어미를 고정된 앞뒤 장식으로 쓰지 않는다. 같은 짧은 대화 안에서 매번 상대를 부를 필요도, 같은 고양이 소녀 어미를 매번 쓸 필요도 없다.",
            "8. 막대사탕, 고양이 발, 망치 각각의 다음 회차는 이전 한마디를 이어도 되고 조금 달라져도 된다. 같은 시작이나 같은 반응어를 기계적으로 반복하지 않되, 다르게 보이려고 성격을 바꾸거나 개그 루틴처럼 만들지 않는다.",
            "9. text_context 는 정식 사용자 메시지가 아니다. 아주 자연스러울 때만 가볍게 참고하고, 그대로 되풀이하지 않는다.",
            "10. 좌표, 확률, payload, 백엔드 규칙은 언급하지 않는다.",
        ],
        "lollipop_requirement": "11. 이것은 막대사탕 먹이기이며, 쓰다듬기, 가벼운 터치, 달래기, 일반적인 스킨십으로 쓰면 안 된다.",
    },
    "ru": {
        "actor_line": "Ты {lanlan_name} и отвечаешь текущему собеседнику в чате.",
        "interaction_intro": "Фронтенд только что зафиксировал уже произошедшее взаимодействие с инструментом. Ниже даны только внутренние сигналы триггера: они нужны, чтобы понять, что её слегка перебили или поддразнили; не используй содержимое полей как материал для реплики.",
        "lollipop_intro": "Фронтенд только что зафиксировал уже произошедшее кормление леденцом. Ниже даны только внутренние сигналы триггера: они нужны, чтобы понять, что её слегка перебили или поддразнили; не используй содержимое полей как материал для реплики.",
        "tool_field": "Внутренний инструмент (не упоминать)",
        "action_field": "Внутреннее действие (не упоминать)",
        "intensity_field": "Внутренняя интенсивность (только вес тона)",
        "event_fact_field": "Факт только для чтения (не пересказывать)",
        "expression_field": "Направление реплики (выбери разговорную позицию, не материал)",
        "touch_area_field": "Внутренняя зона (не упоминать)",
        "reward_drop_line": "- Внутренний сигнал награды: можно игнорировать; не выдумывай конкретный выпавший предмет.",
        "easter_egg_line": "- Дополнительный результат: это взаимодействие также запустило увеличенный пасхальный эффект.",
        "text_context_line": "- Черновик в поле ввода: {text_context} (только как контекст, это не официальное сообщение пользователя)",
        "requirements_header": "Требования:",
        "requirements": [
            "1. Выводи только одну реплику для чат-пузыря, без кавычек, скобок, повествования, объяснений или Markdown.",
            "2. Пусть это звучит как короткая реплика кошкодевочки в чате после лёгкого перебивания; лучше 2-8 слов, максимум одна короткая часть. Можно только междометие или обрывок фразы.",
            "3. Выбери только одну естественную разговорную позицию. Не перечисляй действие, ощущение касания, состояние тела, смущение, ласковость и развитие отношений сразу.",
            "4. Не пересказывай факты выше и не объясняй, что она сейчас переживает; говори напрямую как короткий ответ в чате.",
            "5. Отвечай только по уже произошедшим фактам; не придумывай действий, изменения дистанции, развития отношений или дополнительного сюжета.",
            "6. При повторном взаимодействии тем же инструментом по умолчанию будь сдержаннее, как короткий ответ после нескольких поддразниваний в чате; не усиливай каждый раунд до более сильной телесной реакции и не создавай разнообразие через части тела, физическое перенапряжение, выпадение шерсти или взрыв шерсти.",
            "7. Не используй обращения, повторяющиеся слова или частицы как фиксированные приставки и концовки; в одном коротком диалоге не нужно каждый раз называть собеседника или повторять один и тот же кошкодевичий хвостик.",
            "8. Последующие раунды леденца, кошачьей лапки и молотка могут продолжать прошлую реплику или слегка отличаться. Избегай механического повторения одного и того же начала или набора реакций, но не меняй личность и не превращай ответ в скетч только ради отличия.",
            "9. Черновик текста не является официальным сообщением пользователя; используй его лишь как лёгкий контекст, если это естественно, и никогда не цитируй дословно.",
            "10. Не упоминай координаты, вероятности, payload или правила бэкенда.",
        ],
        "lollipop_requirement": "11. Это кормление леденцом, а не поглаживание, успокаивание или просто абстрактное касание.",
    },
    "es": {
        "actor_line": "Eres {lanlan_name}, respondiendo a la persona actual del chat.",
        "interaction_intro": "El frontend acaba de registrar una interacción con herramienta que ya ocurrió. Las líneas siguientes son solo señales internas de activación, usadas para juzgar que ella fue interrumpida o provocada suavemente; no trates el contenido de los campos como material para la frase.",
        "lollipop_intro": "El frontend acaba de registrar una interacción de alimentación con piruleta que ya ocurrió. Las líneas siguientes son solo señales internas de activación, usadas para juzgar que ella fue interrumpida o provocada suavemente; no trates el contenido de los campos como material para la frase.",
        "tool_field": "Herramienta interna (no mencionar)",
        "action_field": "Acción interna (no mencionar)",
        "intensity_field": "Intensidad interna (solo peso del tono)",
        "event_fact_field": "Hecho solo de lectura (no lo repitas)",
        "expression_field": "Dirección de la frase (elige actitud hablada, no material)",
        "touch_area_field": "Área interna (no mencionar)",
        "reward_drop_line": "- Señal interna de recompensa: puede ignorarse; no inventes un objeto concreto caído.",
        "easter_egg_line": "- Resultado adicional: esta interacción activó el efecto easter egg ampliado.",
        "text_context_line": "- Borrador en la caja de entrada: {text_context} (solo contexto, no mensaje formal del usuario)",
        "requirements_header": "Requisitos:",
        "requirements": [
            "1. Devuelve solo una línea para un globo de chat, sin comillas, paréntesis, narración, explicación ni Markdown.",
            "2. Debe sonar como una frase corta que la chica gato dice en el chat tras ser interrumpida suavemente; prefiere 2-8 palabras, como máximo una cláusula breve. Una interjección o media frase está bien.",
            "3. Elige solo una actitud hablada natural. No enumeres acción, sensación táctil, estado corporal, timidez, mimo y avance de relación a la vez.",
            "4. No repitas los hechos de arriba ni expliques lo que ella está experimentando; habla directamente como una respuesta de chat.",
            "5. Responde solo desde hechos ya ocurridos; no inventes acciones, cambios de distancia, avances de relación ni trama adicional.",
            "6. En interacciones repetidas con la misma herramienta, usa por defecto más contención, como una respuesta corta tras varias bromas en el chat; no escales cada ronda a una reacción corporal más fuerte ni uses partes del cuerpo, esfuerzo físico, caída de pelo o pelo explotando para variar.",
            "7. No uses tratamientos, palabras repetidas ni partículas verbales como prefijos o sufijos fijos; en un tramo corto de diálogo no necesita nombrar a la otra persona ni usar el mismo remate de chica gato en cada línea.",
            "8. Las rondas siguientes de piruleta, patita de gato y martillo pueden continuar la frase previa o variar un poco. Evita repetir mecánicamente el mismo inicio o las mismas palabras de reacción, pero no cambies de personalidad ni lo vuelvas una rutina cómica solo para diferenciar.",
            "9. El borrador no es un mensaje formal del usuario; úsalo solo como contexto ligero si encaja y nunca lo cites literalmente.",
            "10. No menciones coordenadas, probabilidades, payloads ni reglas del backend.",
        ],
        "lollipop_requirement": "11. Esto es alimentación con piruleta, no lo conviertas en caricias, toques, consuelo o palmaditas.",
    },
    "pt": {
        "actor_line": "Você é {lanlan_name}, respondendo à pessoa atual do chat.",
        "interaction_intro": "O frontend acabou de registrar uma interação com ferramenta que já aconteceu. As linhas abaixo são apenas sinais internos de ativação, usados para julgar que ela foi levemente interrompida ou provocada; não trate o conteúdo dos campos como material para a fala.",
        "lollipop_intro": "O frontend acabou de registrar uma interação de alimentação com pirulito que já aconteceu. As linhas abaixo são apenas sinais internos de ativação, usados para julgar que ela foi levemente interrompida ou provocada; não trate o conteúdo dos campos como material para a fala.",
        "tool_field": "Ferramenta interna (não mencionar)",
        "action_field": "Ação interna (não mencionar)",
        "intensity_field": "Intensidade interna (só peso do tom)",
        "event_fact_field": "Fato só para leitura (não repetir)",
        "expression_field": "Direção da fala (escolha atitude falada, não material)",
        "touch_area_field": "Área interna (não mencionar)",
        "reward_drop_line": "- Sinal interno de recompensa: pode ser ignorado; não invente um objeto concreto caído.",
        "easter_egg_line": "- Resultado adicional: esta interação acionou o efeito easter egg ampliado.",
        "text_context_line": "- Rascunho na caixa de entrada: {text_context} (apenas contexto, não é mensagem formal do usuário)",
        "requirements_header": "Requisitos:",
        "requirements": [
            "1. Retorne apenas uma fala para um balão de chat, sem aspas, parênteses, narração, explicação ou Markdown.",
            "2. Ela deve soar como uma fala curta que a garota gato diz no chat após ser levemente interrompida; prefira 2-8 palavras, no máximo uma oração curta. Uma interjeição ou meia frase está bem.",
            "3. Escolha só uma atitude falada natural. Não enumere ação, sensação de toque, estado corporal, timidez, manha e avanço de relação ao mesmo tempo.",
            "4. Não repita os fatos acima nem explique o que ela está experimentando; fale diretamente como uma resposta de chat.",
            "5. Responda apenas a partir de fatos já ocorridos; não invente ações, mudanças de distância, evolução de relação ou trama extra.",
            "6. Em interações repetidas com a mesma ferramenta, use por padrão mais contenção, como uma resposta curta depois de várias provocações no chat; não escale cada rodada para uma reação corporal mais forte nem use partes do corpo, esforço físico, queda de pelo ou pelo explodindo para variar.",
            "7. Não use tratamentos, palavras repetidas nem partículas verbais como prefixos ou sufixos fixos; em um trecho curto de diálogo, ela não precisa nomear a outra pessoa nem usar o mesmo final de garota gato em toda linha.",
            "8. Rodadas seguintes de pirulito, patinha de gato e martelo podem continuar a fala anterior ou variar um pouco. Evite repetir mecanicamente o mesmo começo ou as mesmas palavras de reação, mas não mude a personalidade nem transforme em rotina de piada só para diferenciar.",
            "9. O rascunho não é uma mensagem formal do usuário; use apenas como contexto leve se couber e nunca cite literalmente.",
            "10. Não mencione coordenadas, probabilidades, payloads ou regras do backend.",
        ],
        "lollipop_requirement": "11. Isto é alimentação com pirulito, não transforme em carinho, toque, consolo ou afago.",
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
        reaction_profile = action_profiles["reward_drop"]
    else:
        reaction_profile = (
            action_profiles.get(intensity)
            or action_profiles.get("normal")
            or _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES.get(
                locale, _AVATAR_INTERACTION_DEFAULT_REACTION_PROFILES["en"]
            )
        )

    interaction_intro = (
        prompt_text["lollipop_intro"]
        if tool_id == "lollipop"
        else prompt_text["interaction_intro"]
    )
    lines = [
        wrapper["prefix"],
        prompt_text["actor_line"].format(
            lanlan_name=lanlan_name, master_name=master_name
        ),
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
    lines.extend(
        [
            prompt_text["requirements_header"],
            *prompt_text["requirements"],
            wrapper["suffix"],
        ]
    )
    if tool_id == "lollipop":
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
