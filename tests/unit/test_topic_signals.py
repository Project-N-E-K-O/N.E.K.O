from main_logic.topic.signals import TopicSignalStore, TopicTurnSignal, _select_turns_for_prompt


def test_topic_signal_store_keeps_filler_chat_below_ready_even_after_many_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)

    for text in ["嗯", "哈哈", "好", "可以", "啊", "行", "哦", "没事", "对", "不知道"]:
        store.note_turn("妮可", actor="user", text=text, now=1.0)

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

    store.note_turn("妮可", actor="user", text="我最近一直在纠结要不要换工作，怕换了之后更坑", now=1.0)
    store.note_turn("妮可", actor="ai", text="你像是在怕失去可控感。", now=2.0)
    store.note_turn("妮可", actor="user", text="对，我不是怕累，是怕选错了以后回不了头", now=3.0)
    store.note_turn("妮可", actor="user", text="但现在这个工作又真的让我每天都很烦", now=4.0)
    store.note_turn("妮可", actor="user", text="要不要干脆换个城市重新开始", now=5.0)

    # 4 meaningful user turns reach the gate (the AI turn does not count).
    assert store.is_ready("妮可") is True
    assert store.readiness_percent("妮可") >= 80
    formatted = store.format_global_signals("妮可")
    assert "稳定度:" not in formatted
    assert "换工作" in formatted


def test_topic_signal_store_localizes_evidence_lines():
    store = TopicSignalStore(min_user_turns_for_topic=1)
    store.note_turn("neko", actor="user", text="I keep thinking about moving to a quieter city", now=1.0)
    store.note_turn("neko", actor="ai", text="That sounds like a need for more control.", now=2.0)

    formatted = store.format_global_signals("neko", lang="en")

    # No stats head / inner header; the per-line actor + age label localizes.
    assert "moving to a quieter city" in formatted
    assert "User:" in formatted
    assert "Global evidence:" not in formatted
    assert "收集进度:" not in formatted


def test_filler_turns_do_not_count_toward_readiness():
    substantive = TopicSignalStore(min_user_turns_for_topic=4)
    for text in [
        "我在纠结要不要换工作",
        "怕选错了以后回不了头",
        "现在的工作让我每天都很烦",
        "想去个安静点的城市重新开始",
    ]:
        substantive.note_turn("妮可", actor="user", text=text, now=1.0)

    filler = TopicSignalStore(min_user_turns_for_topic=4)
    for text in ["嗯", "哈哈", "好", "可以"]:
        filler.note_turn("妮可", actor="user", text=text, now=1.0)

    assert substantive.is_ready("妮可") is True
    assert filler.is_ready("妮可") is False
    assert substantive.readiness_percent("妮可") > filler.readiness_percent("妮可")


def test_select_turns_for_prompt_clamps_negative_max_lines():
    turns = [
        TopicTurnSignal(actor="user", text="第一句", timestamp=1.0),
        TopicTurnSignal(actor="user", text="第二句", timestamp=2.0),
    ]

    assert _select_turns_for_prompt(turns, max_lines=-1) == []
