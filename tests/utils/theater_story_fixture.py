"""提供与正式剧本完全隔离的小剧场测试 Story 定位常量。"""  # noqa: DOCSTRING_CJK

from pathlib import Path


THEATER_TEST_STORY_ID = "framework_contract_story"
THEATER_TEST_STORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "theater"
    / "stories"
    / f"{THEATER_TEST_STORY_ID}.json"
)
THEATER_TEST_START_NODE_ID = "node_contract_start"
THEATER_TEST_ANCHOR_NODE_ID = "node_contract_anchor"
THEATER_TEST_EXCHANGE_NODE_ID = "node_contract_exchange"
THEATER_TEST_GOAL_ID = "goal_complete_public_exchange"
THEATER_TEST_SLOT_ID = "slot_public_exchange_item"
