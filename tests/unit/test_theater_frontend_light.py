"""验证轻量页面只保留当前版用户能力。"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_keeps_action_dialogue_and_structured_turns():
    """页面必须区分行动与对白，并提交稳定 Choice 和 revision。"""
    html = (ROOT / "templates" / "theater.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    assert 'id="theater-action-choices"' in html
    assert 'id="theater-dialogue-choices"' in html
    assert "body.choice_id = selected.choiceId" in script
    assert "base_revision: state.stateRevision" in script
    assert "input_kind: selected ? 'choice' : 'free_input'" in script


def test_frontend_renders_story_identity_before_start():
    """棕色舞台内必须渲染剧本背景、玩家身份和猫娘身份，并随故事预览更新。"""
    html = (ROOT / "templates" / "theater.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    for element_id in (
        "theater-story-intro",
        "theater-story-intro-brief",
        "theater-player-role",
        "theater-catgirl-role",
        "theater-story-goal",
    ):
        assert f'id="{element_id}"' in html
    assert "function renderStoryIntro(story)" in script
    assert "card.catgirl_role" in script
    assert "renderStoryIntro(story);" in script
    # 背景卡必须位于舞台节点内部，不能重新变成独立占高的页面区块。
    stage_start = html.index('<section class="theater-stage"')
    intro_start = html.index('id="theater-story-intro"')
    console_start = html.index('<section class="theater-console"')
    assert stage_start < intro_start < console_start
    assert '>背景介绍</p>' in html


def test_frontend_uses_brown_stage_with_galaxy_video():
    """舞台不显示英文副标题，并使用静音循环视频呈现金色银河背景。"""
    html = (ROOT / "templates" / "theater.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "theater.css").read_text(encoding="utf-8")
    assert "Lanlan Theater" not in html
    assert 'id="theater-galaxy-video"' in html
    assert "galaxy-flow-down-right-8s.mp4" in html
    for playback_attribute in ("autoplay", "muted", "loop", "playsinline"):
        assert playback_attribute in html
    assert "initGalaxyCanvas" not in script
    assert ".theater-stage > .theater-galaxy-video" in styles
    assert "object-fit: cover" in styles
    assert (ROOT / "static" / "assets" / "theater" / "galaxy-flow-down-right-8s.mp4").is_file()


def test_frontend_can_collapse_stage_for_more_performance_space():
    """舞台右下角必须可折叠，并通过公开属性驱动紧凑布局和无障碍状态。"""
    html = (ROOT / "templates" / "theater.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "theater.css").read_text(encoding="utf-8")
    assert 'id="theater-stage-toggle"' in html
    assert 'aria-expanded="true"' in html
    assert "function initStageToggle()" in script
    assert "shell.dataset.stageCollapsed" in script
    assert "theater.expandStage" in script
    assert '.theater-shell[data-stage-collapsed="true"]' in styles
    assert "grid-template-rows: 38px 52px minmax(0, 1fr)" in styles


def test_frontend_right_aligns_adaptive_player_turns():
    """推荐选项与自由输入必须共用右对齐、自适应宽度且区别于猫娘对白的玩家气泡。"""
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "css" / "theater.css").read_text(encoding="utf-8")
    assert "user: 'user'" in script
    assert "narrator: 'narration'" in script
    assert "assistant: 'dialogue'" in script
    player_rule = re.search(r"\.theater-turn\.user\s*\{([^}]+)\}", styles)
    assert player_rule
    assert "width: fit-content" in player_rule.group(1)
    assert "margin-left: auto" in player_rule.group(1)
    assert "text-align: right" in player_rule.group(1)
    assert "rgba(83, 99, 156" in player_rule.group(1)


def test_frontend_separates_turn_trace_from_performance_log():
    """演绎日志占用剩余空间，回合摘要固定在其下方，不能依靠易溢出的多行 Grid。"""
    styles = (ROOT / "static" / "css" / "theater.css").read_text(encoding="utf-8")
    performance_rule = re.search(r"\.theater-performance-column\s*\{([^}]+)\}", styles)
    trace_rule = re.search(r"\.theater-trace-panel\s*\{([^}]+)\}", styles)
    log_rule = re.search(r"\.theater-log\s*\{([^}]+)\}", styles)
    assert performance_rule and "display: flex" in performance_rule.group(1)
    assert trace_rule and "flex: 0 0 auto" in trace_rule.group(1)
    assert log_rule and "flex: 1 1 0" in log_rule.group(1)


def test_frontend_removes_deferred_features():
    """当前轻量页面不再包含模式、随机事件、Evidence 和记忆候选入口。"""
    html = (ROOT / "templates" / "theater.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    for removed in ("theater-performance-mode", "theater-random-events", "theater-board-evidence", "theater-memory-candidate"):
        assert removed not in html
    for removed in ("memoryDecision", "submitRandomEvent", "performanceMode"):
        assert removed not in script


def test_frontend_restores_and_reuses_frozen_retry_body():
    """刷新恢复和网络重试继续使用服务端快照及同一序列化请求。"""
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    assert "restoreActiveSession" in script
    assert "const serializedBody" in script
    assert "state_revision_conflict" in script


def test_locale_files_remain_valid_json():
    """八个 locale 必须合法、key 一致并覆盖脚本使用的 theater 文案。"""
    key_sets = []
    for locale in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
        payload = json.loads((ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        assert "theater" in payload
        key_sets.append(set(payload["theater"]))
    assert all(keys == key_sets[0] for keys in key_sets)
    script = (ROOT / "static" / "js" / "theater.js").read_text(encoding="utf-8")
    used_keys = set(re.findall(r"t\('theater\.([^']+)'", script))
    assert used_keys.issubset(key_sets[0])
