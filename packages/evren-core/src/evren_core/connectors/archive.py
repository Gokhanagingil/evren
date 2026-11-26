"""Ham yük arşivi (U5.4, F1-14, ADR-0024): çalıştırıcı tarafında şifreli parça dosyaları ve arşivden yeniden işleme.

Düzen (``EVREN_ARCHIVE_DIR`` altında)::

    <kaynak>/<YYYY-MM-DD>/<run_id>/segment-00001.enc …   gzip(NDJSON) → Fernet; en çok 5000 ham nesne
    <kaynak>/<YYYY-MM-DD>/<run_id>/manifest.json          sayılar, SHA-256 özetleri, anahtar kimliği (VERİ YOK)

* Şifreleme zorunlu: Fernet (``cryptography``; API oturumlarında zaten kullanılan paket, yeni bağımlılık yok). Anahtar
  dosyası (``EVREN_ARCHIVE_KEY_FILE``) bir veya birden çok satır: ilk satır şifreler, hepsi çözer (anahtar döndürme).
* ``raw_ref`` = ``archive:<kaynak>/<tarih>/<run_id>/<parça>/<satır>`` (göreli; makine yolu içermez).
* Dosyalar atomik (geçici dosya + yeniden adlandırma), dizin 0700, dosya 0600. Manifest en son yazılır.
* Okuyucu önce özeti denetler, sonra çözer; kurcalanmış parça, yanlış anahtar, bozuk dosya açık hatayla durur ve
  ileti içerik taşımaz. Yol bileşenleri doğrulanır (yol geçişi yok).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

__all__ = ["ArchiveError", "ArchiveSource", "ArchiveWriter", "load_keys", "open_run", "prune", "runs"]

SEGMENT_RECORDS = 5000
_SOURCE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,63}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SEGMENT = re.compile(r"^segment-(\d{5})\.enc$")
MANIFEST = "manifest.json"


class ArchiveError(RuntimeError):
    """Arşiv hatası; ileti kaynak verisi veya anahtar içermez."""


def load_keys(path: str | Path | None) -> Any:
    """Anahtar dosyasından ``MultiFernet`` (ilk satır şifreler). Dosya yoksa veya anahtar geçersizse hata."""
    from cryptography.fernet import Fernet, MultiFernet

    if not path:
        raise ArchiveError("Arşiv anahtar dosyası tanımlı değil (EVREN_ARCHIVE_KEY_FILE)")
    try:
        lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        raise ArchiveError("Arşiv anahtar dosyası okunamadı") from None
    if not lines:
        raise ArchiveError("Arşiv anahtar dosyası boş")
    try:
        fernets = [Fernet(ln.encode()) for ln in lines]
    except (ValueError, TypeError):
        raise ArchiveError("Arşiv anahtarı geçersiz (Fernet anahtarı bekleniyor)") from None
    keyring = MultiFernet(fernets)
    keyring.key_id = hashlib.sha256(lines[0].encode()).hexdigest()[:12]  # type: ignore[attr-defined]
    return keyring


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _run_dir(root: Path, data_source: str, day: date, run_id: uuid.UUID) -> Path:
    if not _SOURCE.match(data_source):
        raise ArchiveError("Geçersiz veri kaynağı adı")
    return root / data_source / day.isoformat() / str(run_id)


@dataclass
class ArchiveWriter:
    """Bir çalışmanın ham nesnelerini parça parça yazar. ``add`` → ``raw_ref``; ``close`` manifesti yazar."""

    root: Path
    data_source: str
    run_id: uuid.UUID
    keys: Any = field(repr=False)
    mode: str = "snapshot"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    segment_records: int = SEGMENT_RECORDS
    _buffer: list[bytes] = field(default_factory=list, repr=False)
    _segments: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 1
    _closed: bool = False

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.dir = _run_dir(self.root, self.data_source, self.started_at.astimezone(UTC).date(), self.run_id)
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        for p in (self.dir, self.dir.parent, self.dir.parent.parent):
            os.chmod(p, 0o700)
        self.prefix = f"archive:{self.data_source}/{self.dir.parent.name}/{self.run_id}"

    def add(self, raw: dict[str, Any]) -> str:
        if self._closed:
            raise ArchiveError("Arşiv kapatıldı")
        line = len(self._buffer)
        self._buffer.append(json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
        ref = f"{self.prefix}/{self._seq}/{line}"
        if len(self._buffer) >= self.segment_records:
            self._flush()
        return ref

    def _flush(self) -> None:
        if not self._buffer:
            return
        token = self.keys.encrypt(gzip.compress(b"\n".join(self._buffer) + b"\n", mtime=0))
        name = f"segment-{self._seq:05d}.enc"
        _atomic_write(self.dir / name, token)
        self._segments.append({"name": name, "records": len(self._buffer), "sha256": hashlib.sha256(token).hexdigest()})
        self._buffer.clear()
        self._seq += 1

    def close(self, *, completed: bool, closed_at: datetime | None = None) -> dict[str, Any]:
        """Kalan parçayı ve manifesti yazar (manifest en son: manifesti olmayan dizin yarım arşivdir)."""
        if self._closed:
            raise ArchiveError("Arşiv zaten kapatıldı")
        self._flush()
        self._closed = True
        manifest = {"version": 1, "data_source": self.data_source, "run_id": str(self.run_id), "mode": self.mode,
                    "started_at": self.started_at.isoformat(), "closed_at": (closed_at or datetime.now(UTC)).isoformat(),
                    "completed": bool(completed), "records": sum(s["records"] for s in self._segments),
                    "segments": self._segments, "key_id": getattr(self.keys, "key_id", None)}
        _atomic_write(self.dir / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
        return manifest


def _inside(root: Path, path: Path) -> Path:
    real_root, real = root.resolve(), path.resolve()
    if real_root not in real.parents:
        raise ArchiveError("Arşiv yolu arşiv kökünün dışında")
    rel = real.relative_to(real_root).parts
    if len(rel) != 3 or not _SOURCE.match(rel[0]) or not _DATE.match(rel[1]):
        raise ArchiveError("Arşiv yolu <kaynak>/<tarih>/<run_id> biçiminde değil")
    try:
        uuid.UUID(rel[2])
    except ValueError:
        raise ArchiveError("Arşiv yolu <kaynak>/<tarih>/<run_id> biçiminde değil") from None
    return real


@dataclass
class ArchivedRun:
    dir: Path
    manifest: dict[str, Any]
    keys: Any = field(repr=False)

    @property
    def data_source(self) -> str:
        return str(self.manifest["data_source"])

    def records(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """(raw_ref, ham nesne) çiftleri; her parça önce özetle denetlenir, sonra çözülür."""
        from cryptography.fernet import InvalidToken

        prefix = f"archive:{self.data_source}/{self.dir.parent.name}/{self.manifest['run_id']}"
        for seg in self.manifest.get("segments", []):
            m = _SEGMENT.match(str(seg.get("name", "")))
            if not m:
                raise ArchiveError("Manifestte geçersiz parça adı")
            try:
                token = (self.dir / seg["name"]).read_bytes()
            except OSError:
                raise ArchiveError(f"Arşiv parçası okunamadı ({seg['name']})") from None
            if hashlib.sha256(token).hexdigest() != seg.get("sha256"):
                raise ArchiveError(f"Arşiv parçası manifestle uyuşmuyor ({seg['name']}); kurcalanmış olabilir")
            try:
                lines = gzip.decompress(self.keys.decrypt(token)).splitlines()
            except (InvalidToken, OSError, EOFError):
                raise ArchiveError(f"Arşiv parçası çözülemedi ({seg['name']}): anahtar yanlış veya dosya bozuk") from None
            if len(lines) != seg.get("records"):
                raise ArchiveError(f"Arşiv parçasının kayıt sayısı manifestle uyuşmuyor ({seg['name']})")
            for i, line in enumerate(lines):
                try:
                    raw = json.loads(line)
                except ValueError:
                    raise ArchiveError(f"Arşiv kaydı bozuk ({seg['name']})") from None
                yield f"{prefix}/{int(m.group(1))}/{i}", raw


def open_run(root: str | Path, run_dir: str | Path, keys: Any) -> ArchivedRun:
    """Arşivlenmiş çalışmayı açar; yol arşiv kökü altında ve doğru biçimde olmalı, manifest bulunmalı."""
    path = Path(run_dir)
    real = _inside(Path(root), path if path.is_absolute() else Path(root) / path)
    try:
        manifest = json.loads((real / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ArchiveError("Arşiv manifesti yok veya bozuk (yarım arşiv olabilir)") from None
    if manifest.get("version") != 1 or manifest.get("data_source") != real.parent.parent.name \
            or manifest.get("run_id") != real.name:
        raise ArchiveError("Arşiv manifesti dizinle uyuşmuyor")
    return ArchivedRun(real, manifest, keys)


def runs(root: str | Path) -> list[dict[str, Any]]:
    """Arşivdeki çalışmaların manifest özetleri (veri yok); en yeni önce."""
    base = Path(root)
    out = []
    for manifest in base.glob(f"*/*/*/{MANIFEST}"):
        try:
            _inside(base, manifest.parent)
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (ArchiveError, OSError, ValueError):
            continue
        out.append({"path": str(manifest.parent.relative_to(base)), "data_source": doc.get("data_source"),
                    "run_id": doc.get("run_id"), "mode": doc.get("mode"), "started_at": doc.get("started_at"),
                    "completed": doc.get("completed"), "records": doc.get("records"),
                    "segments": len(doc.get("segments", [])), "key_id": doc.get("key_id")})
    return sorted(out, key=lambda r: str(r["started_at"]), reverse=True)


def prune(root: str | Path, retention_days: int, *, now: datetime | None = None) -> list[str]:
    """Saklama süresi dolan çalışma dizinlerini siler. Yalnızca ``<kaynak>/<tarih>/<run_id>`` biçimine uyan dizinler;
    tarih dizin adından okunur. Silinen göreli yollar döner."""
    if retention_days < 1:
        raise ArchiveError("Saklama süresi en az 1 gün olmalı")
    base = Path(root)
    cutoff = (now or datetime.now(UTC)).astimezone(UTC).date() - timedelta(days=retention_days)
    removed: list[str] = []
    for day_dir in base.glob("*/*"):
        if not day_dir.is_dir() or day_dir.is_symlink() or not _SOURCE.match(day_dir.parent.name) \
                or not _DATE.match(day_dir.name):
            continue
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for run_dir in day_dir.iterdir():
            try:
                real = _inside(base, run_dir)
            except ArchiveError:
                continue
            if run_dir.is_symlink():
                continue
            shutil.rmtree(real)
            removed.append(str(real.relative_to(base.resolve())))
        if not any(day_dir.iterdir()):
            day_dir.rmdir()
    return sorted(removed)


class ArchiveSource:
    """Arşivlenmiş çalışmayı kaynak gibi oynatır (``SourceClient``): kaynağa bağlanmadan yeniden işleme.

    ``last_ref``: son verilen ham nesnenin asıl ``raw_ref``'i (çalıştırıcının ``archive`` kancası olarak kullanılır;
    yeniden işlenen kayıtlar asıl arşiv başvurusunu taşır)."""

    def __init__(self, run: ArchivedRun) -> None:
        self.run = run
        self.data_source = run.data_source
        self.last_ref: str | None = None

    @property
    def mode(self) -> Literal["snapshot", "delta"]:
        """Asıl çalışma tamamlanmamışsa ``delta``: eksik arşiv "kaynakta yok" tespitine yol açmasın."""
        return "snapshot" if self.run.manifest.get("completed") and self.run.manifest.get("mode") == "snapshot" \
            else "delta"

    def fetch(self, mode: Literal["snapshot", "delta"] = "snapshot",
              since: datetime | None = None) -> Iterator[dict[str, Any]]:
        for ref, raw in self.run.records():
            self.last_ref = ref
            yield raw

    def ref(self, _raw: dict[str, Any]) -> str:
        if self.last_ref is None:
            raise ArchiveError("Arşiv başvurusu yok")
        return self.last_ref
