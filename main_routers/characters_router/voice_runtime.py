"""Provider-neutral helpers used by voice routes."""


def config_value_is_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off', ''}:
            return False
    return bool(value)


def local_voice_clone_tts_base_url(tts_config: dict, core_config: dict | None = None) -> str:
    return str(
        tts_config.get('base_url')
        or tts_config.get('url')
        or (core_config or {}).get('ttsModelUrl')
        or (core_config or {}).get('TTS_MODEL_URL')
        or ''
    ).strip()


def is_local_voice_clone_tts_config(tts_config: dict, core_config: dict | None = None) -> bool:
    provider = str((core_config or {}).get('ttsModelProvider') or '').strip()
    if provider == 'vllm_omni':
        return False
    base_url = local_voice_clone_tts_base_url(tts_config, core_config)
    return bool(tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://')))


_config_value_is_enabled = config_value_is_enabled
_local_voice_clone_tts_base_url = local_voice_clone_tts_base_url
_is_local_voice_clone_tts_config = is_local_voice_clone_tts_config
