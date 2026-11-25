"""Alım zarfı kaydını kimlik çözümleme girdisine çevirir."""
from __future__ import annotations

from ..envelope import IngestRecord
from .model import SourceRecord

__all__ = ["source_record_from_ingest"]


def source_record_from_ingest(record: IngestRecord) -> SourceRecord:
    return SourceRecord(
        data_source=record.data_source,
        source_key=record.source_key,
        class_hint=record.class_hint,
        attributes=dict(record.attributes),
        lookup={
            "mac_addresses": list(record.lookup.mac_addresses),
            "ip_addresses": list(record.lookup.ip_addresses),
            "serial_numbers": list(record.lookup.serial_numbers),
        },
        external_ids=dict(record.external_ids),
        observed_at=record.observed_at,
    )
