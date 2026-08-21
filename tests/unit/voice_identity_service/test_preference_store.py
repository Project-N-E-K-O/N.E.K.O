from __future__ import annotations

from pathlib import Path

import pytest

from main_logic.voice_identity_service.preference_store import (
    VoiceIdentityPreferenceStore,
    VoiceIdentityPreferenceStoreError,
)


def test_missing_preference_defaults_disabled_and_round_trips(tmp_path: Path) -> None:
    store = VoiceIdentityPreferenceStore(tmp_path / "voice_identity.settings.json")

    assert store.load() is False
    store.save(True)
    assert store.load() is True
    store.save(False)
    assert store.load() is False


def test_corrupt_preference_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "voice_identity.settings.json"
    path.write_text('{"requested_enabled":"yes","schema_version":1}', encoding="utf-8")

    with pytest.raises(VoiceIdentityPreferenceStoreError, match="corrupt"):
        VoiceIdentityPreferenceStore(path).load()


@pytest.mark.asyncio
async def test_async_preference_methods_match_sync_contract(tmp_path: Path) -> None:
    store = VoiceIdentityPreferenceStore(tmp_path / "voice_identity.settings.json")

    await store.asave(True)

    assert await store.aload() is True
