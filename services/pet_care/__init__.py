"""Local PetCare business services.

The local adapter mirrors the future community contract: it owns one account
pet, applies elapsed-state settlement, confirms idempotent care operations,
and serves task/check-in/reward contracts.  It never reads or writes Cat Mind
state.
"""

from .binding_service import (
    BindingContext,
    BindingServiceError,
    DEFAULT_PET_CARE_SNAPSHOT_CONFIG,
    LocalPetCareBindingService,
)
from .account_session import VerifiedAccountSession, resolve_verified_account_id, resolve_verified_account_session

__all__ = [
    "BindingContext",
    "BindingServiceError",
    "DEFAULT_PET_CARE_SNAPSHOT_CONFIG",
    "LocalPetCareBindingService",
    "resolve_verified_account_id",
    "VerifiedAccountSession",
    "resolve_verified_account_session",
]
