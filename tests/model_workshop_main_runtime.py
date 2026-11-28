"""Real Main role setup for model-apply tests, without provider or connector I/O."""

import asyncio
import weakref
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@asynccontextmanager
async def main_character_runtime(config, states, *, input_mode='text'):
    from app import main_server
    from app.main_server import character_runtime, voice_identity_runtime
    from main_logic.core import manager as manager_module
    from main_logic import public_knowledge

    # Main publishes these snapshots both in its implementation and its facade.
    # Restore both views so a real refresh cannot leak into another test.
    globals_names = (
        'master_name', 'her_name', 'master_basic_config', 'lanlan_basic_config',
        'name_mapping', 'lanlan_prompt', 'time_store', 'setting_store',
        'recent_log', 'catgirl_names',
    )
    with pytest.MonkeyPatch.context() as patch:
        for module in (character_runtime, main_server):
            for name in globals_names:
                patch.setattr(module, name, getattr(module, name))
        roles = {}
        patch.setattr(character_runtime, 'role_state', roles)
        patch.setattr(main_server, 'role_state', roles)
        patch.setattr(character_runtime, '_config_manager', config)
        patch.setattr(manager_module, 'get_config_manager', lambda: config)
        # The voice Owner service belongs to full app startup, which is absent
        # here. Keep its normal uninitialized registry; do not replace its APIs.
        patch.setattr(voice_identity_runtime, '_runtime_registry', None)
        # Model application does not exercise the Memory Server's independent
        # knowledge catalog. Supply a fresh unavailable cache so real manager
        # construction cannot schedule a background network probe or timer.
        patch.setattr(public_knowledge, '_tool_available', False)
        patch.setattr(public_knowledge, '_next_check_at', float('inf'))
        patch.setattr(public_knowledge, '_listeners', weakref.WeakSet())
        initial = {}
        connectors = []
        managers = []
        try:
            await character_runtime._refresh_character_globals()
            for name, state in states.items():
                assert state in ('idle', 'active', 'starting')
                character_runtime._ensure_character_slots(name)
                role = roles[name]
                prompt = character_runtime.lanlan_prompt[name].replace(
                    '{LANLAN_NAME}', name,
                ).replace('{MASTER_NAME}', character_runtime.master_name)
                manager = character_runtime.core.LLMSessionManager(
                    role.sync_message_queue, name, prompt,
                )
                managers.append(manager)
                manager.websocket_lock = role.websocket_lock
                manager.websocket = object()
                manager.user_language = 'ja'
                manager._user_language_explicit = True
                # Mark lifecycle states on real managers without starting a
                # remote AI session. Existing-session identity must survive save.
                manager.is_active = state == 'active'
                # Production defaults to audio; normal model-preview acceptance
                # uses text chat. Dedicated audio cases exercise the real UI lock.
                manager.input_mode = input_mode
                manager._starting_session_count = int(state == 'starting')
                manager._starting_input_mode = input_mode if state == 'starting' else None
                if state != 'idle':
                    manager.session = object()
                role.session_manager = manager
                # Model edits must retain an existing connector. A real local
                # task supplies that lifecycle without contacting Memory/Monitor.
                role.sync_task = asyncio.create_task(asyncio.Event().wait())
                connectors.append(role.sync_task)
                initial[name] = SimpleNamespace(
                    role=role, manager=manager, connector=role.sync_task,
                    queue=role.sync_message_queue, lock=role.websocket_lock,
                    websocket=manager.websocket, session=manager.session, state=state,
                )
            yield SimpleNamespace(module=character_runtime, facade=main_server, initial=initial)
        finally:
            for role in roles.values():
                if role.session_manager is not None:
                    managers.append(role.session_manager)
            for manager in set(managers):
                manager.shutdown()
            for task in connectors:
                task.cancel()
            if connectors:
                await asyncio.gather(*connectors, return_exceptions=True)
