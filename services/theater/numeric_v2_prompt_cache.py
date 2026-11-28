"""Keep transient Actor data after the stable instructions and authored context."""

import re

from utils.llm_client import HumanMessage
from .numeric_v2_prompt_mark import theater_system_message
from .prompt_data import prompt_data_block, prompt_data_block_payload


_TRANSIENT = {"本轮玩家动作结果投影", "本轮重试反馈", "开场重试反馈"}
_FRAME = re.compile(r"======以下为([^\n=]+)======\n.*?\n======以上为\1======", re.DOTALL)


def cache_ordered_messages(messages):
    supplemental = {}
    result = []
    for message in messages:
        if message.role != "system":
            result.append(message)
            continue
        def detach(match):
            label = match[1]
            if label not in _TRANSIENT:
                return match[0]
            supplemental[label] = prompt_data_block_payload(match[0], label)
            return "（见末尾同名资料）"
        result.append(theater_system_message(_FRAME.sub(detach, message.content)))
    task = {}
    for message in result:
        if message.role != "user":
            continue
        for label in ("玩家本轮输入", "剧本资料"):
            try:
                data = prompt_data_block_payload(message.content, label)
            except KeyError:
                continue
            task.update({key: data[key] for key in (
                "player_input", "current_scene_task", "source_delivery_task") if key in data})
    if supplemental or task:
        continuation = (
            "\n从最新已播状态之后写起，先回应本次玩家输入，再交付仍缺且前因已具备的一项回应；"
            "不复述上轮，不重演已完成事件。任务和作者规划不证明已发生，也不替玩家行动或选择。")
        result.append(HumanMessage(content=(
            prompt_data_block("演出补充资料", supplemental) + "\n\n"
            + prompt_data_block("本轮续写任务", task)
            + continuation)))
    return result
