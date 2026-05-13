"""Compatibility shim package for `collab`.

This package exposes a top-level `collab` import surface while the
implementation lives under the repository `src/` package (module name
`src`). When installed from a registry you can also point the runtime at a
different implementation package using the `COLLAB_PKG_LOCAL_NAME` env var
(for staged rollouts or editable installs).

Mechanism:
- If `COLLAB_PKG_LOCAL_NAME` is set (and not `collab`), import that package
  and re-export its public symbols.
- Otherwise, prepend the repository `src/` directory to this package's
  `__path__` so `import collab.lock_client` resolves the `src/lock_client.py`
  file but exposes it as `collab.lock_client` to callers.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Optional


# Helper: prefer installed distribution metadata for `collab-runtime` when
# available (this is the most robust approach once the wheel is installed).
def _installed_version(dist_name: str = "collab-runtime") -> Optional[str]:
    try:
        try:
            from importlib.metadata import version as _ver
        except Exception:
            # Backwards-compatible fallback to the importlib_metadata backport
            from importlib_metadata import version as _ver  # type: ignore

        try:
            return _ver(dist_name)
        except Exception:
            return None
    except Exception:
        return None


# If an explicit implementation package is requested, import and re-export it.
_override = os.environ.get("COLLAB_PKG_LOCAL_NAME")
if _override and _override != "collab":
    try:
        _impl = importlib.import_module(_override)
    except Exception as exc:  # pragma: no cover - import-time fallback
        raise ImportError(f"runtime package {_override} is not installed") from exc

    __all__ = getattr(_impl, "__all__", [])
    for _n in __all__:
        globals()[_n] = getattr(_impl, _n)

    __version__ = getattr(_impl, "__version__", "0.0.0")
else:
    # Map this package to the local `src/` directory so submodule imports such
    # as `collab.lock_client` resolve to `src/lock_client.py` but appear under
    # the `collab.*` namespace for consumers.
    _this_dir = Path(__file__).resolve().parent
    _repo_root = _this_dir.parent
    _src_dir = _repo_root / "src"
    if _src_dir.exists():
        __path__.insert(0, str(_src_dir))

    # Prefer the installed distribution version when available (works for
    # wheels installed from a registry). Fallback to the local `src` package
    # `__version__` when not installed.
    _ver = _installed_version("collab-runtime")
    if _ver:
        __version__ = _ver
    else:
        try:
            _impl = importlib.import_module("src")
            __version__ = getattr(_impl, "__version__", "0.0.0")
        except Exception:  # pragma: no cover - best-effort only
            __version__ = "0.0.0"
