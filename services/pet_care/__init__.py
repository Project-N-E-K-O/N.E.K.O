"""Local PetCare business services.

Stage 2 adds a read-only, persisted snapshot.  Action settlement and elapsed
time calculation remain outside this package until their later phases.
"""

from .binding_service import (
    BindingContext,
    BindingServiceError,
    DEFAULT_PET_CARE_SNAPSHOT_CONFIG,
    LocalPetCareBindingService,
)
from .account_session import resolve_verified_account_id

__all__ = [
    "BindingContext",
    "BindingServiceError",
    "DEFAULT_PET_CARE_SNAPSHOT_CONFIG",
    "LocalPetCareBindingService",
    "resolve_verified_account_id",
]
