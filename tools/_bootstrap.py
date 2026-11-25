"""Araçların kurulum gerektirmeden evren_core'u bulmasını sağlar."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "packages" / "evren-core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
