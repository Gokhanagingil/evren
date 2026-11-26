-- 0021 — Değişiklik kayıtları ve yetkisiz değişiklik tespiti (F1-04, ADR-0025).
--
-- * ServiceNow değişiklik kayıtları (change_request, task_ci) bağlayıcı → geçit → uzlaştırıcı yolundan gelir; uzlaştırıcı
--   aynı sys_id ile ref.sn_change_request ve ref.sn_task_ci'ya yazar. Yalnızca değişiklik kaynağı rolü yazar (uygulama
--   katmanı); veritabanı uzlaştırıcıya yalnızca evren_core.reference.CHANGE_TABLES sütunlarında yetki verir (testli).
--   Silme yetkisi yoktur.
-- * state/approval/type sütunlarında Evren'in değişiklik sözlüğü durur; ürün kodu eşleme dosyasında çevrilir (kural 8).
-- * insight.config_change: gözlemci rolündeki kaynağın kendi kaydında izlenen özniteliğin değiştiği gözlemler ve
--   onaylı pencereye göre değerlendirmesi. Öznitelik geçmişi taşıdığı için okuyucu rolü (evren_readonly, MCP) okumaz;
--   API izin ve alan hassasiyetiyle süzer.
BEGIN;

ALTER TABLE ref.sn_change_request
  ADD COLUMN IF NOT EXISTS work_start timestamptz,
  ADD COLUMN IF NOT EXISTS work_end   timestamptz,
  ADD CONSTRAINT sn_change_state_vocab CHECK (state IS NULL OR state IN
    ('new', 'assess', 'authorize', 'scheduled', 'implement', 'review', 'closed', 'canceled', 'other')),
  ADD CONSTRAINT sn_change_approval_vocab CHECK (approval IS NULL OR approval IN
    ('not_requested', 'requested', 'approved', 'rejected', 'cancelled', 'not_required', 'skipped', 'other')),
  ADD CONSTRAINT sn_change_type_vocab CHECK (type IS NULL OR type IN ('standard', 'normal', 'emergency', 'other'));
COMMENT ON COLUMN ref.sn_change_request.state IS 'Evren değişiklik sözlüğü; ürün durum kodu eşleme dosyasında çevrilir (ADR-0025)';
COMMENT ON COLUMN ref.sn_change_request.approval IS 'Evren değişiklik sözlüğü (approved, requested…; ADR-0025)';
COMMENT ON COLUMN ref.sn_change_request.work_start IS 'Gerçekleşen başlangıç (ServiceNow work_start)';
COMMENT ON COLUMN ref.sn_change_request.work_end IS 'Gerçekleşen bitiş (ServiceNow work_end)';
CREATE INDEX IF NOT EXISTS sn_change_ci ON ref.sn_change_request (cmdb_ci);

ALTER TABLE ref.sn_task_ci ADD COLUMN IF NOT EXISTS sys_updated_on timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS sn_task_ci_task ON ref.sn_task_ci (task);

GRANT INSERT (sys_id, number, short_description, type, state, approval, start_date, end_date, work_start, work_end,
              cmdb_ci, sys_updated_on) ON ref.sn_change_request TO evren_app_reconciler;
GRANT UPDATE (number, short_description, type, state, approval, start_date, end_date, work_start, work_end, cmdb_ci,
              sys_updated_on) ON ref.sn_change_request TO evren_app_reconciler;
GRANT INSERT (sys_id, task, ci_item, sys_updated_on) ON ref.sn_task_ci TO evren_app_reconciler;
GRANT UPDATE (task, ci_item, sys_updated_on) ON ref.sn_task_ci TO evren_app_reconciler;

CREATE TABLE insight.config_change (
  sys_id          meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  ci_sys_id       meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  sys_class_name  text NOT NULL,
  attribute       text NOT NULL,
  old_value       jsonb,
  new_value       jsonb,
  data_source     text NOT NULL REFERENCES ingest.data_source(name),
  run_id          uuid,
  observed_from   timestamptz,             -- kaynağın eski değeri en son gördüğü an
  observed_at     timestamptz NOT NULL,    -- kaynağın yeni değeri ilk gördüğü an
  recorded_at     timestamptz NOT NULL DEFAULT now(),
  verdict         text NOT NULL DEFAULT 'pending'
                  CHECK (verdict IN ('pending', 'authorized', 'unauthorized', 'suppressed')),
  evaluated_at    timestamptz,
  change_sys_id   meta.sysid,
  change_number   text,
  anomaly_sys_id  meta.sysid REFERENCES insight.anomaly(sys_id),
  detail          jsonb NOT NULL DEFAULT '{}',
  CHECK (observed_from IS NULL OR observed_from <= observed_at),
  CHECK (verdict <> 'authorized' OR change_number IS NOT NULL)
);
CREATE INDEX config_change_pending ON insight.config_change (recorded_at) WHERE verdict = 'pending';
CREATE INDEX config_change_ci ON insight.config_change (ci_sys_id, observed_at DESC);
CREATE INDEX config_change_anomaly ON insight.config_change (anomaly_sys_id) WHERE anomaly_sys_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS anomaly_ci_kind ON insight.anomaly (ci_sys_id, kind, status);
COMMENT ON TABLE insight.config_change IS
  'Gözlenen yapılandırma değişiklikleri ve onaylı değişiklik penceresine göre değerlendirmesi (ADR-0025)';

-- API, Evren'de doğup replikaya giden CI'ın değişiklik kayıtlarını ServiceNow sys_id'siyle bulur: yalnızca iki sütun.
GRANT USAGE ON SCHEMA sync TO evren_app_api;
GRANT SELECT (evren_sys_id, sn_sys_id) ON sync.sn_ci_map TO evren_app_api;

-- İnsan kararı (kural 10): yetkisiz değişiklik anomalisini incelendi / yanlış alarm olarak işaretleme. Karar veren,
-- zaman ve gerekçe anomali satırında; her karar audit.security_event'e de yazılır (API).
ALTER TABLE insight.anomaly
  ADD COLUMN IF NOT EXISTS reviewed_by text,
  ADD COLUMN IF NOT EXISTS reviewed_at timestamptz,
  ADD COLUMN IF NOT EXISTS review_note text CHECK (review_note IS NULL OR length(review_note) <= 2000);
GRANT UPDATE (reviewed_by, reviewed_at, review_note) ON insight.anomaly TO evren_app_api;

ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT 'evren_data_steward', 'anomaly', 'anomaly_review', NULL, 'internal', NULL,
       'Yetkisiz değişiklik anomalisini incelendi / yanlış alarm olarak işaretler (ADR-0025)'
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = 'evren_data_steward' AND p.resource = 'anomaly'
                                                    AND p.operation = 'anomaly_review');

REVOKE ALL ON insight.config_change FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON insight.config_change TO evren_app_reconciler;
GRANT SELECT ON insight.config_change TO evren_app_api, evren_auditor_ro;

COMMIT;
