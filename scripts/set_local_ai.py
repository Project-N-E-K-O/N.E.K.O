# -*- coding: utf-8 -*-
"""Switch N.E.K.O text / vision / TTS to local endpoints (Ollama + Edge TTS bridge).

Important product facts (upstream N.E.K.O):
  - assistApi must be a known profile key (openai/qwen/...). "ollama" is INVALID
    and silently falls back to qwen → DashScope 401 with empty keys.
  - coreApi is realtime WebSocket (ASR + omni). Ollama is HTTP chat only —
    never point core at Ollama.
  - Custom OpenAI-compatible TTS only wins when the character voice_id matches
    ttsVoiceId (clone voices like voice-tone-* force CosyVoice instead).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

OLLAMA_BASE = "http://127.0.0.1:11434/v1"
EDGE_TTS_BASE = "http://127.0.0.1:19000/v1"
DEFAULT_CHAT_MODEL = "qwen2.5:7b"
DEFAULT_VISION_MODEL = "llava"
DEFAULT_EDGE_VOICE = "ja-JP-NanamiNeural"
OLLAMA_KEY = "ollama"

# Bad keys left by AIYS / older seeds that break resolution.
_BAD_URL_KEYS = (
    "assist:ollama",
    "core:openai",  # must stay WSS realtime, not HTTP Ollama
)


def config_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "N.E.K.O" / "config"
    return Path.home() / "Documents" / "N.E.K.O" / "config"


def config_path() -> Path:
    return config_dir() / "core_config.json"


def characters_path() -> Path:
    return config_dir() / "characters.json"


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def _model_slot(prefix: str, model: str, url: str = OLLAMA_BASE, key: str = OLLAMA_KEY) -> dict:
    return {
        f"{prefix}ModelProvider": "custom",
        f"{prefix}ModelUrl": url,
        f"{prefix}ModelId": model,
        f"{prefix}ModelApiKey": key,
    }


def build_patch(chat_model: str, vision_model: str, edge_voice: str, use_gsv: bool) -> dict:
    patch: dict = {
        "enableCustomApi": True,
        # Text assist: use a REAL profile key; override URL to Ollama.
        "assistApi": "openai",
        "assistApiKeyOpenai": OLLAMA_KEY,
        # Realtime Core stays free (WSS). Mic ASR is overridden to local
        # faster-whisper via asrProvider (see main_logic/asr_client).
        "coreApi": "free",
        "coreApiKey": "free-access",
        "asrProvider": "faster_whisper",
        # Shorthand model ids (some UI paths still read these)
        "conversationModel": chat_model,
        "summaryModel": chat_model,
        "correctionModel": chat_model,
        "emotionModel": chat_model,
        "visionModel": vision_model,
        "agentModel": chat_model,
        # Chat shows Chinese; TTS speaks Japanese (same meaning, dual tags).
        "dualLanguageSpeech": True,
        # Omni / realtime follows Core (free WSS), not custom HTTP.
        "omniModelProvider": "follow_core",
        "omniModelUrl": "",
        "omniModelId": "",
        "omniModelApiKey": "",
        "resolvedProviderUrls": {
            "assist:openai": OLLAMA_BASE,
            "assist:free": "https://www.lanlan.tech/text/v1",
            "core:free": "wss://www.lanlan.tech/core",
        },
    }
    for prefix in (
        "conversation",
        "summary",
        "correction",
        "emotion",
        "agent",
        "gameMain",
        "gameSummary",
    ):
        patch.update(_model_slot(prefix, chat_model))
    patch.update(_model_slot("vision", vision_model))

    if use_gsv:
        patch.update(
            {
                "ttsModelProvider": "gptsovits",
                "ttsProvider": "gptsovits",
                "ttsModelUrl": "http://127.0.0.1:9880",
                "ttsVoiceId": "",
                "ttsModelId": "",
                "ttsModelApiKey": "",
                "gptsovitsEnabled": True,
            }
        )
    else:
        patch.update(
            {
                "ttsModelProvider": "custom",
                "ttsProvider": "custom",
                "ttsModelUrl": EDGE_TTS_BASE,
                "ttsModelId": "edge-tts",
                "ttsModelApiKey": "local",
                "ttsVoiceId": edge_voice,
                "gptsovitsEnabled": False,
                "disableTts": False,
            }
        )
    return patch


def force_independent_asr_preference(*, dry_run: bool) -> None:
    """Persist independentAsrEnabled=true so Core uses built-in independent ASR."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from utils.preferences import (
            load_global_conversation_settings,
            save_global_conversation_settings,
        )
    except Exception as exc:
        print(f"  WARN: cannot load preferences module: {exc}")
        return
    settings = dict(load_global_conversation_settings() or {})
    if settings.get("independentAsrEnabled") is True:
        print("  independentAsrEnabled: already true")
        return
    settings["independentAsrEnabled"] = True
    if dry_run:
        print("  [dry-run] would set independentAsrEnabled=true")
        return
    ok = save_global_conversation_settings(settings)
    print(
        "  independentAsrEnabled: set true"
        if ok
        else "  WARN: failed to save independentAsrEnabled"
    )


def sync_character_voices(edge_voice: str, dry_run: bool) -> int:
    """Point character voice_id at Edge voice so custom TTS is selected."""
    path = characters_path()
    data = load_json(path)
    cats = data.get("猫娘")
    if not isinstance(cats, dict):
        return 0
    changed = 0
    for name, profile in cats.items():
        if not isinstance(profile, dict):
            continue
        reserved = profile.get("_reserved")
        if not isinstance(reserved, dict):
            reserved = {}
            profile["_reserved"] = reserved
        old = str(reserved.get("voice_id") or "").strip()
        if old == edge_voice:
            continue
        if old and not str(reserved.get("voice_id_before_local_ai") or "").strip():
            reserved["voice_id_before_local_ai"] = old
        reserved["voice_id"] = edge_voice
        changed += 1
        print(f"  voice sync: {name!r} {old or '(empty)'} -> {edge_voice}")
    if changed and not dry_run:
        save_json(path, data)
    return changed


def clean_resolved_urls(urls: dict, patch_urls: dict) -> dict:
    out = dict(urls)
    for bad in _BAD_URL_KEYS:
        out.pop(bad, None)
    out.update(patch_urls or {})
    return out


def verify_runtime_config() -> None:
    """Best-effort check via ConfigManager (needs project on sys.path)."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        cm.clear_cache() if hasattr(cm, "clear_cache") else None
        conv = cm.get_model_api_config("conversation")
        vision = cm.get_model_api_config("vision")
        tts = cm.get_model_api_config("tts_custom")
        core = cm.get_core_config() or {}
        print("\n[verify]")
        print(
            f"  assistApi={core.get('assistApi')!r} coreApi={core.get('CORE_API_TYPE') or core.get('coreApi')!r}"
        )
        print(
            f"  conversation: model={conv.get('model')!r} url={conv.get('base_url')!r} "
            f"key={'SET' if conv.get('api_key') else 'MISSING'}"
        )
        print(
            f"  vision: model={vision.get('model')!r} url={vision.get('base_url')!r} "
            f"key={'SET' if vision.get('api_key') else 'MISSING'}"
        )
        print(
            f"  tts_custom: model={tts.get('model')!r} url={tts.get('base_url')!r} "
            f"key={'SET' if tts.get('api_key') else 'MISSING'}"
        )
        print(f"  asrProvider={core.get('asrProvider')!r}")
        ok = bool(conv.get("model") and conv.get("api_key") and conv.get("base_url"))
        if "11434" not in str(conv.get("base_url") or ""):
            print("  WARN: conversation URL is not Ollama (11434)")
            ok = False
        if str(core.get("CORE_API_TYPE") or core.get("coreApi") or "") == "openai":
            print("  WARN: coreApi=openai will break local/free voice; expected free")
        if str(core.get("asrProvider") or "").strip().lower() != "faster_whisper":
            print("  WARN: asrProvider is not faster_whisper")
            ok = False
        else:
            try:
                from main_logic.asr_client import (
                    _resolve_asr_selection,
                    builtin_independent_asr_forced,
                )

                selection = _resolve_asr_selection("free")
                print(f"  asr_selection: {selection.provider_key!r}")
                print(f"  builtin_independent_forced: {builtin_independent_asr_forced()}")
                if selection.provider_key != "faster_whisper":
                    print("  WARN: free core did not resolve to faster_whisper")
                    ok = False
                if not builtin_independent_asr_forced():
                    print("  WARN: builtin independent ASR not forced")
                    ok = False
            except Exception as asr_exc:
                print(f"  WARN: ASR resolve failed: {asr_exc}")
                ok = False
        print("  status:", "OK" if ok else "BAD")
    except Exception as e:
        print(f"\n[verify] skipped ({e})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure N.E.K.O for local Ollama + local TTS")
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--edge-voice", default=DEFAULT_EDGE_VOICE)
    parser.add_argument("--tts", choices=("edge", "gptsovits"), default="edge")
    parser.add_argument(
        "--no-sync-voice",
        action="store_true",
        help="Do not rewrite character voice_id to Edge voice",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true", help="Resolve config via ConfigManager")
    args = parser.parse_args()

    path = config_path()
    current = load_json(path)
    patch = build_patch(args.chat_model, args.vision_model, args.edge_voice, args.tts == "gptsovits")

    urls = current.get("resolvedProviderUrls")
    if not isinstance(urls, dict):
        urls = {}
    merged = dict(current)
    merged.update(patch)
    merged["resolvedProviderUrls"] = clean_resolved_urls(urls, patch.get("resolvedProviderUrls") or {})
    # Drop invalid leftover fields that confuse humans / old seeds
    merged.pop("assistApiKeyOllama", None)
    merged["_neko_local_ai"] = {
        "mode": "ollama+local-tts+local-asr",
        "chat_model": args.chat_model,
        "vision_model": args.vision_model,
        "tts": args.tts,
        "asr": "faster_whisper",
        "edge_voice": args.edge_voice,
        "core": "free",
        "assist": "openai@ollama",
    }

    print(f"Config file: {path}")
    print(f"Ollama chat : {args.chat_model} @ {OLLAMA_BASE}")
    print(f"Ollama vision: {args.vision_model} @ {OLLAMA_BASE}")
    print("Core (voice LLM): free @ wss://www.lanlan.tech/core")
    print("Mic ASR       : local faster-whisper (asrProvider)")
    if args.tts == "edge":
        print(f"TTS         : Edge bridge @ {EDGE_TTS_BASE} voice={args.edge_voice}")
    else:
        print("TTS         : GPT-SoVITS @ http://127.0.0.1:9880")
    print()
    print("NOTE: Mic ASR is local (faster-whisper). Voice LLM reply may still use free Core.")
    print("      First ASR run downloads the Whisper model (default: small).")
    print("      Do NOT launch AIYS shell — its seed writes invalid assistApi=ollama.")
    print()

    ollama_ok = probe("http://127.0.0.1:11434/api/tags")
    print("Ollama reachable:", "YES" if ollama_ok else "NO (install/start Ollama, then: ollama pull ...)")
    if args.tts == "edge":
        edge_ok = probe("http://127.0.0.1:19000/health")
        print("Edge bridge  :", "YES" if edge_ok else "NO (run start_edge_tts.bat)")

    if args.tts == "edge" and not args.no_sync_voice:
        print("\nSync character voices for Edge TTS:")
        n = sync_character_voices(args.edge_voice, dry_run=args.dry_run)
        if n == 0:
            print("  (no change needed)")
        elif args.dry_run:
            print(f"  [dry-run] would update {n} character(s)")

    print("\nForce built-in independent ASR (not Omni-native / CORE free route):")
    force_independent_asr_preference(dry_run=args.dry_run)

    if args.dry_run:
        print("\n[dry-run] not writing core_config")
        print(json.dumps(patch, ensure_ascii=False, indent=2))
        return 0

    save_json(path, merged)
    marker = path.parent / ".neko_local_ai_seeded"
    marker.write_text("seeded by scripts/set_local_ai.py\n", encoding="utf-8")
    # Neutralize AIYS re-seed marker so AIYS won't think ollama assist is fine
    aiys_marker = path.parent / ".aiys_ollama_seeded"
    if aiys_marker.is_file():
        aiys_marker.write_text(
            "invalidated: assistApi=ollama is not a N.E.K.O profile; use set_local_ai.bat\n",
            encoding="utf-8",
        )
    print(f"\nWrote {path}")
    print("Restart N.E.K.O desktop / launcher for settings to take effect.")

    if args.verify:
        verify_runtime_config()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
