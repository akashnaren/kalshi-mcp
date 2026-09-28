#!/usr/bin/env python3
"""stdio entrypoint. Plugin hosts launch this file.

Insert this file's directory on sys.path before importing the package. Hosts
that launch the script from another working directory, including Python's safe
path (-P), can still import kalshi_readonly.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from kalshi_readonly.tools import main

if __name__ == "__main__":
    main()
