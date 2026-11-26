"""The plugin permission master switch: read its sources, write the host setting.

The switch turns every permission check off (uses are still judged and
recorded). Writing needs a valid ``X-CSRF-Token`` even from local callers.
"""

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool

from plugin.core import capability_policy
from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain
from plugin.server.infrastructure.mutation_auth import require_plugin_token_mutation_access

router = APIRouter(prefix="/plugin-permissions", tags=["plugin-permissions"])
logger = get_logger("server.routes.plugin_permission_switch")


class SwitchSetting(BaseModel):
    disabled: StrictBool


def switch_view() -> dict:
    """``disabled``: whether checks are off now (any source). ``sources``:
    each source, whether it turns them off and whether the host can write it."""
    states = capability_policy.source_states()
    return {
        "disabled": any(states.values()),
        "sources": [
            {
                "source": source,
                "disabled": states[source],
                "writable": source in capability_policy.WRITABLE_SOURCES,
            }
            for source in capability_policy.SOURCES
        ],
    }


def _write(disabled: bool) -> dict:
    from plugin.server.infrastructure.permission_switch import switch_store
    from plugin.server.infrastructure.plugin_permissions import notify_all_changed

    switch_store.set_disabled(disabled)
    # Every plugin's resolved states carry ``enforced``: push them again.
    notify_all_changed()
    return switch_view()


@router.get("/master-switch")
async def get_master_switch():
    return await asyncio.to_thread(switch_view)


@router.put(
    "/master-switch",
    dependencies=[Depends(require_plugin_token_mutation_access)],
)
async def put_master_switch(body: SwitchSetting):
    """Write the host setting. While an environment variable keeps the
    checks off, ``disabled`` stays true after turning the setting off; the
    source list says which one."""
    try:
        return await asyncio.to_thread(_write, body.disabled)
    except ServerDomainError as exc:
        raise_http_from_domain(exc, logger=logger, include_details=True)
