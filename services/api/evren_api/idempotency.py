"""Tekrar güvenliği (GRC Platformu ile ortak sözleşme, docs/08).

Aynı kişi + aynı ``idempotency_key`` + aynı içerik → kaydedilen yanıt (``replayed: true``);
aynı anahtar + farklı içerik → 409. Kayıt ve iş aynı veritabanı işleminde yazılır.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from .errors import ApiError

__all__ = ["remember", "replay", "request_hash"]


def request_hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(list(parts), sort_keys=True, default=str).encode()).hexdigest()


def replay(cur: psycopg.Cursor[Any], principal: str, key: str, digest: str) -> JSONResponse | None:
    cur.execute("SELECT request_hash, status_code, response FROM api.idempotency WHERE principal = %s AND key = %s",
                (principal, key))
    prior = cur.fetchone()
    if prior is None:
        return None
    if prior[0] != digest:
        raise ApiError(409, "idempotency_cakismasi", "Bu idempotency_key farklı bir istekle kullanılmış.")
    return JSONResponse({**prior[2], "replayed": True}, status_code=prior[1])


def remember(cur: psycopg.Cursor[Any], principal: str, key: str, path: str, digest: str, status: int,
             response: dict[str, Any]) -> None:
    cur.execute("""INSERT INTO api.idempotency (principal, key, method, path, request_hash, status_code, response)
                   VALUES (%s, %s, 'POST', %s, %s, %s, %s)""",
                (principal, key, path, digest, status, Jsonb(response)))
