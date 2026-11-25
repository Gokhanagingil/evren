-- 0005 · Kimlik aday dizini (PostgreSQL) — uzlaştırıcı servisi (F0-10)
--
-- evren_core.identity.CandidateIndex sözleşmesinin kesin anahtar tarafı. Her CI için birleştirilmiş kimlik
-- anahtarları (ad, FQDN, seri, UUID, MAC, IP, harici kimlik…) tutulur; yeni kaynak kaydı bu indeks
-- üzerinden adaylara indirgenir (blocking). Bulanık ad araması ileride OpenSearch ile tamamlanır (F1-08).
--
-- Anahtarlar yalnızca birleşir (bir kaynak yeni anahtar bildirdikçe eklenir). Ayırma (split) işlemi
-- profili yeniden kurar; bu yüzden uzlaştırıcıya silme yetkisi verilmez, ayırma ayrı bir göçle gelir.

BEGIN;

CREATE TABLE ingest.identity_profile (
  ci_sys_id  meta.sysid PRIMARY KEY REFERENCES cmdb.cmdb_ci(sys_id),
  class_name text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  family     text,
  keys       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(keys) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingest.identity_key (
  key_type  text NOT NULL CHECK (key_type IN ('name', 'compact', 'fqdn', 'serial', 'uuid', 'mac', 'ip', 'ext',
                                                'correlation', 'vmobj', 'prefix')),
  family    text NOT NULL DEFAULT '',
  value     text NOT NULL CHECK (length(value) BETWEEN 1 AND 512),
  ci_sys_id meta.sysid NOT NULL REFERENCES ingest.identity_profile(ci_sys_id),
  PRIMARY KEY (key_type, family, value, ci_sys_id)
);
CREATE INDEX identity_key_ci ON ingest.identity_key (ci_sys_id);

-- Eşleştirme kuyruğu kaydının türü: ekranda ayrı sekmeler, öncelik ve karar seçenekleri buna göre.
ALTER TABLE ingest.match_candidate
  ADD COLUMN kind text NOT NULL DEFAULT 'review'
    CHECK (kind IN ('review', 'insufficient', 'unmatched_shadow', 'unmatched_retiring'));
CREATE INDEX match_candidate_kind ON ingest.match_candidate (kind, status, priority, created_at);

GRANT SELECT, INSERT, UPDATE ON ingest.identity_profile TO evren_app_reconciler;
GRANT SELECT, INSERT ON ingest.identity_key TO evren_app_reconciler;
GRANT SELECT ON ingest.identity_profile, ingest.identity_key TO evren_auditor_ro;

COMMENT ON TABLE ingest.identity_key IS 'Kimlik aday dizini: (anahtar türü, sınıf ailesi, değer) → CI. Anahtarlar yalnızca birleşir.';

COMMIT;
