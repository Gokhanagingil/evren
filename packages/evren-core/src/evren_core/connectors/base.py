"""Bağlayıcı (connector) sözleşmesi.

Bağlayıcının tek işi kaynaktan veriyi eksiksiz çekmek ve ``FieldMapping`` ile
``IngestRecord``'a çevirmektir. Bağlayıcı CMDB'ye yazmaz, eşleştirme yapmaz,
CI silmez. Silme/eskime kararı, çalışmanın tamamlandığı ve devre kesicinin
izin verdiği durumda uzlaştırıcı tarafından verilir.

Kurallar
--------
* Sayfalama tamamlanmadan ``completed=True`` bildirilmez (kısmi toplama =
  karantina). Bu, GRC uygulamasının ``collection_status=partial`` ilkesiyle aynıdır.
* Kimlik bilgileri ortam değişkeni/sır kasasından okunur; loglara yazılmaz.
* Her kayıt için ham yük arşive yazılır ve ``raw_ref`` ile işaretlenir.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from ..envelope import IngestRecord, RunSummary

__all__ = ["Connector", "ConnectorHealth", "RunContext"]


@dataclass(frozen=True)
class RunContext:
    run_id: UUID
    mode: Literal["snapshot", "delta"]
    started_at: datetime
    since: datetime | None = None  # delta modu için


@dataclass(frozen=True)
class ConnectorHealth:
    reachable: bool
    authenticated: bool
    detail: str = ""


class Connector(Protocol):
    data_source: str

    def healthcheck(self) -> ConnectorHealth: ...

    def fetch_raw(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        """Kaynak nesnelerini ham JSON olarak üretir. Sayfalama burada tamamlanır."""
        ...

    def run(self, ctx: RunContext) -> tuple[Iterator[IngestRecord], RunSummary]: ...
