"""提供 v2.5 临时支线合同的稳定兼容门面。"""  # noqa: DOCSTRING_CJK
from .branch_contract_common import (
    PUBLIC_ENTITY_STATUSES as PUBLIC_ENTITY_STATUSES,
    validate_public_entity_label as validate_public_entity_label,
)
from .branch_fact_contracts import (
    BRANCH_HISTORY_EXIT_KINDS as BRANCH_HISTORY_EXIT_KINDS,
    PROTECTED_FACT_FIELDS as PROTECTED_FACT_FIELDS,
    build_committed_branch_fact as build_committed_branch_fact,
    validate_branch_fact_candidate as validate_branch_fact_candidate,
    validate_branch_history_entry as validate_branch_history_entry,
    validate_committed_branch_fact_against_patch as validate_committed_branch_fact_against_patch,
    validate_committed_branch_fact_structure as validate_committed_branch_fact_structure,
)
from .branch_patch_contracts import (
    PROTECTED_PATCH_FIELDS as PROTECTED_PATCH_FIELDS,
    validate_runtime_branch_patch as validate_runtime_branch_patch,
)
