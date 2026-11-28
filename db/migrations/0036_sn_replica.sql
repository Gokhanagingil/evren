-- 0036 — ServiceNow replikası: itme yönü (U9.1, F2-01, ADR-0042).
--
-- Evren ana kaynaktır; ServiceNow CMDB replikadır. sn-sync çalışanı (evren_app_sync) kapsamdaki CI'ları ServiceNow'a
-- Identification and Reconciliation API ile yazar. CMDB'ye yazamaz; yalnızca eşleme durumunu ve çalışmaları yazar.
--
-- * sync.replica_setting: tek satır — mod (kapalı | gölge | etkin) ve tur başına toplu değişiklik sınırı. Gölge modda
--   yük ServiceNow'un işlemeden deneme uç noktasına gider (yazılmaz); etkin modda yazılır.
-- * sync.replica_run: her tur. Sınırı aşan değişiklik "bekletildi" (held) olur; yetkili kişi gerekçeyle onaylayınca
--   (approved) çalışan bekleyenleri gönderir. Aynı anda tek bekleyen/onaylı tur olur.
-- * sync.sn_ci_map: CI başına son sonuç (işlem, ServiceNow'un bulduğu sys_id, gölge sonucu, yeniden deneme zamanı).
--   Çelişki (ServiceNow beklenenden başka kaydı buldu) insan kararıyla çözülür: ServiceNow kaydını kabul et veya
--   dışarıda tut.
-- * sync.sn_rel_map: ilişkilerin replikaya gidişi. Evren'de kalkan ilişki "removed" işaretlenir (IRE ilişki silmez;
--   ServiceNow'da kaldırılması işletim prosedürüdür, ADR-0042).
BEGIN;

CREATE TABLE IF NOT EXISTS sync.replica_setting (
  id           smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  mode         text NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'shadow', 'active')),
  max_changes  integer NOT NULL DEFAULT 200 CHECK (max_changes BETWEEN 1 AND 100000),
  updated_by   text,
  updated_at   timestamptz,
  note         text CHECK (note IS NULL OR length(note) BETWEEN 10 AND 1000)
);
INSERT INTO sync.replica_setting (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS sync.replica_run (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  mode          text NOT NULL CHECK (mode IN ('shadow', 'active')),
  status        text NOT NULL CHECK (status IN ('running', 'done', 'failed', 'held', 'approved')),
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  candidates    integer NOT NULL DEFAULT 0 CHECK (candidates >= 0),
  sent          integer NOT NULL DEFAULT 0 CHECK (sent >= 0),
  inserted      integer NOT NULL DEFAULT 0 CHECK (inserted >= 0),
  updated       integer NOT NULL DEFAULT 0 CHECK (updated >= 0),
  unchanged     integer NOT NULL DEFAULT 0 CHECK (unchanged >= 0),
  conflicts     integer NOT NULL DEFAULT 0 CHECK (conflicts >= 0),
  errors        integer NOT NULL DEFAULT 0 CHECK (errors >= 0),
  relations     integer NOT NULL DEFAULT 0 CHECK (relations >= 0),
  held_reason   text CHECK (held_reason IS NULL OR length(held_reason) <= 300),
  approved_by   text,
  approved_at   timestamptz,
  approve_note  text CHECK (approve_note IS NULL OR length(approve_note) BETWEEN 10 AND 1000),
  last_error    text CHECK (last_error IS NULL OR length(last_error) <= 300),
  worker        text,
  CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
  CHECK (status <> 'approved' OR approved_by IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS replica_run_open ON sync.replica_run ((true)) WHERE status IN ('held', 'approved');
CREATE INDEX IF NOT EXISTS replica_run_recent ON sync.replica_run (started_at DESC);

-- Onay yalnızca bekletilen turda ve bir kez; biten tur değişmez
CREATE OR REPLACE FUNCTION sync.tg_replica_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'replika çalışması silinemez' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status IN ('done', 'failed') THEN
    RAISE EXCEPTION 'biten replika çalışması değişmez' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'approved' AND OLD.status <> 'held' AND OLD.status <> 'approved' THEN
    RAISE EXCEPTION 'yalnızca bekletilen tur onaylanır' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.approved_by IS NOT NULL AND (NEW.approved_by IS DISTINCT FROM OLD.approved_by
      OR NEW.approved_at IS DISTINCT FROM OLD.approved_at OR NEW.approve_note IS DISTINCT FROM OLD.approve_note) THEN
    RAISE EXCEPTION 'onay değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS replica_run_guard ON sync.replica_run;
CREATE TRIGGER replica_run_guard BEFORE UPDATE OR DELETE ON sync.replica_run
  FOR EACH ROW EXECUTE FUNCTION sync.tg_replica_run_guard();

ALTER TABLE sync.sn_ci_map
  ADD COLUMN IF NOT EXISTS last_operation       text,
  -- CI'ın golden_version'ı: son itmede / son gölge denemesinde görülen sürüm (zaman damgası yarışından bağımsız)
  ADD COLUMN IF NOT EXISTS pushed_version       bigint,
  ADD COLUMN IF NOT EXISTS shadow_version       bigint,
  ADD COLUMN IF NOT EXISTS identified_sn_sys_id meta.sysid,
  -- İnsan kararıyla kabul edilen ServiceNow kaydı: sn-sync doğrulayıp yazınca sn_sys_id olur (API sn_sys_id yazamaz)
  ADD COLUMN IF NOT EXISTS accepted_sn_sys_id   meta.sysid,
  ADD COLUMN IF NOT EXISTS message              text CHECK (message IS NULL OR length(message) <= 300),
  ADD COLUMN IF NOT EXISTS skipped_fields       text[],
  ADD COLUMN IF NOT EXISTS next_try_at          timestamptz,
  ADD COLUMN IF NOT EXISTS shadow_verdict       text CHECK (shadow_verdict IN ('ok', 'conflict', 'error')),
  ADD COLUMN IF NOT EXISTS shadow_operation     text,
  ADD COLUMN IF NOT EXISTS shadow_hash          text,
  ADD COLUMN IF NOT EXISTS shadow_at            timestamptz,
  ADD COLUMN IF NOT EXISTS last_run             bigint REFERENCES sync.replica_run(id),
  ADD COLUMN IF NOT EXISTS resolved_by          text,
  ADD COLUMN IF NOT EXISTS resolved_at          timestamptz,
  ADD COLUMN IF NOT EXISTS resolve_note         text CHECK (resolve_note IS NULL OR length(resolve_note) BETWEEN 10 AND 1000),
  ADD COLUMN IF NOT EXISTS updated_at           timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS sn_ci_map_attention ON sync.sn_ci_map (state, updated_at DESC)
  WHERE state IN ('conflict', 'error') OR shadow_verdict IN ('conflict', 'error');

CREATE TABLE IF NOT EXISTS sync.sn_rel_map (
  evren_rel    meta.sysid PRIMARY KEY,
  parent       meta.sysid NOT NULL,
  child        meta.sysid NOT NULL,
  type_name    text NOT NULL,
  state        text NOT NULL CHECK (state IN ('in_sync', 'error', 'removed')),
  attempts     integer NOT NULL DEFAULT 0,
  message      text CHECK (message IS NULL OR length(message) <= 300),
  pushed_at    timestamptz,
  removed_at   timestamptz,
  next_try_at  timestamptz,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sn_rel_map_state ON sync.sn_rel_map (state);

-- Servis nabzı: sn-sync
ALTER TABLE ingest.worker_heartbeat DROP CONSTRAINT IF EXISTS worker_heartbeat_kind_check;
ALTER TABLE ingest.worker_heartbeat ADD CONSTRAINT worker_heartbeat_kind_check
  CHECK (kind IN ('requests', 'ingest', 'indexer', 'connector', 'gateway', 'governance', 'sn_sync'));

-- ---------------------------------------------------------------- yetkiler
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest', 'retire',
  'task_admin', 'audit_review', 'ops_admin', 'replica_admin'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT r, 'platform', 'replica_admin', NULL, 'internal', NULL,
       'ServiceNow replikasının modunu ve kapsamını değiştirir, bekletilen toplu itmeyi onaylar, çelişkiyi çözer (ADR-0042)'
  FROM unnest(ARRAY['evren_config_manager', 'evren_admin']) AS r
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = r AND p.operation = 'replica_admin');

-- sn-sync çalışanı: eşleme ve çalışma yazar, CMDB'ye yazamaz. Eski tasarımdan kalan kaynak kaydı yazma yetkisi geri
-- alınır (ServiceNow'dan alım bağlayıcı ve uzlaştırıcıdan geçer, ADR-0020).
REVOKE INSERT, UPDATE ON ingest.source_record, ingest.source_run FROM evren_app_sync;
GRANT SELECT, INSERT, UPDATE ON sync.sn_ci_map, sync.sn_rel_map TO evren_app_sync;
GRANT SELECT ON sync.replica_setting TO evren_app_sync;
GRANT SELECT, INSERT, UPDATE (status, finished_at, candidates, sent, inserted, updated, unchanged, conflicts, errors,
                              relations, held_reason, last_error, worker) ON sync.replica_run TO evren_app_sync;
GRANT USAGE ON SEQUENCE sync.replica_run_id_seq TO evren_app_sync;
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_sync;
GRANT SELECT (ci_sys_id, data_source, source_key, active) ON ingest.identity_link TO evren_app_sync;

-- API: durumu okur; ayarı, kapsamı, onayı ve çelişki kararını yalnızca bu sütunlarla yazar. Eşlemede iç hata metnini
-- (last_error) ve yük özetlerini okumaz; ServiceNow sys_id'sini yazamaz (kabul edilen kayıt ayrı sütunda, sn-sync
-- ServiceNow'da doğrulayınca eşlemeye geçer).
GRANT USAGE ON SCHEMA sync TO evren_app_api;
GRANT SELECT ON sync.replica_setting, sync.replica_run, sync.sn_rel_map, sync.sn_sync_scope
  TO evren_app_api, evren_auditor_ro;
GRANT SELECT ON sync.sn_ci_map TO evren_auditor_ro;
GRANT SELECT (evren_sys_id, sn_sys_id, sn_class, state, last_pushed_at, attempts, last_operation, identified_sn_sys_id,
              accepted_sn_sys_id, message, skipped_fields, next_try_at, shadow_verdict, shadow_operation, shadow_at,
              last_run, resolved_by, resolved_at, resolve_note, updated_at) ON sync.sn_ci_map TO evren_app_api;
GRANT UPDATE (mode, max_changes, updated_by, updated_at, note) ON sync.replica_setting TO evren_app_api;
GRANT UPDATE (enabled) ON sync.sn_sync_scope TO evren_app_api;
GRANT UPDATE (status, approved_by, approved_at, approve_note) ON sync.replica_run TO evren_app_api;
GRANT UPDATE (state, accepted_sn_sys_id, attempts, next_try_at, resolved_by, resolved_at, resolve_note, updated_at)
  ON sync.sn_ci_map TO evren_app_api;

COMMIT;
