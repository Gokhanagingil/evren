"""Bağlayıcı çalıştırıcı (U5, ADR-0020): kaynak istemcisi → eşleme → alım geçidi.

Akış:

1. Geçitte çalışma açılır (``snapshot`` veya ``delta``).
2. Kaynak istemcisi ham nesneleri sayfa sayfa üretir (``SourceClient.fetch``). Çalıştırıcı bellekte en fazla bir toplu
   gönderim kadar kayıt tutar; büyük kaynaklar akış olarak taşınır.
3. Her ham nesneye kaynağın eşleme dosyaları uygulanır (bir nesne birden çok kayıt üretebilir: vCenter VM → VM örneği +
   misafir sunucu). Eşlenemeyen nesne sayılır ve atlanır; içeriği günlüğe yazılmaz.
4. Kapanış bildirilir: ``completed`` yalnızca sayfalama sonuna kadar hatasız gelindiyse doğrudur. Yarıda kalan çekme,
   reddedilen toplu gönderim veya eşleme hatası çalışmayı "tamamlanmadı" yapar; devre kesici eskime ve silme gibi
   yıkıcı işleri durdurur (kural 4).

Çalıştırıcı CMDB'ye yazmaz, eşleştirme yapmaz, CI silmez (kural 2).
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import yaml

from ..envelope import IngestRecord, RunSummary
from .gateway import GatewayRejected
from .http import HttpError
from .mapping import FieldMapping, MappingError

__all__ = ["ConnectorRunner", "GatewayPort", "RunReport", "SourceClient", "load_mappings"]

log = logging.getLogger("evren.connector")


class SourceClient(Protocol):
    """Kaynağa özel istemci: yalnızca okur ve sayfalamayı sonuna kadar tamamlar."""

    data_source: str

    def fetch(self, mode: Literal["snapshot", "delta"], since: datetime | None) -> Iterator[dict[str, Any]]: ...


class GatewayPort(Protocol):
    """Alım geçidi (``GatewayClient``) veya kuru çalıştırma (``DryRunGateway``)."""

    batch_size: int

    def open_run(self, summary: RunSummary) -> dict[str, Any]: ...

    def send(self, run_id: UUID, records: Sequence[IngestRecord]) -> int: ...

    def complete(self, summary: RunSummary) -> dict[str, Any]: ...


def load_mappings(directory: str | Path, data_source: str) -> list[FieldMapping]:
    """``connectors/<kaynak>/`` içindeki ``mapping.yaml`` ve ``*.mapping.yaml`` dosyalarından verilen veri kaynağına ait
    eşlemeler (ad sırasıyla)."""
    out = []
    for path in sorted(Path(directory).glob("*mapping.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if doc.get("data_source") == data_source:
            out.append(FieldMapping.from_dict(doc))
    if not out:
        raise MappingError(f"{directory}: {data_source} için eşleme dosyası yok")
    return out


@dataclass
class RunReport:
    run_id: UUID
    data_source: str
    mode: str
    fetched: int = 0
    mapped: int = 0
    skipped: int = 0
    sent: int = 0
    errors: int = 0
    completed: bool = False
    failure: str | None = None
    gateway_state: str | None = None
    notes: list[str] = field(default_factory=list)
    by_class: Counter[str] = field(default_factory=Counter)

    def as_json(self) -> dict[str, Any]:
        return {"run_id": str(self.run_id), "data_source": self.data_source, "mode": self.mode,
                "fetched": self.fetched, "mapped": self.mapped, "skipped": self.skipped, "sent": self.sent,
                "errors": self.errors, "completed": self.completed, "failure": self.failure,
                "gateway_state": self.gateway_state, "by_class": dict(sorted(self.by_class.items())),
                "notes": self.notes}


class ConnectorRunner:
    def __init__(self, client: SourceClient, mappings: Sequence[FieldMapping], gateway: GatewayPort, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 archive: Callable[[dict[str, Any]], str] | None = None) -> None:
        if any(m.data_source != client.data_source for m in mappings):
            raise MappingError("Eşleme dosyası başka bir veri kaynağına ait")
        self.client, self.mappings, self.gateway, self.clock, self.archive = client, list(mappings), gateway, clock, archive

    def _records(self, raw: dict[str, Any], run_id: UUID, observed: datetime, report: RunReport) -> list[IngestRecord]:
        out: list[IngestRecord] = []
        raw_ref = self.archive(raw) if self.archive else None
        for m in self.mappings:
            if not m.included(raw):
                continue
            try:
                rec = m.apply(raw, run_id=run_id, observed_at=observed)
            except (MappingError, ValueError) as exc:  # pydantic doğrulama hatası da ValueError
                report.skipped += 1
                report.errors += 1
                if len(report.notes) < 20:
                    report.notes.append(f"eşleme hatası: {type(exc).__name__}")
                continue
            if rec is not None:
                out.append(rec.model_copy(update={"raw_ref": raw_ref}) if raw_ref else rec)
        return out

    def run(self, mode: Literal["snapshot", "delta"] = "snapshot", since: datetime | None = None,
            run_id: UUID | None = None) -> RunReport:
        run_id = run_id or uuid.uuid4()
        started = self.clock()
        report = RunReport(run_id, self.client.data_source, mode)
        base = {"data_source": self.client.data_source, "run_id": run_id, "mode": mode, "started_at": started}
        self.gateway.open_run(RunSummary(completed=False, record_count=0, **base))
        pending: list[IngestRecord] = []

        def flush() -> None:
            if pending:
                report.sent += self.gateway.send(run_id, pending)
                pending.clear()

        fetch_ok = False
        try:
            for raw in self.client.fetch(mode, since):
                report.fetched += 1
                recs = self._records(raw, run_id, self.clock(), report)
                report.mapped += len(recs)
                report.by_class.update(r.class_hint for r in recs)
                pending.extend(recs)
                if len(pending) >= self.gateway.batch_size:
                    flush()
            flush()
            fetch_ok = True
        except GatewayRejected as exc:
            report.errors += len(pending) or 1
            report.failure = f"geçit reddetti (durum {exc.status})"
        except HttpError as exc:
            report.failure = f"kaynak veya geçit erişimi: {exc}"
        except Exception as exc:  # beklenmeyen hata çalışmayı tamamlanmamış kapatır; ileti kaynak verisi içerebilir
            report.failure = f"beklenmeyen hata: {type(exc).__name__}"
            log.error("bağlayıcı çalışması yarıda kaldı: %s (%s)", self.client.data_source, type(exc).__name__)
            log.debug("ayrıntı", exc_info=True)
        report.completed = fetch_ok and report.errors == 0
        summary = RunSummary(completed=report.completed, record_count=report.sent, error_count=report.errors,
                             finished_at=self.clock(), notes=(report.failure or None), **base)
        try:
            report.gateway_state = self.gateway.complete(summary).get("state")
        except HttpError as exc:
            report.failure = report.failure or f"kapanış bildirilemedi: {exc}"
        log.info("bağlayıcı çalışması: %s", report.as_json())
        return report

