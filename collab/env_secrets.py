"""Resolve effective Supabase credentials from environment variables."""

from __future__ import annotations

import os
import re
from typing import Optional

# Placeholder patterns copied from scripts/setup.ps1 (Test-IsPlaceholderValue).
_PLACEHOLDER_ENV_RE = re.compile(
    r"^(?:your[_-]|your[_-]?project|example|CHANGE_ME|change[_-]?me|"
    r"replace[_-]?me|TODO|<team-)",
    re.IGNORECASE,
)


def is_placeholder_env_value(value: Optional[str]) -> bool:
    """Return True when *value* is unset or still a template placeholder."""
    if value is None:
        return True
    stripped = value.strip()
    if not stripped:
        return True
    return bool(_PLACEHOLDER_ENV_RE.match(stripped))


def effective_env_secret(value: Optional[str]) -> Optional[str]:
    """Return a usable secret or None when *value* is blank/placeholder."""
    if is_placeholder_env_value(value):
        return None
    return value.strip() if value else None


def effective_service_role_key(raw: Optional[str] = None) -> Optional[str]:
    """Service role key for API calls, or None when placeholder/unset."""
    if raw is None:
        raw = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    return effective_env_secret(raw)


def effective_anon_key(raw: Optional[str] = None) -> Optional[str]:
    """Anon key for API calls, or None when placeholder/unset."""
    if raw is None:
        raw = os.getenv("SUPABASE_ANON_KEY")
    return effective_env_secret(raw)
