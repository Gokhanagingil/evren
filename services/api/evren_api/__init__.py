"""Evren REST API ve arayüz sunucusu (F0-09). Ayrıntı: services/api/README.md."""
from .app import create_app

__all__ = ["create_app"]
