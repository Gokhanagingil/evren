"""API metrikleri ve ``/metrics`` (U8.9, ADR-0040; F1-15).

* İstek sayısı ve süresi: yöntem, rota şablonu (``/api/v1/ci/{sys_id}`` — kimlik değeri değil) ve durum sınıfı
  (2xx/4xx/5xx). Kişi, sorgu metni veya kimlik etikete girmez (kardinalite ve gizlilik).
* Platform göstergeleri kazıma anında veritabanından okunur: servis nabzı ve Kafka gecikmesi, olay yayını birikmesi,
  etkin uyarılar, açık ve süresi geçen görevler, açık bulgular, son denetim mührünün yaşı, bildirim teslim kuyruğu.

``/metrics`` yalnızca ``EVREN_METRICS_TOKEN_FILE`` tanımlıysa açıktır ve ``Authorization: Bearer <belirteç>`` ister
(sabit süreli karşılaştırma). Tanımlı değilse 404: iç ayrıntı oturumsuz açılmaz.
"""
from __future__ import annotations

import hmac
from typing import Any

import psycopg
from psycopg_pool import PoolTimeout

from evren_core.metrics import Registry, render_samples

__all__ = ["ApiMetrics"]

#: Kazıma anında okunan göstergeler: (ad, açıklama, SQL). SQL sabittir; her satır (etiketler sözlüğü, değer) döner.
PLATFORM_GAUGES: tuple[tuple[str, str, str], ...] = (
    ("evren_service_up", "Servis türünün canlı örneği var mı (1/0)",
     """SELECT jsonb_build_object('kind', kind),
               max(CASE WHEN stopped_at IS NULL AND seen_at > now() - greatest(3 * poll_seconds, 120)
                         * interval '1 second' THEN 1 ELSE 0 END)
          FROM ingest.worker_heartbeat WHERE seen_at > now() - interval '1 day' GROUP BY kind"""),
    ("evren_kafka_consumer_lag", "Kafka tüketici gecikmesi (kayıt), canlı örneklerin toplamı",
     """SELECT jsonb_build_object('kind', kind), coalesce(sum((details->'kafka'->>'lag')::numeric), 0)
          FROM ingest.worker_heartbeat
         WHERE stopped_at IS NULL AND seen_at > now() - greatest(3 * poll_seconds, 120) * interval '1 second'
           AND details->'kafka' ? 'lag' GROUP BY kind"""),
    ("evren_outbox_backlog", "Yayımlanmamış olay sayısı",
     "SELECT '{}'::jsonb, count(*) FROM events.outbox WHERE published_at IS NULL"),
    ("evren_outbox_oldest_age_seconds", "Yayımlanmamış en eski olayın yaşı (saniye)",
     """SELECT '{}'::jsonb, coalesce(extract(epoch FROM now() - min(created_at)), 0)
          FROM events.outbox WHERE published_at IS NULL"""),
    ("evren_alerts_firing", "Etkin platform uyarısı sayısı",
     """SELECT jsonb_build_object('severity', s), (SELECT count(*) FROM ops.alert WHERE state = 'firing' AND severity = s)
          FROM unnest(ARRAY['info', 'warning', 'critical']) AS s"""),
    ("evren_tasks_open", "Açık yönetişim görevi",
     "SELECT '{}'::jsonb, count(*) FROM gov.task WHERE state = 'open'"),
    ("evren_tasks_overdue", "Süresi geçmiş açık yönetişim görevi",
     "SELECT '{}'::jsonb, count(*) FROM gov.task WHERE state = 'open' AND due_at < now()"),
    ("evren_findings_open", "Açık kalite bulgusu",
     "SELECT '{}'::jsonb, count(*) FROM insight.finding WHERE state = 'open'"),
    ("evren_audit_seal_age_seconds", "Son mühürlenen denetim dönemi bitişinden bu yana geçen süre (saniye)",
     "SELECT '{}'::jsonb, coalesce(extract(epoch FROM now() - max(period_end)), 0) FROM audit.seal"),
    ("evren_notification_deliveries", "Bildirim teslim kuyruğu (bekleyen; son 24 saatte başarısız)",
     """SELECT jsonb_build_object('status', s), (SELECT count(*) FROM notify.delivery d WHERE d.status = s
                                                 AND (s = 'pending' OR d.created_at > now() - interval '1 day'))
          FROM unnest(ARRAY['pending', 'failed']) AS s"""),
)


class ApiMetrics:
    def __init__(self, token: str | None) -> None:
        self.token = token
        self.registry = Registry()
        self.requests = self.registry.counter("evren_http_requests_total", "API istek sayısı",
                                              ("method", "route", "status"))
        self.duration = self.registry.histogram("evren_http_request_duration_seconds", "API istek süresi (saniye)",
                                                ("method", "route"))

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def authorized(self, header: str | None) -> bool:
        if not self.token or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip().encode(), self.token.encode())

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.inc(method=method, route=route, status=f"{status // 100}xx")
        self.duration.observe(seconds, method=method, route=route)

    def render(self, pool: Any) -> str:
        extra: list[str] = []
        up = 0
        try:
            with pool.connection(timeout=2) as conn:
                for name, help_text, sql in PLATFORM_GAUGES:
                    rows = conn.execute(sql).fetchall()
                    extra += render_samples(name, help_text, "gauge", ((dict(r[0]), float(r[1])) for r in rows))
            up = 1
        except (psycopg.Error, PoolTimeout):  # veritabanı yoksa yalnızca istek metrikleri ve evren_db_up=0
            extra = []
        extra += render_samples("evren_db_up", "API veritabanına ulaşabiliyor mu (1/0)", "gauge", [({}, up)])
        return self.registry.render(extra)
