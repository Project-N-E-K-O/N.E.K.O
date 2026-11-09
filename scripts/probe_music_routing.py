"""Live, assessment-only music routing probe. Never invokes plugin actions.

Loads production routing, prompt, and plugin schemas from --repo. Uses the
configured summary model unless --tier is explicitly provided. All input is
synthetic; credentials remain in the existing ConfigManager and are not saved.
"""
import argparse
import asyncio
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib


def case(id, text, action=None, *, plugin="netease_music", entry="playlist", args=None, history=None):
    return dict(id=id, text=text, plugin=plugin, entry=entry, action=action,
                args=args or {}, history=history or [])


CASES = [
    case("cloud_like", "收藏这首", "like", entry="cloud_music", args={"song": "current"}),
    case("cloud_named", "收藏这首到Bgm歌单", "add", entry="cloud_music", args={"playlist": "Bgm", "song": "current"}),
    case("cloud_account", "看看我的网易云账号", "account", entry="cloud_music"),
    case("cloud_playlists", "列出我网易云收藏的歌单", "playlists", entry="cloud_music", args={"kind": "subscribed"}),
    case("cloud_daily", "看看网易云今天的日推", "daily", entry="cloud_music"),

    case("local_replay", "播放我们的本地歌单夜读", "replay", args={"name": "夜读"}),
    case("saved_replay", "重播我们自己收藏的夜读歌单", ("list", "replay")),
    case("restart_replay", "刚重启完，继续播放之前保存的夜读歌单", ("list", "replay")),
    case("local_list", "列出我们的本地歌单", "list"),
    case("saved_list", "我们自己收藏了哪些本地歌单？", "list"),
    case("save_current", "把这首存到我们的夜读歌单", "add", entry="cloud_music", args={"playlist": "夜读", "song": "current"}),
    case("save_current_local", "把正在听的歌加入本地歌单夜读", "save_current", args={"name": "夜读"}),
    case("save_current_queue", "把这首加入本地歌单夜读，不要保存整张歌单", "save_current", args={"name": "夜读"}, history=[("assistant", "正在播放网易云歌单中的第二首。")]),
    case("save_whole", "把当前整张歌单保存为本地歌单夜读", "save", args={"name": "夜读"}),
    case("search_public", "在网易云搜一个适合夜读的歌单并播放", "search", args={"name": "夜读"}),
    case("curate", "放点歌陪我看书，偏好轻柔民谣", "curate"),
    case("single", "播放周杰伦的晴天", entry="play_netease_music", args={"query_contains": ["晴天", "周杰伦"]}),
    case("jukebox_play", "用点歌台播放晴天", "play", plugin="jukebox_controller", entry="control_jukebox", args={"query_contains": ["晴天"]}),
    case("local_file", "用本地音频文件播放晴天", "play", plugin="jukebox_controller", entry="control_jukebox", args={"query_contains": ["晴天"]}),
    case("jukebox_next", "点歌台下一首", "next", plugin="jukebox_controller", entry="control_jukebox"),
    case("jukebox_stop", "停止点歌台播放", "stop", plugin="jukebox_controller", entry="control_jukebox"),
    case("queue_next", "网易云歌单切到下一首", "next"),
    case("queue_stop", "停止网易云歌单连播", "stop"),
    case("switch_to_saved", "现在播放我们的本地歌单夜读", "replay", args={"name": "夜读"}, history=[("user", "用点歌台播放晴天"), ("assistant", "点歌台正在播放晴天。")]),
    case("switch_to_jukebox", "换成点歌台播放晴天", "play", plugin="jukebox_controller", entry="control_jukebox", args={"query_contains": ["晴天"]}, history=[("user", "播放我们的本地歌单夜读"), ("assistant", "正在播放夜读歌单。")]),
    case("deny_jukebox", "不要用点歌台，播放我们自己保存的夜读歌单", ("list", "replay")),
    case("deny_saved", "不用我们的歌单，用点歌台播放晴天", "play", plugin="jukebox_controller", entry="control_jukebox", args={"query_contains": ["晴天"]}),
    case("no_play_recommend", "只推荐几首适合夜读的歌，先不要播放", plugin=None),
    case("discuss_difference", "本地歌单和点歌台有什么区别？", plugin=None),
    case("taste_only", "我更喜欢安静一点的民谣", plugin=None),
    case("no_replay_past", "先别播放了，和我聊聊天吧", plugin=None, history=[("user", "播放我们的本地歌单夜读"), ("assistant", "还没有开始播放，正在等你确认。")]),
    case("named_jukebox_saved", "重播我们保存的歌单，名字叫点歌台精选", "replay", args={"name": "点歌台精选"}),
    case("english_saved", "Play our saved local playlist Night Reading", "replay", args={"name": "Night Reading"}),
    case("english_jukebox", "Use the local jukebox to play Yellow", "play", plugin="jukebox_controller", entry="control_jukebox", args={"query_contains": ["Yellow"]}),
    case("ambiguous_known_names", "重播夜读歌单", ("list", "replay"), history=[("assistant", "已保存的歌单：夜读、夜读歌单。")]),
    case("known_saved_name", "重播我们自己收藏的夜读歌单", "replay", args={"name": "夜读"}, history=[("user", "列出我们的歌单"), ("assistant", "已保存的歌单：夜读、通勤。")]),
    case("quoted_saved_name", "播放我们保存的《夜读》歌单", "replay", args={"name": "夜读"}),
    case("literal_suffix", "播放我们保存的歌单，名字是《夜读歌单》", "replay", args={"name": "夜读歌单"}),
    case("recover_missing_name", "那就播夜读吧", "replay", args={"name": "夜读"}, history=[("user", "重播夜读歌单"), ("assistant", "没有找到名为《夜读歌单》的本地歌单，请使用已保存的完整名称。已保存的歌单：夜读, 通勤")]),
    case("recover_literal_suffix", "选夜读歌单，不是夜读", "replay", args={"name": "夜读歌单", "name_confirmed": True}, history=[("user", "播放之前存的歌单"), ("assistant", "请告诉我要重播哪张本地歌单。已保存的歌单：夜读, 夜读歌单")]),
    case("literal_jukebox", "播放我们保存的歌单《点歌台》", "replay", args={"name": "点歌台"}),
]


def resolve_i18n(value):
    if isinstance(value, dict):
        if "$i18n" in value:
            return value.get("default", "")
        return {k: resolve_i18n(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_i18n(v) for v in value]
    return value


def load_plugins(repo):
    plugins = []
    for pid in ["netease_music", "jukebox_controller"]:
        folder = repo / "plugin/plugins" / pid
        manifest = tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))["plugin"]
        metadata = resolve_i18n(json.loads((folder / "plugin.meta.json").read_text(encoding="utf-8")))
        plugins.append({**manifest, "entries": metadata["entries"]})
    return plugins


def check(c, decision, plugins):
    import jsonschema
    errors = []
    actionable = bool(decision.get("has_task") and decision.get("can_execute"))
    if c["plugin"] is None:
        return ["unexpected actionable plugin call"] if actionable else []
    if not actionable:
        return ["no actionable decision"]
    for field, expected in [("plugin_id", c["plugin"]), ("entry_id", c["entry"])]:
        if decision.get(field) != expected:
            errors.append(f"{field}: expected {expected}, got {decision.get(field)}")
    args = decision.get("plugin_args") or {}
    if not isinstance(args, dict):
        return errors + ["plugin_args must be an object"]
    if isinstance(c["action"], tuple):
        if args.get("action") not in c["action"]:
            errors.append("ambiguous replay must list names or defer to the backend name check")
        # A premature name_confirmed flag cannot bypass the backend's pending
        # candidate check. Either lookup route must reach that guard first.
        if args.get("action") == "replay" and args.get("name") not in ("夜读", "夜读歌单"):
            errors.append("ambiguous replay invented an unrelated name")
    elif c["action"] is not None and args.get("action") != c["action"]:
        errors.append(f"action: expected {c['action']}, got {args.get('action')}")
    for key, expected in c["args"].items():
        if key == "query_contains":
            if not all(x.lower() in str(args.get("query", "")).lower() for x in expected):
                errors.append("query missing requested song or artist")
        elif args.get(key) != expected:
            errors.append(f"{key}: expected {expected}, got {args.get(key)}")
    if c["action"] == "curate":
        tracks = args.get("tracks", [])
        def playable_pair(track):
            if not isinstance(track, str):
                return False
            title, separator, artist = track.partition("|")
            title, artist = title.strip(), artist.strip()
            return bool(separator and title and artist and len(f"{title} {artist}") <= 100)
        if (not isinstance(tracks, list) or not 6 <= len(tracks) <= 8
                or not all(playable_pair(t) for t in tracks)):
            errors.append("curate must provide 6–8 title | artist strings")
    for p in plugins:
        if p["id"] == decision.get("plugin_id"):
            for entry in p["entries"]:
                if entry["id"] == decision.get("entry_id"):
                    try:
                        jsonschema.validate(args, entry["input_schema"])
                    except jsonschema.ValidationError as error:
                        errors.append("schema: " + error.message)
    return errors


async def run(options):
    if options.repeat < 1 or options.concurrency < 1:
        raise ValueError("repeat and concurrency must be positive")
    output = options.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new or empty output directory for each run")
    repo = options.repo.resolve()
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    if not options.pipeline:
        print("Assessment-only mode; use --pipeline for the complete analyze entry", file=sys.stderr)
    from brain.task_executor import DirectTaskExecutor
    from utils.config_manager import get_config_manager

    plugins = load_plugins(repo)
    config = get_config_manager()
    api = config.get_model_api_config(options.tier)
    requested = set(options.only.split(",")) if options.only else set()
    unknown = requested - {c["id"] for c in CASES}
    if unknown:
        raise ValueError(f"Unknown case ids: {sorted(unknown)}")
    selected = [c for c in CASES if not requested or c["id"] in requested]
    output.mkdir(parents=True, exist_ok=True)
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], text=True)
    metadata = {"head": head.strip(),
                "tier": options.tier, "model": api["model"], "lang": options.lang,
                "repeat": options.repeat, "cases": len(selected), "pipeline": options.pipeline,
                "prompt_sha256": hashlib.sha256((repo / "config/prompts/prompts_agent.py").read_bytes()).hexdigest(),
                "scope": "Real production routing and schemas; two music plugins available; synthetic conversations; no execution, memory lookup, or UI."}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
    results = []
    sem = asyncio.Semaphore(options.concurrency)
    async def one(c, repeat):
        async with sem:
            executor = None
            assessments = []
            data = {}
            raw_task = None
            errors = []
            started = time.monotonic()
            try:
                executor = DirectTaskExecutor()
                original_assess = executor._assess_user_plugin

                async def observe_assessment(*args, **kwargs):
                    assessment = await original_assess(*args, **kwargs)
                    assessments.append(dataclasses.asdict(assessment))
                    return assessment

                executor._assess_user_plugin = observe_assessment
                original_get = executor._get_llm
                if options.tier != "summary":
                    executor._get_llm = lambda **kwargs: original_get(**{**kwargs, "tier": options.tier})
                messages = [{"role": role, "text": text} for role, text in c["history"]]
                messages.append({"role": "user", "text": c["text"]})
                if options.pipeline:
                    async def provider(force_refresh=False):
                        return plugins
                    executor.plugin_list_provider = provider
                    task = await executor.analyze_and_execute(
                        messages, agent_flags={"user_plugin_enabled": True,
                                               "computer_use_enabled": False,
                                               "browser_use_enabled": False}, lang=options.lang)
                    raw_task = dataclasses.asdict(task) if task else None
                    data = {"has_task": bool(task and task.has_task),
                            "can_execute": bool(task and task.execution_method == "user_plugin"),
                            "plugin_id": task.tool_name if task else None,
                            "entry_id": task.entry_id if task else None,
                            "plugin_args": task.tool_args if task else None,
                            "reason": task.reason if task else None}
                else:
                    decision = await executor._assess_user_plugin(executor._format_messages(messages), plugins, lang=options.lang)
                    data = dataclasses.asdict(decision)
                errors = check(c, data, plugins)
                if not assessments:
                    errors.append("No model assessment observed")
                for assessment in assessments:
                    reason = str(assessment.get("reason", "")).lower()
                    if reason.startswith(("assessment error:", "json parse error:", "empty llm response")):
                        errors.append("Model assessment failed: " + reason)
            except Exception as exc:
                errors.append(f"Case raised {type(exc).__name__}")
            finally:
                if executor is not None:
                    executor._close_all_llms()
                    if executor._cleanup_tasks:
                        await asyncio.gather(*executor._cleanup_tasks, return_exceptions=True)
            result = {"case": c, "repeat": repeat, "decision": data, "task": raw_task,
                      "assessments": assessments, "errors": errors,
                      "seconds": round(time.monotonic() - started, 2)}
            results.append(result)
            with (output / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=True) + "\n")
            print(json.dumps({"case": c["id"], "repeat": repeat, "passed": not errors, "errors": errors}, ensure_ascii=True), file=sys.stderr, flush=True)
    await asyncio.gather(*(one(c, r) for r in range(1, options.repeat + 1) for c in selected))
    summary = {**metadata, "passed": sum(not r["errors"] for r in results), "total": len(results),
               "failed": [{"id": r["case"]["id"], "repeat": r["repeat"], "errors": r["errors"]} for r in results if r["errors"]]}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2), file=sys.stderr)
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tier", default="summary")
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--only", default="")
    parser.add_argument("--pipeline", action="store_true")
    sys.exit(asyncio.run(run(parser.parse_args())))
