"""Entrypoint wrapper so `python -m collab` and `collab` console scripts work.

Delegates to the CLI implementation in `src/main.py` via the shimmed path.
"""

from __future__ import annotations

from .main import main  # resolved via the shimmed __path__ pointing at `src/`

if __name__ == "__main__":
    main()
