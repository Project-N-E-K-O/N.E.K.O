from pathlib import Path

import pytest

from utils.new_character_greeting_state import mark_pending


@pytest.mark.unit
@pytest.mark.asyncio
async def test_new_character_greeting_state_uses_runtime_state_dir(tmp_path: Path):
    class Config:
        local_state_dir = tmp_path / "anchor" / "state"
        runtime_state_dir = tmp_path / "selected" / "state"

        def ensure_runtime_state_directory(self):
            self.runtime_state_dir.mkdir(parents=True, exist_ok=True)
            return True

    config = Config()
    await mark_pending(config, "妮可", source="test")

    assert (config.runtime_state_dir / "new_character_greeting_state.json").is_file()
    assert not (config.local_state_dir / "new_character_greeting_state.json").exists()
