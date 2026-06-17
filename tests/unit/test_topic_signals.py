import json
import time

from main_logic.topic.signals import TopicSignalStore


def test_topic_signal_store_keeps_filler_chat_below_ready_even_after_many_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)
    now = time.time()

    for idx, text in enumerate(["嗯", "哈哈", "好", "可以", "啊", "行", "哦", "没事", "对", "不知道"]):
        store.note_turn("妮可", actor="user", text=text, now=now + idx)

    # All filler → no meaningful turns → never ready, however many arrive.
    assert store.readiness_percent("妮可") == 0
    assert store.is_ready("妮可") is False
    formatted = store.format_global_signals("妮可")
    # Only the raw evidence list is emitted — no stats head, no inner header.
    assert "收集进度:" not in formatted
    assert "全局证据:" not in formatted
    assert "- [" in formatted  # turn lines still render


def test_topic_signal_store_ready_after_enough_meaningful_user_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)
    now = time.time()

    store.note_turn("妮可", actor="user", text="我最近一直在纠结要不要换工作，怕换了之后更坑", now=now)
    store.note_turn("妮可", actor="ai", text="你像是在怕失去可控感。", now=now + 1)
    store.note_turn("妮可", actor="user", text="对，我不是怕累，是怕选错了以后回不了头", now=now + 2)
    store.note_turn("妮可", actor="user", text="但现在这个工作又真的让我每天都很烦", now=now + 3)
    store.note_turn("妮可", actor="user", text="要不要干脆换个城市重新开始", now=now + 4)

    # 4 meaningful user turns reach the gate (the AI turn does not count).
    assert store.is_ready("妮可") is True
    assert store.readiness_percent("妮可") >= 80
    formatted = store.format_global_signals("妮可")
    assert "稳定度:" not in formatted
    assert "换工作" in formatted


def test_topic_signal_store_default_ready_requires_eight_meaningful_user_turns():
    store = TopicSignalStore()
    now = time.time()

    for idx in range(7):
        store.note_turn("妮可", actor="user", text=f"第{idx}个认真话题信号", now=now + idx)

    assert store.is_ready("妮可") is False

    store.note_turn("妮可", actor="user", text="第8个认真话题信号", now=now + 8)

    assert store.is_ready("妮可") is True


def test_topic_signal_store_localizes_evidence_lines():
    store = TopicSignalStore(min_user_turns_for_topic=1)
    now = time.time()
    store.note_turn("neko", actor="user", text="I keep thinking about moving to a quieter city", now=now)
    store.note_turn("neko", actor="ai", text="That sounds like a need for more control.", now=now + 1)

    formatted = store.format_global_signals("neko", lang="en")

    # No stats head / inner header; the per-line actor + age label localizes.
    assert "moving to a quieter city" in formatted
    assert "User:" in formatted
    assert "Global evidence:" not in formatted
    assert "收集进度:" not in formatted


def test_filler_turns_do_not_count_toward_readiness():
    now = time.time()
    substantive = TopicSignalStore(min_user_turns_for_topic=4)
    for idx, text in enumerate([
        "我在纠结要不要换工作",
        "怕选错了以后回不了头",
        "现在的工作让我每天都很烦",
        "想去个安静点的城市重新开始",
    ]):
        substantive.note_turn("妮可", actor="user", text=text, now=now + idx)

    filler = TopicSignalStore(min_user_turns_for_topic=4)
    for idx, text in enumerate(["嗯", "哈哈", "好", "可以"]):
        filler.note_turn("妮可", actor="user", text=text, now=now + idx)

    assert substantive.is_ready("妮可") is True
    assert filler.is_ready("妮可") is False
    assert substantive.readiness_percent("妮可") > filler.readiness_percent("妮可")


def test_topic_signal_store_renders_the_full_rolling_window():
    store = TopicSignalStore(min_user_turns_for_topic=1, max_turns=60)
    now = time.time()

    for idx in range(65):
        store.note_turn("妮可", actor="user", text=f"第{idx}条长期信号", now=now + idx)

    formatted = store.format_global_signals("妮可")

    assert "第0条长期信号" not in formatted
    assert "第4条长期信号" not in formatted
    assert "第5条长期信号" in formatted
    assert "第64条长期信号" in formatted
    assert formatted.count("- [") == 60


def test_topic_signal_store_persists_runtime_retention_prune(tmp_path):
    path = tmp_path / "topic_signals.json"
    now = time.time()
    store = TopicSignalStore(
        min_user_turns_for_topic=1,
        retention_seconds=10,
        persistence_path=path,
        persistence_flush_delay_seconds=60,
    )
    store.note_turn("妮可", actor="user", text="已经过期的原始证据", now=now - 20)
    store.note_turn("妮可", actor="user", text="仍在窗口内的新证据", now=now)
    store.flush()

    assert store.last_turn_at("妮可") == now
    store.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    texts = [
        item["text"]
        for item in payload["characters"]["妮可"]
    ]
    assert texts == ["仍在窗口内的新证据"]
