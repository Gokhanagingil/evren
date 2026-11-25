-- 0010 · Gölge mod raporu ve kanıt anlık görüntüsü (F0-13)
--
-- Rapor: ITSM rolündeki mevcut CMDB'nin (bugün ServiceNow) Evren gözlemleriyle karşılaştırılması — kapsam,
-- mükerrer adayları, değer farkları, gölge kaynak uyumu. Rapor API'de canlı üretilir; GRC Platformu'na kanıt
-- olarak giden sürüm insight.evidence_snapshot'a değiştirilemez biçimde (salt ekleme, SHA-256) kaydedilir.

BEGIN;

-- Mükerrer adayları kimlik dizininden bulunur (seri, UUID, FQDN, MAC, ad). Dizin CI'da zaten görünen
-- değerleri içerir; API yalnızca okur.
GRANT SELECT ON ingest.identity_key, ingest.identity_profile TO evren_app_api;

CREATE TABLE insight.evidence_snapshot (
  sys_id            meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  kind              text NOT NULL CHECK (kind IN ('shadow_report')),
  generated_at      timestamptz NOT NULL DEFAULT now(),
  generated_by      text NOT NULL,
  collection_status text NOT NULL CHECK (collection_status IN ('complete', 'partial')),
  content           jsonb NOT NULL CHECK (jsonb_typeof(content) = 'object'),
  content_sha256    text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  note              text CHECK (length(note) <= 2000)
);
CREATE INDEX evidence_snapshot_kind ON insight.evidence_snapshot (kind, generated_at DESC);

-- Kanıt değiştirilemez: güncelleme ve silme sahibi dahil herkese kapalı (denetim iziyle aynı tetikleyici).
CREATE TRIGGER evidence_snapshot_append_only BEFORE UPDATE OR DELETE ON insight.evidence_snapshot
  FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only();
CREATE TRIGGER evidence_snapshot_no_truncate BEFORE TRUNCATE ON insight.evidence_snapshot
  FOR EACH STATEMENT EXECUTE FUNCTION audit.tg_append_only();

GRANT SELECT ON insight.evidence_snapshot TO evren_app_api, evren_auditor_ro;
GRANT INSERT (kind, generated_by, collection_status, content, content_sha256, note) ON insight.evidence_snapshot
  TO evren_app_api;
-- 0003'teki "insight şemasında tüm tablolar" yetkisi yalnızca o anki tabloları kapsar; açıkça hiçbir şey verilmez.
REVOKE ALL ON insight.evidence_snapshot FROM evren_app_reconciler, evren_readonly;

-- Kanıt kaydetme izni: veri sorumlusu ve denetçi (rapor okuma cmdb_ci/read ile).
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description) VALUES
  ('evren_data_steward', 'shadow_report', 'create', NULL, 'internal', NULL,
   'Gölge mod raporunu GRC Platformu için değiştirilemez kanıt olarak kaydeder'),
  ('evren_auditor', 'shadow_report', 'create', NULL, 'internal', NULL,
   'Gölge mod raporunu denetim kanıtı olarak kaydeder (veri değiştirmez)');

COMMENT ON TABLE insight.evidence_snapshot IS 'GRC Platformu''na giden değiştirilemez rapor kanıtları (SHA-256 ile).';

COMMIT;
