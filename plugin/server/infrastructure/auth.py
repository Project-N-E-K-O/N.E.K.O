"""Plugin server authentication compatibility layer."""

from fastapi import Depends


def generate_admin_code() -> str:
    """Retained for compatibility; admin-code auth is disabled."""
    return ""


def set_admin_code(code: str) -> None:
    """Retained for compatibility; admin-code auth is disabled."""
    _ = code


def get_admin_code() -> None:
    """Retained for compatibility; admin-code auth is disabled."""
    return None


async def verify_admin_code() -> str:
    """Always allow access; admin-code auth is disabled."""
    return "authenticated"


# Create dependencies
require_admin = Depends(verify_admin_code)

