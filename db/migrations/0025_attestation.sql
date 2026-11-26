-- 0025 — Sahiplik onayı (U6.4, F1-17, ADR-0027).
--
-- CI'ın sorumlusu (owned_by / managed_by alanındaki kullanıcı) kaydı onaylar veya gerekçeyle reddeder. API yalnızca
-- isteği yazar (istek sütunları); uzlaştırıcı uygulama anında kişinin hâlâ sorumlu ve CI'ın kapsamda olduğunu yeniden
-- denetler, onay alanlarını CI'ın Manual kaynak kaydına yazar ve uzlaştırır (kural 1). Ret, Veri sorumlusuna
-- attestation_rejected anomalisi açar. Süre dolumu ve sahip değişikliği onayı "Henüz incelenmedi" yapar
-- (değişim nedeni: attestation).
BEGIN;

CREATE TABLE ingest.attestation_request (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  ci_sys_id      meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  decision       text NOT NULL CHECK (decision IN ('attested', 'rejected')),
  reason         text CHECK (reason IN ('not_owner', 'not_exists', 'data_wrong')),
  note           text CHECK (length(note) <= 2000),
  attester_user  meta.sysid NOT NULL REFERENCES ref.sys_user(sys_id),
  requested_by   text NOT NULL,
  requested_at   timestamptz NOT NULL DEFAULT now(),
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  result_note    text,
  applied_at     timestamptz,
  CHECK ((status = 'pending') = (applied_at IS NULL)),
  -- ret gerekçeli bir beyandır: neden kodu ve açıklama zorunlu; onayda neden kodu olmaz
  CHECK (decision = 'attested' AND reason IS NULL
         OR decision = 'rejected' AND reason IS NOT NULL AND length(btrim(coalesce(note, ''))) > 0)
);
-- CI başına tek bekleyen istek (çift tıklama, eşzamanlı iki karar)
CREATE UNIQUE INDEX attestation_request_pending ON ingest.attestation_request (ci_sys_id) WHERE status = 'pending';
CREATE INDEX attestation_request_ci ON ingest.attestation_request (ci_sys_id, requested_at DESC);
COMMENT ON TABLE ingest.attestation_request IS 'Sahiplik onayı istekleri; uzlaştırıcı yeniden denetleyip uygular (ADR-0027)';

CREATE TRIGGER attestation_request_notify AFTER INSERT ON ingest.attestation_request
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();

-- API: isteği yazar; durumu ve sonucu değiştiremez. Uzlaştırıcı: uygular; içeriği değiştiremez.
GRANT SELECT ON ingest.attestation_request TO evren_app_api, evren_app_reconciler, evren_auditor_ro;
GRANT INSERT (ci_sys_id, decision, reason, note, attester_user, requested_by)
  ON ingest.attestation_request TO evren_app_api;
GRANT UPDATE (status, result_note, applied_at) ON ingest.attestation_request TO evren_app_reconciler;

-- Değişim geçmişinde onay ve süre dolumu kendi nedeniyle görünür
ALTER TABLE audit.ci_change DROP CONSTRAINT IF EXISTS ci_change_reason_check;
ALTER TABLE audit.ci_change ADD CONSTRAINT ci_change_reason_check CHECK (reason IN (
  'reconciliation', 'manual', 'merge', 'split', 'reclassification', 'staleness', 'import', 'schema', 'sn_pull',
  'attestation'));

-- Ret: Veri sorumlusunun ele alacağı anomali (inceleme akışı ADR-0025 ile aynı)
ALTER TABLE insight.anomaly DROP CONSTRAINT IF EXISTS anomaly_kind_check;
ALTER TABLE insight.anomaly ADD CONSTRAINT anomaly_kind_check CHECK (kind IN (
  'unauthorized_change', 'source_volume', 'mass_change', 'duplicate_spike', 'attribute_drift', 'coverage_gap',
  'stale_population', 'relationship_break', 'attestation_rejected', 'other'));

-- Yeni işlem: attest (CI sahibi rolü). İzin tek başına yetmez; kişi CI'ın sorumlusu olmalıdır (API + uzlaştırıcı).
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT 'evren_ci_owner', 'cmdb_ci', 'attest', NULL, 'internal', NULL,
       'Sorumlusu olduğu CI''ın kaydını onaylar veya gerekçeyle reddeder (ADR-0027)'
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = 'evren_ci_owner' AND p.resource = 'cmdb_ci'
                                                    AND p.operation = 'attest');

COMMIT;
