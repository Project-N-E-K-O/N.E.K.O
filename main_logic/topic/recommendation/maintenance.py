"""Recommendation-only adapter for existing storage/cloud apply transactions."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from .registry import get_recommendation_service
from .contracts import RecommendationError


@dataclass
class _MaintenanceOwner:
    service: object
    claims: set[object] = field(default_factory=set)
    version: int = 0
    verified_version: int = -1
    root_ready: bool = False


_owners: dict[int, _MaintenanceOwner] = {}


@asynccontextmanager
async def recommendation_maintenance():
    """Pause synchronously before an existing transaction's first await.

    Independent storage/cloud locks can overlap. Claims and versioned fence
    reads prevent one completed request from releasing another's pause, or a
    slow successful read from overriding a later failed recovery. This module
    runs on the application's event loop and never creates another writer.
    """
    service = get_recommendation_service()
    if service is None:
        yield
        return
    key = id(service)
    owner = _owners.setdefault(key, _MaintenanceOwner(service))
    claim = object()
    owner.claims.add(claim)
    owner.version += 1
    service.set_maintenance(True)
    try:
        # Persist only receipts already captured at the actual publication
        # boundary before the existing root transaction changes its fence.
        deadline = asyncio.get_running_loop().time() + service.settings.close_timeout
        try:
            if len(owner.claims) == 1:
                async with asyncio.timeout_at(deadline):
                    await service.flush_publications(allow_maintenance=True)
        except RecommendationError as exc:
            # An optional unavailable store must not prevent its own root's
            # recovery. Receipts stay owned by the service for a later retry.
            if exc.code not in {"store_unavailable", "maintenance"}:
                raise
        except TimeoutError:
            pass
        finally:
            service.store.suspend()
        # Cancellation of an async waiter is not proof that its thread stopped.
        # Block the transaction with a bounded error if physical work survives.
        await service.store.wait_idle(deadline=deadline)
        yield
    finally:
        owner.version += 1
        version = owner.version
        try:
            deadline = asyncio.get_running_loop().time() + service.settings.close_timeout
            async with asyncio.timeout_at(deadline):
                ready = await service.store.root_ready()
                if owner.version == version:
                    owner.verified_version = version
                    owner.root_ready = ready
        except (TimeoutError, RecommendationError):
            if owner.version == version:
                owner.root_ready = False
                owner.verified_version = version
        finally:
            owner.claims.discard(claim)
            if not owner.claims:
                try:
                    if (get_recommendation_service() is service and
                            owner.verified_version == owner.version and owner.root_ready):
                        recovery_version = owner.version
                        service.store.resume()
                        async with asyncio.timeout_at(deadline):
                            await service.recover_after_maintenance()
                            ready = await service.store.root_ready()
                        if (get_recommendation_service() is service and
                                _owners.get(key) is owner and not owner.claims
                                and owner.version == recovery_version and ready):
                            service.set_maintenance(False)
                        else:
                            service.store.suspend()
                except (TimeoutError, RecommendationError):
                    service.store.suspend()
                finally:
                    if _owners.get(key) is owner and not owner.claims:
                        _owners.pop(key)
