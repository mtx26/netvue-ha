"""Make the repository root and the HA test helpers importable from any pytest cwd."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tests" / "ha"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
