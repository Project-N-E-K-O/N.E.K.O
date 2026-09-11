from __future__ import annotations


def numeric_v2_story() -> dict:
    """构造一个供 NEKO_Numeric_drama 项目与 API 测试复用的最小合法包。"""

    def beat(summary: str, goal: str) -> dict:
        return {
            "summary": summary,
            "must_happen": [goal],
            "must_not_happen": [],
            "catgirl_situation": "她正在判断玩家是否可信。",
            "transition_goal": "让关系变化自然落到作者设定的结果。",
        }

    def gate(gate_id: str, target: str, op: str, value: int, priority: int) -> dict:
        return {
            "id": gate_id,
            "target_node_id": target,
            "priority": priority,
            "conditions": {
                "all": [{
                    "type": "metric_compare",
                    "metric": "trust",
                    "op": op,
                    "value": value,
                }]
            },
            "transition_contract": {
                "reason": "当前信任度满足作者路线条件。",
                "must_deliver": ["交付对应的剧情结果"],
                "must_preserve": ["保留此前已经发生的互动"],
                "tone": "克制",
            },
        }

    return {
        "schema": "neko.story.numeric.v2",
        "meta": {
            "story_id": "neko_numeric_drama_test",
            "title": "清河晚风",
            "author": "NEKO_Numeric_drama",
            "revision": "draft_1",
            "language": "zh-CN",
            "contract_version": "v2.2",
        },
        "intro": {
            "background": "玩家多年后回到小镇，在花店遇到旧友。",
            "player_identity": "男主，回乡整理旧屋的年轻邻居。",
            "catgirl_identity": "女主，经营花店、保留旧信的儿时朋友。",
        },
        "characters": {},
        "catgirl_binding": {
            "source": "runtime.current_catgirl",
            "role_overlay": "她期待重逢，也担心玩家再次离开。",
        },
        "metric_schema": {
            "trust": {
                "name": "信任度",
                "description": "猫娘愿意相信玩家承诺的程度。",
                "min": 0,
                "max": 100,
                "initial": 20,
                "visibility": "hidden",
                "per_turn_limit": {"increase": 5, "decrease": 5},
                "increase_criteria": ["玩家兑现承诺"],
                "decrease_criteria": ["玩家故意说谎"],
                "bands": [
                    {"min": 0, "max": 29, "label": "戒备"},
                    {"min": 30, "max": 69, "label": "试探"},
                    {"min": 70, "max": 100, "label": "信赖"},
                ],
            }
        },
        "initial_state": {"metrics": {"trust": 20}, "player_address_known": False},
        "start_node_id": "start",
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "chapter": "重逢",
                "min_turns": 2,
                "recommended_turns": 4,
                "story_beat": beat(
                    "雨后的花店门铃轻轻响起。",
                    "猫娘向来客说明她仍妥善保留着旧信。",
                ),
                "route_gates": [
                    gate("to_stay", "ending_stay", ">=", 70, 20),
                    gate("to_leave", "ending_leave", "<", 70, 10),
                ],
            },
            {
                "id": "ending_stay",
                "type": "ending",
                "chapter": "决定",
                "story_beat": beat(
                    "旧信与备用钥匙一同放在花店桌面上。",
                    "猫娘说明她愿意为未来保留继续相处的位置。",
                ),
                "route_gates": [],
                "terminal": True,
                "ending_id": "stay",
            },
            {
                "id": "ending_leave",
                "type": "ending",
                "chapter": "决定",
                "story_beat": beat(
                    "雨停后的长街恢复了安静。",
                    "猫娘说明她已经接受暂时分别的安排。",
                ),
                "route_gates": [],
                "terminal": True,
                "ending_id": "leave",
            },
        ],
        "endings": [
            {"id": "stay", "title": "留下", "summary": "玩家留下。", "terminal": True},
            {"id": "leave", "title": "离开", "summary": "玩家离开。", "terminal": True},
        ],
    }


def numeric_v2_setup() -> dict:
    story = numeric_v2_story()
    definition = story["metric_schema"]["trust"]
    return {
        "brief": "多年未见的两人在小镇重逢，最后决定留下还是分别。",
        "genre": "小镇故事",
        "tone": ["克制", "温柔"],
        "relationship": "多年没有联系的邻居",
        "content_boundaries": ["不使用突然失忆"],
        "length_preset": "short",
        "metrics": [{
            "id": "trust",
            "preset": "trust",
            "name": definition["name"],
            "description": definition["description"],
            "min": definition["min"],
            "max": definition["max"],
            "initial": definition["initial"],
            "increase_limit": definition["per_turn_limit"]["increase"],
            "decrease_limit": definition["per_turn_limit"]["decrease"],
            "increase_criteria": definition["increase_criteria"],
            "decrease_criteria": definition["decrease_criteria"],
            "visibility": definition["visibility"],
            "bands": definition["bands"],
        }],
    }
