#!/usr/bin/env python3
"""Standard entry point for the collab package.

Provides backward compatibility for direct `python run.py` invocations. The canonical
entry point remains the installed `collab` console script.
"""

from collab.__main__ import main

if __name__ == "__main__":
    main()
