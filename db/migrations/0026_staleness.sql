-- 0026 — Eskime işletimi (U7.1, F1-06, ADR-0028).
--
-- Uzlaştırıcı, bütün gözlemci kaynakların görmediği CI için öneri açar (insight.staleness_case): eskimiş → emekliye
-- ayırma adayı. Golden record kendiliğinden değişmez. Veri sorumlusu gerekçeyle karar verir (ingest.retirement_request):
-- emekliye ayır (uzlaştırıcı yaşam döngüsü alanlarını Manual kaydına yazar; değişim nedeni staleness) veya koru.
-- Devre kesici OK değilse ne öneri ilerler ne emekliye ayırma uygulanır (kural 4).
BEGIN;

CREATE TABLE insight.staleness_case (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  ci_sys_id      meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  stage          text NOT NULL CHECK (stage IN ('stale', 'candidate')),
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'retired', 'dismissed', 'recovered')),
  rule_class     text NOT NULL,
  last_seen      timestamptz NOT NULL,
  observations   jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(observations) = 'array'),
  opened_at      timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  closed_at      timestamptz,
  decided_by     text,
  decision_note  text CHECK (length(decision_note) <= 2000),
  reappeared_at  timestamptz,
  CHECK ((status = 'open') = (closed_at IS NULL)),
  CHECK (status NOT IN ('retired', 'dismissed') OR (decided_by IS NOT NULL AND decision_note IS NOT NULL))
);
-- CI başına tek açık öneri
CREATE UNIQUE INDEX staleness_case_open ON insight.staleness_case (ci_sys_id) WHERE status = 'open';
CREATE INDEX staleness_case_ci ON insight.staleness_case (ci_sys_id, opened_at DESC);
CREATE INDEX staleness_case_status ON insight.staleness_case (status, stage, last_seen);
COMMENT ON TABLE insight.staleness_case IS 'Eskime önerileri: eskimiş / emekliye ayırma adayı CI (ADR-0028)';

CREATE TABLE ingest.retirement_request (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  case_sys_id    meta.sysid NOT NULL REFERENCES insight.staleness_case(sys_id),
  ci_sys_id      meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  action         text NOT NULL CHECK (action IN ('retire', 'dismiss')),
  note           text NOT NULL CHECK (length(btrim(note)) > 0 AND length(note) <= 2000),
  requested_by   text NOT NULL,
  requested_at   timestamptz NOT NULL DEFAULT now(),
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  result_note    text,
  applied_at     timestamptz,
  CHECK ((status = 'pending') = (applied_at IS NULL))
);
CREATE UNIQUE INDEX retirement_request_pending ON ingest.retirement_request (ci_sys_id) WHERE status = 'pending';
COMMENT ON TABLE ingest.retirement_request IS 'Eskime önerisine insan kararı; uzlaştırıcı yeniden denetleyip uygular (ADR-0028)';

CREATE TRIGGER retirement_request_notify AFTER INSERT ON ingest.retirement_request
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();

-- Öneriyi yalnızca uzlaştırıcı yazar; API ve denetçi okur. Okuyucu ve MCP öneri tablosunu görmez (API sınıf iznine göre süzer).
GRANT SELECT, INSERT, UPDATE ON insight.staleness_case TO evren_app_reconciler;
GRANT SELECT ON insight.staleness_case TO evren_app_api, evren_auditor_ro;
-- Karar isteği: API yalnızca istek sütunlarını yazar; uzlaştırıcı yalnızca sonucu.
GRANT SELECT ON ingest.retirement_request TO evren_app_api, evren_app_reconciler, evren_auditor_ro;
GRANT INSERT (case_sys_id, ci_sys_id, action, note, requested_by) ON ingest.retirement_request TO evren_app_api;
GRANT UPDATE (status, result_note, applied_at) ON ingest.retirement_request TO evren_app_reconciler;

-- Emekli CI yeniden görülürse Veri sorumlusuna anomali
ALTER TABLE insight.anomaly DROP CONSTRAINT IF EXISTS anomaly_kind_check;
ALTER TABLE insight.anomaly ADD CONSTRAINT anomaly_kind_check CHECK (kind IN (
  'unauthorized_change', 'source_volume', 'mass_change', 'duplicate_spike', 'attribute_drift', 'coverage_gap',
  'stale_population', 'relationship_break', 'attestation_rejected', 'retired_ci_seen', 'other'));

-- Yeni işlem: retire (Veri sorumlusu) — eskime önerisinde emekliye ayır / koru kararı
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest', 'retire'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT 'evren_data_steward', 'cmdb_ci', 'retire', NULL, 'internal', NULL,
       'Eskime önerisinde CI''ı emekliye ayırır veya korur; gerekçe zorunlu (ADR-0028)'
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = 'evren_data_steward' AND p.resource = 'cmdb_ci'
                                                    AND p.operation = 'retire');

COMMIT;
