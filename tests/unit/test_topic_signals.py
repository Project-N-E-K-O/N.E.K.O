from main_logic.topic.signals import TopicSignalStore, TopicTurnSignal, _select_turns_for_prompt


def test_topic_signal_store_keeps_filler_chat_below_ready_even_after_many_turns():
    store = TopicSignalStore(min_user_turns_for_topic=4)

    for text in ["嗯", "哈哈", "好", "可以", "啊", "行", "哦", "没事", "对", "不知道"]:
        store.note_turn("妮可", actor="user", text=text, now=1.0)

    assert store.readiness_percent("妮可") < 80
    assert store.is_ready("妮可") is False
    formatted = store.format_global_signals("妮可")
    # stats head (readiness/density/stability) is no longer injected into the
    # prompt — only the raw evidence list remains. readiness stays a backend gate.
    assert "收集进度:" not in formatted
    assert "信息密度:" not in formatted
    assert "全局证据:" in formatted


def test_topic_signal_store_allows_dense_short_collection_to_be_analyzed():
    store = TopicSignalStore(min_user_turns_for_topic=4)

    store.note_turn("妮可", actor="user", text="我最近一直在纠结要不要换工作，怕换了之后更坑", now=1.0)
    store.note_turn("妮可", actor="ai", text="你像是在怕失去可控感。", now=2.0)
    store.note_turn("妮可", actor="user", text="对，我不是怕累，是怕选错了以后回不了头", now=3.0)
    store.note_turn("妮可", actor="user", text="但现在这个工作又真的让我每天都很烦", now=4.0)

    assert store.readiness_percent("妮可") >= 80
    assert store.is_ready("妮可") is True
    formatted = store.format_global_signals("妮可")
    assert "稳定度:" not in formatted
    assert "换工作" in formatted


def test_topic_signal_store_localizes_global_signal_labels():
    store = TopicSignalStore(min_user_turns_for_topic=1)
    store.note_turn("neko", actor="user", text="I keep thinking about moving to a quieter city", lang="en", now=1.0)
    store.note_turn("neko", actor="ai", text="That sounds like a need for more control.", lang="en", now=2.0)

    formatted = store.format_global_signals("neko", lang="en")

    # stats head dropped; the evidence-list label still localizes by lang
    assert "Global evidence:" in formatted
    assert "moving to a quieter city" in formatted
    assert "Collection progress:" not in formatted
    assert "User evidence count:" not in formatted
    assert "收集进度:" not in formatted


def test_topic_signal_store_scores_repeated_theme_higher_than_unrelated_thin_turns():
    repeated = TopicSignalStore(min_user_turns_for_topic=4)
    repeated.note_turn("妮可", actor="user", text="我最近一直在看赛博朋克2077的夜之城细节", now=1.0)
    repeated.note_turn("妮可", actor="user", text="夜之城那些路边小故事特别戳我", now=2.0)
    repeated.note_turn("妮可", actor="user", text="我喜欢这种不直接讲但能让人懂的游戏细节", now=3.0)

    scattered = TopicSignalStore(min_user_turns_for_topic=4)
    scattered.note_turn("妮可", actor="user", text="今天还行", now=1.0)
    scattered.note_turn("妮可", actor="user", text="这个按钮在哪", now=2.0)
    scattered.note_turn("妮可", actor="user", text="哈哈确实", now=3.0)

    assert repeated.readiness_percent("妮可") > scattered.readiness_percent("妮可")


def test_select_turns_for_prompt_clamps_negative_max_lines():
    turns = [
        TopicTurnSignal(actor="user", text="第一句", timestamp=1.0, lang="zh-CN"),
        TopicTurnSignal(actor="user", text="第二句", timestamp=2.0, lang="zh-CN"),
    ]

    assert _select_turns_for_prompt(turns, max_lines=-1) == []
