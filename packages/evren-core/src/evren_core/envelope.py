"""Alım (ingest) zarfı — tüm bağlayıcıların ara katmana gönderdiği tek sözleşme.

CloudEvents 1.0 yapılandırılmış JSON biçimindedir. Keşif aracı değiştiğinde
yalnızca bağlayıcı ve eşleme dosyası değişir; zarftan sonraki hiçbir bileşen
(kimlik çözümleme, uzlaştırma, CMDB, ServiceNow eşlemesi) etkilenmez.

JSON şeması ``schemas/ingest-envelope.schema.json`` bu modelden üretilir
(``python tools/export_schemas.py``); testler ikisinin eşit olduğunu doğrular.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["IngestEnvelope", "IngestRecord", "LookupData", "RelationRef", "RunEnvelope", "RunSummary"]

DATA_SOURCE_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$"
CLASS_PATTERN = r"^[a-z][a-z0-9_]{1,79}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LookupData(_Strict):
    """IRE *lookup* karşılığı: bir CI'ın çoklu tanımlayıcıları."""

    mac_addresses: list[str] = Field(default_factory=list, max_length=256)
    ip_addresses: list[str] = Field(default_factory=list, max_length=1024)
    serial_numbers: list[str] = Field(default_factory=list, max_length=32)


class RelationRef(_Strict):
    """Aynı veya başka bir kaynaktaki nesneye ilişki (ör. VM → ESXi host)."""

    type: str = Field(pattern=r"^[^:]+::[^:]+$", examples=["Runs on::Runs", "Virtualized by::Virtualizes"])
    direction: Literal["parent", "child"] = Field(
        description="Bu kaydın ilişkideki rolü. 'parent': bu kayıt → hedef (ör. uygulama 'Runs on' sunucu)."
    )
    target_source_key: str = Field(min_length=1, max_length=512)
    target_data_source: str | None = Field(default=None, pattern=DATA_SOURCE_PATTERN)
    target_class_hint: str | None = Field(default=None, pattern=CLASS_PATTERN)


class IngestRecord(_Strict):
    schema_version: Literal[1] = 1
    data_source: str = Field(pattern=DATA_SOURCE_PATTERN)
    run_id: UUID
    source_key: str = Field(min_length=1, max_length=512, description="Kaynaktaki kalıcı nesne kimliği")
    class_hint: str = Field(pattern=CLASS_PATTERN, description="Önerilen ServiceNow sınıfı (ör. cmdb_ci_linux_server)")
    attributes: dict[str, Any] = Field(default_factory=dict, description="ServiceNow alan adlarıyla öznitelikler")
    lookup: LookupData = Field(default_factory=LookupData)
    external_ids: dict[str, str] = Field(default_factory=dict)
    relations: list[RelationRef] = Field(default_factory=list, max_length=512)
    deleted: bool = Field(default=False, description="Kaynak nesnenin silindiğini açıkça bildirir (delta modu)")
    observed_at: datetime
    raw_ref: str | None = Field(default=None, max_length=1024, description="Ham yükün arşivdeki konumu")


class RunSummary(_Strict):
    data_source: str = Field(pattern=DATA_SOURCE_PATTERN)
    run_id: UUID
    mode: Literal["snapshot", "delta"]
    completed: bool
    record_count: int = Field(ge=0)
    error_count: int = Field(default=0, ge=0)
    started_at: datetime
    finished_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class _CloudEvent(_Strict):
    specversion: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=512, examples=["urn:evren:connector:zabbix:dc1"])
    time: datetime
    datacontenttype: Literal["application/json"] = "application/json"
    subject: str | None = Field(default=None, max_length=512)


class IngestEnvelope(_CloudEvent):
    type: Literal["evren.ingest.record.v1"] = "evren.ingest.record.v1"
    data: IngestRecord


class RunEnvelope(_CloudEvent):
    type: Literal["evren.ingest.run.started.v1", "evren.ingest.run.completed.v1"]
    data: RunSummary
