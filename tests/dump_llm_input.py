# -*- coding: utf-8 -*-
"""
Dump LLM Input — reconstruct the full messages array that the AI model
receives at the start of a conversation turn.

This script reuses the existing project modules (ConfigManager, PersonaManager,
RecentHistoryManager, ReflectionEngine, etc.) to build the exact same system
prompt that ``memory_server.py`` + ``main_logic/core.py`` would produce at
runtime, then prints it in the OpenAI ``messages`` format.

Usage:
    python tests/dump_llm_input.py
    python tests/dump_llm_input.py --character 蓝蓝
    python tests/dump_llm_input.py --character 蓝蓝 --user-message "你好呀"
    python tests/dump_llm_input.py --lang en
    python tests/dump_llm_input.py --output prompt_dump.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

# Ensure project root is on sys.path so all internal imports work.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── venv guard ───────────────────────────────────────────────────────
# The project requires Python 3.11 with dependencies installed in .venv.
# If the user accidentally runs this with the system Python, detect it
# early and either re-exec under the correct interpreter or print help.
_in_venv = (
    hasattr(sys, "real_prefix")
    or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
)
if not _in_venv:
    # Try to find and re-launch under the project venv.
    _candidates = [
        os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe"),  # Windows
        os.path.join(PROJECT_ROOT, ".venv", "bin", "python"),          # Linux/macOS
    ]
    _venv_python = next((p for p in _candidates if os.path.isfile(p)), None)
    if _venv_python:
        import subprocess
        _script = os.path.abspath(__file__)
        _ret = subprocess.call([_venv_python, _script] + sys.argv[1:])
        sys.exit(_ret)
    else:
        print(
            "[ERROR] Not running inside the project virtual environment, "
            "and .venv was not found.\n"
            "Please run with:  .venv\\Scripts\\python.exe tests\\dump_llm_input.py\n"
            "Or activate the venv first:  .venv\\Scripts\\activate",
            file=sys.stderr,
        )
        sys.exit(1)

# ── project imports ──────────────────────────────────────────────────
from utils.config_manager import get_config_manager
from config.prompts_chara import get_lanlan_prompt, is_default_prompt
from config.prompts_sys import _loc
from config.prompts_sys import SESSION_INIT_PROMPT, SESSION_INIT_PROMPT_AGENT, CONTEXT_SUMMARY_READY
from config.prompts_memory import (
    PERSONA_HEADER,
    INNER_THOUGHTS_HEADER,
    INNER_THOUGHTS_DYNAMIC,
    CHAT_GAP_NOTICE,
    CHAT_GAP_LONG_HINT,
    CHAT_GAP_CURRENT_TIME,
    CHAT_HOLIDAY_CONTEXT,
)
from utils.config_manager import get_reserved
from memory import (
    CompressedRecentHistoryManager,
    ImportantSettingsManager,
    TimeIndexedMemory,
    FactStore,
    PersonaManager,
    ReflectionEngine,
)
from utils.frontend_utils import get_timestamp
from utils.time_format import format_elapsed as _format_elapsed
from utils.tokenize import count_tokens as _dump_count_tokens


# ── helpers ──────────────────────────────────────────────────────────

def _format_legacy_settings_as_text(settings: dict, lanlan_name: str) -> str:
    """Mirror of the same function in memory_server.py."""
    if not settings:
        return f"{lanlan_name}记得：（暂无记录）"
    sections = []
    for name, data in settings.items():
        if not isinstance(data, dict) or not data:
            continue
        lines = []
        for key, value in data.items():
            if value is None or value == '' or value == []:
                continue
            if isinstance(value, list):
                value_str = '、'.join(str(v) for v in value)
            elif isinstance(value, dict):
                parts = [f"{k}: {v}" for k, v in value.items() if v is not None and v != '']
                value_str = '、'.join(parts) if parts else str(value)
            else:
                value_str = str(value)
            lines.append(f"- {key}：{value_str}")
        if lines:
            sections.append(f"关于{name}：\n" + "\n".join(lines))
    if not sections:
        return f"{lanlan_name}记得：（暂无记录）"
    return f"{lanlan_name}记得：\n" + "\n".join(sections)


def build_memory_context_structured(
    lanlan_name: str,
    master_name: str,
    name_mapping: dict,
    lang: str,
    config_manager,
    recent_history_manager: CompressedRecentHistoryManager,
    settings_manager: ImportantSettingsManager,
    time_manager: TimeIndexedMemory,
    persona_manager: PersonaManager,
    reflection_engine: ReflectionEngine,
) -> dict:
    """Like ``build_memory_context`` but returns structured components."""

    brackets_pattern = re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
    local_name_mapping = dict(name_mapping)
    local_name_mapping['ai'] = lanlan_name

    # ── Persona (long-term memory) ──
    pending_reflections = reflection_engine.get_pending_reflections(lanlan_name)
    confirmed_reflections = reflection_engine.get_confirmed_reflections(lanlan_name)

    persona_header = _loc(PERSONA_HEADER, lang).format(name=lanlan_name)
    persona_md = persona_manager.render_persona_markdown(
        lanlan_name, pending_reflections, confirmed_reflections,
    )
    if not persona_md:
        persona_md = _format_legacy_settings_as_text(
            settings_manager.get_settings(lanlan_name), lanlan_name
        ) + "\n"

    # ── Inner thoughts header ──
    inner_thoughts_header = _loc(INNER_THOUGHTS_HEADER, lang).format(name=lanlan_name)
    inner_thoughts_dynamic = _loc(INNER_THOUGHTS_DYNAMIC, lang).format(
        name=lanlan_name,
        time=get_timestamp(),
    )

    # ── Recent history ──
    recent_history_entries: list[dict] = []
    for item in recent_history_manager.get_recent_history(lanlan_name):
        speaker = local_name_mapping[item.type]
        if isinstance(item.content, str):
            cleaned = brackets_pattern.sub('', item.content).strip()
        else:
            texts = [brackets_pattern.sub('', j['text']).strip()
                     for j in item.content if j['type'] == 'text']
            cleaned = "\n".join(texts)
        recent_history_entries.append({"speaker": speaker, "content": cleaned})

    # ── Chat gap hint ──
    time_context = ""
    try:
        last_time = time_manager.get_last_conversation_time(lanlan_name)
        if last_time:
            gap = datetime.now() - last_time
            gap_seconds = gap.total_seconds()
            if gap_seconds >= 1800:
                elapsed = _format_elapsed(lang, gap_seconds)
                if gap_seconds >= 18000:
                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    time_context += _loc(CHAT_GAP_CURRENT_TIME, lang).format(now=now_str)
                    time_context += _loc(CHAT_GAP_NOTICE, lang).format(master=master_name, elapsed=elapsed)
                    time_context += _loc(CHAT_GAP_LONG_HINT, lang).format(name=lanlan_name, master=master_name) + "\n"
                else:
                    time_context += _loc(CHAT_GAP_NOTICE, lang).format(master=master_name, elapsed=elapsed) + "\n"
    except Exception as e:
        print(f"[WARN] Failed to compute chat gap: {e}")

    # ── Holiday context ──
    holiday_context = ""
    try:
        from utils.holiday_cache import get_holiday_context_line
        holiday_name = get_holiday_context_line(lang)
        if holiday_name:
            holiday_context = _loc(CHAT_HOLIDAY_CONTEXT, lang).format(holiday=holiday_name)
    except Exception:
        pass

    return {
        "persona_header": persona_header,
        "persona_content": persona_md,
        "inner_thoughts_header": inner_thoughts_header,
        "inner_thoughts_dynamic": inner_thoughts_dynamic,
        "recent_history": recent_history_entries,
        "time_context": time_context,
        "holiday_context": holiday_context,
    }


def _flatten_memory_components(components: dict) -> str:
    """Combine structured memory components back into a flat string."""
    result = components["persona_header"]
    result += components["persona_content"]
    result += components["inner_thoughts_header"]
    result += components["inner_thoughts_dynamic"]
    for entry in components["recent_history"]:
        result += f"{entry['speaker']} | {entry['content']}\n"
    result += components["time_context"]
    result += components["holiday_context"]
    return result


def build_memory_context(
    lanlan_name: str,
    master_name: str,
    name_mapping: dict,
    lang: str,
    config_manager,
    recent_history_manager: CompressedRecentHistoryManager,
    settings_manager: ImportantSettingsManager,
    time_manager: TimeIndexedMemory,
    persona_manager: PersonaManager,
    reflection_engine: ReflectionEngine,
) -> str:
    """Reproduce the logic of ``GET /new_dialog/{lanlan_name}`` in memory_server.py."""
    components = build_memory_context_structured(
        lanlan_name, master_name, name_mapping, lang, config_manager,
        recent_history_manager, settings_manager, time_manager,
        persona_manager, reflection_engine,
    )
    return _flatten_memory_components(components)


def build_initial_prompt(lanlan_name: str, lanlan_prompt: str, lang: str) -> str:
    """Reproduce ``LLMSessionManager._build_initial_prompt`` (non-agent path)."""
    prompt = _loc(SESSION_INIT_PROMPT, lang).format(name=lanlan_name) + lanlan_prompt
    return prompt


def build_full_system_message(
    lanlan_name: str,
    master_name: str,
    lang: str,
    config_manager,
    recent_history_manager,
    settings_manager,
    time_manager,
    persona_manager,
    reflection_engine,
    name_mapping: dict,
    lanlan_prompt: str,
) -> str:
    """Combine initial prompt + memory context + closing marker."""

    initial = build_initial_prompt(lanlan_name, lanlan_prompt, lang)

    memory_ctx = build_memory_context(
        lanlan_name=lanlan_name,
        master_name=master_name,
        name_mapping=name_mapping,
        lang=lang,
        config_manager=config_manager,
        recent_history_manager=recent_history_manager,
        settings_manager=settings_manager,
        time_manager=time_manager,
        persona_manager=persona_manager,
        reflection_engine=reflection_engine,
    )

    closing = _loc(CONTEXT_SUMMARY_READY, lang).format(
        name=lanlan_name, master=master_name,
    )

    return initial + memory_ctx + closing


def build_messages(system_content: str, user_message: str | None = None) -> list[dict]:
    """Build the OpenAI-style messages array."""
    messages = [{"role": "system", "content": system_content}]
    if user_message:
        messages.append({"role": "user", "content": user_message})
    return messages


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Reconstruct and dump the full LLM input for a NEKO conversation.",
    )
    parser.add_argument(
        "--character", "-c",
        help="Character name (猫娘 name). If omitted, uses the current active character.",
    )
    parser.add_argument(
        "--user-message", "-m",
        help="Optional simulated user message to append as the first turn.",
    )
    parser.add_argument(
        "--lang", "-l",
        default=None,
        help="Language code (zh/en/ja/ko/ru). Defaults to system language.",
    )
    _default_output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_dump.json")
    parser.add_argument(
        "--output", "-o",
        default=_default_output,
        help=f"Output file path. Defaults to {_default_output}",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Print only the raw system prompt text (no JSON wrapper).",
    )
    parser.add_argument(
        "--flat", action="store_true",
        help="Output the old flat OpenAI messages format instead of the structured format.",
    )
    args = parser.parse_args()

    # ── Initialize components (same order as memory_server.py) ──
    config_manager = get_config_manager()
    config_manager.ensure_memory_directory()

    (
        master_name,
        current_character,
        master_basic_config,
        catgirl_data,
        name_mapping,
        lanlan_prompt_map,
        time_store,
        setting_store,
        recent_log,
    ) = config_manager.get_character_data()

    catgirl_names = list(catgirl_data.keys())

    # Resolve target character
    target = args.character or current_character
    if target not in catgirl_names:
        print(f"[ERROR] Character '{target}' not found. Available: {catgirl_names}")
        sys.exit(1)

    # Resolve language
    if args.lang:
        lang = args.lang
    else:
        try:
            from utils.language_utils import get_global_language
            lang = get_global_language()
        except Exception:
            lang = 'zh'

    # Build the character prompt (with placeholders replaced)
    stored_prompt = get_reserved(
        catgirl_data.get(target, {}), 'system_prompt',
        default=None, legacy_keys=('system_prompt',),
    )
    if stored_prompt is None or is_default_prompt(stored_prompt):
        prompt_template = get_lanlan_prompt(lang)
    else:
        prompt_template = stored_prompt

    lanlan_prompt = (
        prompt_template
        .replace('{LANLAN_NAME}', target)
        .replace('{MASTER_NAME}', master_name)
    )

    # Initialize memory components
    recent_history_manager = CompressedRecentHistoryManager()
    settings_manager = ImportantSettingsManager()
    time_manager = TimeIndexedMemory(recent_history_manager)
    fact_store = FactStore(time_indexed_memory=time_manager)
    persona_manager = PersonaManager()
    reflection_engine = ReflectionEngine(fact_store, persona_manager)

    # ── Build components ──
    session_init = _loc(SESSION_INIT_PROMPT, lang).format(name=target)

    memory_components = build_memory_context_structured(
        lanlan_name=target,
        master_name=master_name,
        name_mapping=name_mapping,
        lang=lang,
        config_manager=config_manager,
        recent_history_manager=recent_history_manager,
        settings_manager=settings_manager,
        time_manager=time_manager,
        persona_manager=persona_manager,
        reflection_engine=reflection_engine,
    )

    closing = _loc(CONTEXT_SUMMARY_READY, lang).format(
        name=target, master=master_name,
    )

    memory_flat = _flatten_memory_components(memory_components)
    system_content = session_init + lanlan_prompt + memory_flat + closing

    # ── Format output ──
    if args.raw:
        output_text = system_content
        if args.user_message:
            output_text += f"\n\n--- [User Message] ---\n{args.user_message}"
    elif args.flat:
        messages = build_messages(system_content, args.user_message)
        output_text = json.dumps(messages, ensure_ascii=False, indent=2)
    else:
        structured_output = {
            "metadata": {
                "character": target,
                "master": master_name,
                "language": lang,
                "dump_time": datetime.now().isoformat(timespec="seconds"),
                "system_prompt_chars": len(system_content),
                "approx_tokens": _dump_count_tokens(system_content),
            },
            "background": {
                "session_init": session_init,
                "character_prompt": lanlan_prompt,
                "persona_header": memory_components["persona_header"],
                "persona_content": memory_components["persona_content"],
            },
            "conversation": {
                "context_header": memory_components["inner_thoughts_header"],
                "context_timestamp": memory_components["inner_thoughts_dynamic"],
                "recent_history": memory_components["recent_history"],
                "time_context": memory_components["time_context"] or None,
                "holiday_context": memory_components["holiday_context"] or None,
            },
            "closing": closing,
            "user_message": args.user_message,
        }
        output_text = json.dumps(structured_output, ensure_ascii=False, indent=2)

    # ── Output ──
    output_path = os.path.abspath(args.output)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output_text)

    # ── Summary stats ──
    sys_len = len(system_content)
    approx_tokens = _dump_count_tokens(system_content)
    print(f"\n{'='*60}")
    print(f"角色          : {target}")
    print(f"主人          : {master_name}")
    print(f"语言          : {lang}")
    print(f"System prompt : {sys_len} 字符 ({approx_tokens} tokens, tiktoken o200k_base)")
    print(f"输出格式      : {'raw' if args.raw else 'flat' if args.flat else 'structured'}")
    print(f"{'='*60}")
    print(f"\n[OK] 输出完成，文件已保存至：{output_path}")


if __name__ == "__main__":
    main()
