"""Alım geçidi istemcisi (U5, ADR-0017/0020): çalışma aç → kayıtları toplu gönder → kapanış iste.

Bağlayıcı CMDB'ye yazmaz; yalnızca geçide gönderir. Geçit belirteci tek bir veri kaynağına bağlıdır ve yalnızca
dosyadan okunur. Toplu gönderim ya hep ya hiçtir: geçit bir kaydı reddederse tamamı reddedilir; istemci reddedilen
toplu gönderimi hata sayısına ekler ve çalışmayı "tamamlanmadı" olarak kapatır (devre kesici korunur).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from ..envelope import IngestEnvelope, IngestRecord, RunEnvelope, RunSummary
from .http import HttpClient, HttpError

__all__ = ["DryRunGateway", "GatewayClient", "GatewayRejected"]


class GatewayRejected(HttpError):
    """Geçit toplu gönderimi kalıcı olarak reddetti (şema, yetki, kaynak uyumsuzluğu)."""


class GatewayClient:
    def __init__(self, http: HttpClient, connector_name: str, *, batch_size: int = 500,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.http = http
        self.source = f"urn:evren:connector:{connector_name}"
        self.batch_size = batch_size
        self.clock = clock

    def _event(self, kind: str, data: Any) -> dict[str, Any]:
        env = {"id": str(uuid.uuid4()), "source": self.source, "time": self.clock(), "data": data}
        model = (RunEnvelope(type=kind, **env) if kind.startswith("evren.ingest.run.")
                 else IngestEnvelope(**env))
        return model.model_dump(mode="json")

    def open_run(self, summary: RunSummary) -> dict[str, Any]:
        return self.http.post_json("/ingest/v1/runs", self._event("evren.ingest.run.started.v1", summary),
                                   ok=(200, 201))

    def send(self, run_id: UUID, records: Sequence[IngestRecord]) -> int:
        """Kayıtları ``batch_size``'lık parçalarla gönderir; kabul edilen kayıt sayısını döner."""
        accepted = 0
        for i in range(0, len(records), self.batch_size):
            chunk = records[i:i + self.batch_size]
            body = {"records": [self._event("evren.ingest.record.v1", r) for r in chunk]}
            try:
                resp = self.http.post_json(f"/ingest/v1/runs/{run_id}/records", body, ok=(202,))
            except HttpError as exc:
                if exc.status in (400, 403, 409, 413):
                    raise GatewayRejected(str(exc), exc.status) from None
                raise
            accepted += int(resp.get("accepted", 0))
        return accepted

    def complete(self, summary: RunSummary) -> dict[str, Any]:
        return self.http.post_json(f"/ingest/v1/runs/{summary.run_id}/complete",
                                   self._event("evren.ingest.run.completed.v1", summary), ok=(200, 202))

    def status(self, run_id: UUID) -> dict[str, Any]:
        return self.http.get_json(f"/ingest/v1/runs/{run_id}")


class DryRunGateway:
    """Kuru çalıştırma: hiçbir şey göndermez. Eşleme sonucunu gözden geçirmek için kayıtları sayar ve isteğe bağlı olarak
    NDJSON dosyasına yazar (her satır bir ``IngestRecord``). Dosya kaynak verisi içerir; kurum dışına çıkarılmaz."""

    def __init__(self, out: str | Path | None = None, *, batch_size: int = 500) -> None:
        self.batch_size = batch_size
        self.path = Path(out) if out else None
        self._fh: IO[str] | None = None
        self.records = 0

    def open_run(self, summary: RunSummary) -> dict[str, Any]:
        if self.path:
            self._fh = self.path.open("w", encoding="utf-8")
        return {"run_id": str(summary.run_id), "state": "dry_run"}

    def send(self, run_id: UUID, records: Sequence[IngestRecord]) -> int:
        for r in records:
            if self._fh:
                self._fh.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")
        self.records += len(records)
        return len(records)

    def complete(self, summary: RunSummary) -> dict[str, Any]:
        if self._fh:
            self._fh.close()
            self._fh = None
        return {"run_id": str(summary.run_id), "state": "dry_run"}
