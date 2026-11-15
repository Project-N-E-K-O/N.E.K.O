"""Give legacy focused store tests explicit, isolated external installation paths.

The production default stays fail-closed. Constructors remain the actual classes,
so existing SQL triggers, monkeypatch fault injection and retries stay meaningful.
"""

import hashlib
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_chat_sync_writer(monkeypatch, tmp_path):
    from services.chat_sync.writer import ChatSyncWriter, registered_writer
    from services.chat_sync.runtime import DesktopChatSyncRuntime, DesktopSyncState
    from services.chat_sync.ledger import ChatSyncLedger
    from services.chat_sync.source import ChatOriginalSource
    from services.chat_sync.scheduler import ChatSyncScheduler
    from services.chat_sync.profile_sync import ProfileStore

    original_runtime = DesktopChatSyncRuntime.__init__

    def runtime_init(self, directory, *args, **kwargs):
        directory = Path(directory)
        assert directory.resolve().is_relative_to(tmp_path.resolve())
        kwargs.setdefault('witness_path', tmp_path / 'installation-witnesses' / (hashlib.sha256(str(directory.resolve()).encode()).hexdigest() + '.json'))
        original_runtime(self, directory, *args, **kwargs)

    monkeypatch.setattr(DesktopChatSyncRuntime, '__init__', runtime_init)

    def wrap(original):
        def store_init(self, path, *args, **kwargs):
            path = Path(path)
            assert path.resolve().is_relative_to(tmp_path.resolve())
            writer = registered_writer(path.parent)
            if writer is None:
                writer = ChatSyncWriter(path.parent, tmp_path / 'installation-witnesses' / (hashlib.sha256(str(path.parent.resolve()).encode()).hexdigest() + '.json'),
                                        database_names=(path.name,))
            elif path.name not in writer.database_names:
                writer.database_names += (path.name,)
            with writer.initialize():
                original(self, path, *args, **kwargs)
        return store_init

    for cls in (DesktopSyncState, ChatSyncLedger, ChatOriginalSource, ChatSyncScheduler, ProfileStore):
        monkeypatch.setattr(cls, '__init__', wrap(cls.__init__))
