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

    return ConnectionPool(settings.db_conninfo, min_size=1, max_size=settings.db_pool_size, configure=configure,
                          open=False, name="evren-api", timeout=10)
