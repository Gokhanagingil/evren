-- 0029 — Sistem durumu (U8.1, ADR-0032).
--
-- Servis nabızları ayrıntı taşır (ingest.worker_heartbeat.details): alım tüketicisi ve dizinleyici Kafka tüketici
-- gecikmesini, alım geçidi Kafka bağlantısını, uzlaştırıcı olay yayıncısının açık olup olmadığını bildirir. Sistem
-- durumu sayfası bunları, veritabanı, göç defteri ve OpenSearch denetimiyle birleştirir.
--
-- Sistem durumu iç ayrıntı gösterir (sürümler, bağlantı sayıları, kuyruklar); yalnızca "platform" okuma izni olan
-- görür: yeni "Platform operatörü" rolü ve Veri sorumlusu (denetçi ve break-glass '*' ile zaten görür).
BEGIN;

ALTER TABLE ingest.worker_heartbeat
  ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object');

ALTER TABLE ingest.worker_heartbeat DROP CONSTRAINT IF EXISTS worker_heartbeat_kind_check;
ALTER TABLE ingest.worker_heartbeat
  ADD CONSTRAINT worker_heartbeat_kind_check CHECK (kind IN ('requests', 'ingest', 'indexer', 'connector', 'gateway'));

-- Alım geçidi kendi nabzını yazar (kaynak kaydına erişimi yine yoktur)
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_ingest;

-- Göç defteri: göç aracı (tools/migrate.py) da aynı tanımla açar; elle uygulanan kurulumda da var olsun
CREATE TABLE IF NOT EXISTS meta.schema_migration (
  version    text PRIMARY KEY,
  sha256     text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT ON meta.schema_migration TO evren_app_api, evren_auditor_ro;

-- Olay yayını gecikmesi: API yalnızca kimlik ve zaman sütunlarını okur; olay içeriği (payload) kapalı kalır
GRANT USAGE ON SCHEMA events TO evren_app_api;
GRANT SELECT (id, created_at, published_at) ON events.outbox TO evren_app_api;

INSERT INTO iam.role (name, label_tr, description, elevated, machine) VALUES
  ('evren_operator', 'Platform operatörü',
   'Sistem durumunu, servis nabızlarını, kuyrukları ve kaynak çalışmalarını izler; veri değiştirmez', false, false)
ON CONFLICT (name) DO NOTHING;

INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT r, 'platform', 'read', NULL, 'internal', NULL, d
  FROM (VALUES ('evren_operator', 'Sistem durumu (ADR-0032)'),
               ('evren_data_steward', 'Sistem durumu: kaynak çalışmaları ve uzlaştırıcı (ADR-0032)')) v(r, d)
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = v.r AND p.resource = 'platform' AND p.operation = 'read');

COMMIT;
