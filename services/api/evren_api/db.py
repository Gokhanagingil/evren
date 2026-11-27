"""PostgreSQL bağlantı havuzu. Her bağlantı en az yetkili API rolüne geçer ve sorgu zaman aşımı alır."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from .config import Settings

__all__ = ["make_pool"]


def make_pool(settings: Settings) -> ConnectionPool:
    def configure(conn: psycopg.Connection[Any]) -> None:
        conn.autocommit = True
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.db_role)))
        conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(settings.statement_timeout_ms)))
        conn.execute("SET application_name = 'evren-api'")

    def reset(conn: psycopg.Connection[Any]) -> None:
        # İsteğin denetim bağlamı (ADR-0039) bir sonraki isteğe taşınmaz; hata olursa havuz bağlantıyı atar.
        conn.execute("SELECT set_config('evren.request_id', '', false), set_config('evren.client_ip', '', false)")

    return ConnectionPool(settings.db_conninfo, min_size=1, max_size=settings.db_pool_size, configure=configure,
                          reset=reset, open=False, name="evren-api", timeout=10)
