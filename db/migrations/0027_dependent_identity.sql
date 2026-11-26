-- 0027 — Bağımlı CI kimliği (U7.2, F1-07, ADR-0029).
--
-- Bağımlı sınıfın kaydı (ağ adaptörü, uygulama, veritabanı…) ana kaydını bildirir (ingest.source_record.parent:
-- {"source_key", "data_source"}). Uzlaştırıcı ana CI'ı bulur ve yerel anahtarı ana CI bağlamında arar: aynı MAC iki
-- farklı cihazda iki ayrı adaptördür. Anahtarlar küresel kimlik dizinine (ingest.identity_key) hiç girmez.
BEGIN;

ALTER TABLE ingest.source_record
  ADD COLUMN IF NOT EXISTS parent jsonb CHECK (parent IS NULL OR jsonb_typeof(parent) = 'object');

CREATE TABLE ingest.dependent_key (
  parent_ci_sys_id meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  family           text NOT NULL CHECK (family ~ '^[a-z][a-z0-9_]{1,79}$'),
  signature        text NOT NULL CHECK (length(signature) BETWEEN 1 AND 200),
  value            text NOT NULL CHECK (length(value) BETWEEN 1 AND 2000),
  ci_sys_id        meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  data_source      text NOT NULL REFERENCES ingest.data_source(name),
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (parent_ci_sys_id, family, signature, value),
  CHECK (parent_ci_sys_id <> ci_sys_id)
);
CREATE INDEX dependent_key_ci ON ingest.dependent_key (ci_sys_id);
COMMENT ON TABLE ingest.dependent_key IS 'Bağımlı CI kimliği: ana CI bağlamında yerel anahtar (ADR-0029)';

GRANT SELECT, INSERT, UPDATE ON ingest.dependent_key TO evren_app_reconciler;
GRANT SELECT ON ingest.dependent_key TO evren_app_api, evren_auditor_ro;

COMMIT;
