"""Collab runtime package (published on PyPI as ``collab-runtime``)."""

from __future__ import annotations

from typing import Optional

__all__ = ["__version__"]


def _installed_version(dist_name: str = "collab-runtime") -> Optional[str]:
    try:
        from importlib.metadata import version as _ver
    except Exception:
        try:
            from importlib_metadata import version as _ver  # type: ignore
        except Exception:
            return None
    try:
        return _ver(dist_name)
    except Exception:
        return None


__version__ = _installed_version("collab-runtime") or "0.0.0"
