"""Araçların kurulum gerektirmeden evren_core'u bulmasını sağlar; ortak bağlantı dizesi okuyucusu."""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "packages" / "evren-core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def database_url() -> str:
    """``EVREN_DATABASE_URL_FILE`` (sır dosyası) → ``EVREN_DATABASE_URL`` → boş (libpq ortam değişkenleri)."""
    path = os.environ.get("EVREN_DATABASE_URL_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get("EVREN_DATABASE_URL", "")
