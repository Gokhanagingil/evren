"""Kaynak sağlığı ve devre kesici.

Bir bağlayıcı kısmi veri döndürdüğünde (API zaman aşımı, yetki kaybı, sayfalama
hatası) binlerce CI'ın "artık görülmüyor" diye eskimiş/emekli işaretlenmesi
CMDB'de en yıkıcı veri kalitesi olayıdır. Devre kesici bunu önler:

* ``OK``    — çalışma normal; silme/eskime işlemleri uygulanır.
* ``WARN``  — sapma var; kayıtlar işlenir, silme/eskime işlemleri uygulanmaz.
* ``OPEN``  — çalışma karantinaya alınır; hiçbir yıkıcı işlem yapılmaz, kayıtlar
              yalnızca analiz için saklanır ve operatöre bildirim gider.

Taban çizgisi son başarılı çalışmaların medyanı ve MAD (median absolute
deviation) ile hesaplanır; tek bir uç değer taban çizgisini bozmaz.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median

__all__ = ["BreakerState", "BreakerThresholds", "BreakerVerdict", "evaluate_run"]


class BreakerState(StrEnum):
    OK = "ok"
    WARN = "warn"
    OPEN = "open"


@dataclass(frozen=True)
class BreakerThresholds:
    warn_drop_pct: float = 5.0
    open_drop_pct: float = 15.0
    mad_k: float = 6.0
    open_changed_pct: float = 35.0
    warn_changed_pct: float = 15.0
    open_error_pct: float = 2.0
    min_baseline_runs: int = 3


DEFAULT_THRESHOLDS = BreakerThresholds()


@dataclass(frozen=True)
class BreakerVerdict:
    state: BreakerState
    reasons: tuple[str, ...]
    baseline: float | None

    @property
    def allows_destructive(self) -> bool:
        return self.state is BreakerState.OK


def evaluate_run(
    record_count: int,
    baseline_counts: Sequence[int],
    *,
    changed_count: int = 0,
    error_count: int = 0,
    completed: bool = True,
    thresholds: BreakerThresholds = DEFAULT_THRESHOLDS,
    mode: str = "snapshot",
) -> BreakerVerdict:
    """Çalışmanın sağlığı. ``mode='delta'``: yalnızca değişenler gelir; hacim, sıfır kayıt ve değişim oranı kuralları
    anlamsızdır (delta zaten "görülmeyeni eksik say" işlemine girmez). Yalnızca tamamlanma ve hata oranı denetlenir.
    """
    t = thresholds
    reasons: list[str] = []
    state = BreakerState.OK

    def raise_to(new: BreakerState, reason: str) -> None:
        nonlocal state
        order = [BreakerState.OK, BreakerState.WARN, BreakerState.OPEN]
        if order.index(new) > order.index(state):
            state = new
        reasons.append(reason)

    if not completed:
        raise_to(BreakerState.OPEN, "Çalışma tamamlanmadı (kısmi toplama); yıkıcı işlem yapılmaz")
    if record_count == 0 and mode != "delta":
        raise_to(BreakerState.OPEN, "Kaynak sıfır kayıt döndürdü")

    total = max(record_count, 1)
    error_pct = 100.0 * error_count / total
    if error_pct >= t.open_error_pct and (record_count or error_count):
        raise_to(BreakerState.OPEN, f"Hatalı kayıt oranı %{error_pct:.1f}")
    if mode == "delta":
        reasons.append("Delta çalışması: hacim ve değişim oranı denetlenmez; eskime yalnızca tam çalışmayla")
        return BreakerVerdict(state, tuple(reasons), None)

    changed_pct = 100.0 * changed_count / total
    if changed_pct >= t.open_changed_pct:
        raise_to(BreakerState.OPEN, f"Kayıtların %{changed_pct:.1f}'i değişti; eşleme hatası olabilir")
    elif changed_pct >= t.warn_changed_pct:
        raise_to(BreakerState.WARN, f"Olağandışı değişim oranı %{changed_pct:.1f}")

    baseline: float | None = None
    if len(baseline_counts) >= t.min_baseline_runs:
        baseline = float(median(baseline_counts))
        mad = float(median(abs(c - baseline) for c in baseline_counts))
        drop = baseline - record_count
        drop_pct = 100.0 * drop / baseline if baseline else 0.0
        robust_limit = t.mad_k * max(mad, 1.0)
        if drop_pct >= t.open_drop_pct and drop > robust_limit:
            raise_to(BreakerState.OPEN, f"Kayıt sayısı taban çizgisine göre %{drop_pct:.1f} düştü")
        elif drop_pct >= t.warn_drop_pct and drop > robust_limit:
            raise_to(BreakerState.WARN, f"Kayıt sayısı %{drop_pct:.1f} düştü")
    else:
        raise_to(BreakerState.WARN, "Taban çizgisi için yeterli geçmiş çalışma yok; eskime işlemleri bekletilir")

    return BreakerVerdict(state, tuple(reasons), baseline)
