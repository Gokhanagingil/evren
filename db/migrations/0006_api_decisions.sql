-- 0006 · API kararları ve tekrar güvenliği (F0-09)
--
-- Eşleştirme kararı akışı (CLAUDE.md kural 1: golden record'a tek yazan uzlaştırıcıdır):
--   API: yetki kontrolü → match_candidate'e karar (decision, decision_ci, decided_by …) + events.outbox
--   Uzlaştırıcı: kararı uygular (kimlik bağı veya yeni CI) → uzlaştırma → applied_at
-- API kimlik bağına ve golden record'a yazamaz; yalnızca karar sütunlarını güncelleyebilir.

BEGIN;

ALTER TABLE ingest.match_candidate
  ADD COLUMN decision    text CHECK (decision IN ('merge', 'new_ci', 'defer', 'fix_at_source')),
  ADD COLUMN decision_ci meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  ADD COLUMN applied_at  timestamptz,
  ADD CONSTRAINT match_candidate_merge_target CHECK (decision IS DISTINCT FROM 'merge' OR decision_ci IS NOT NULL);
CREATE INDEX match_candidate_pending_apply ON ingest.match_candidate (decided_at)
  WHERE applied_at IS NULL AND status IN ('accepted', 'created_new', 'rejected');

-- API'nin kuyruktaki yetkisi sütun düzeyine indirilir (0003'te tablo düzeyindeydi).
REVOKE UPDATE ON ingest.match_candidate FROM evren_app_api;
GRANT UPDATE (status, decision, decision_ci, decided_by, decided_at, decision_note) ON ingest.match_candidate
  TO evren_app_api;

-- Tekrar güvenliği (GRC Platformu ile ortak sözleşme): aynı anahtar + aynı içerik → kaydedilen yanıt;
-- aynı anahtar + farklı içerik → 409. Anahtar kişi başınadır.
CREATE SCHEMA IF NOT EXISTS api;
CREATE TABLE api.idempotency (
  principal    text NOT NULL,
  key          text NOT NULL CHECK (key ~ '^[A-Za-z0-9._:-]{8,128}$'),
  method       text NOT NULL,
  path         text NOT NULL,
  request_hash text NOT NULL,
  status_code  smallint NOT NULL,
  response     jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (principal, key)
);
CREATE INDEX idempotency_created ON api.idempotency (created_at);
GRANT USAGE ON SCHEMA api TO evren_app_api, evren_auditor_ro;
GRANT SELECT, INSERT ON api.idempotency TO evren_app_api;
GRANT SELECT ON api.idempotency TO evren_auditor_ro;

COMMIT;
