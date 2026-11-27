-- 0031 — Yönetişim görevleri: görev, görev kanalı, görev olayları (U8.4, ADR-0035, F1-24).
--
-- Evren sorumlulara görev açar (tek seferlik; U8.5 zamanlanmış kampanya; U8.6 bulgu kuralı). Görevin doğruluk kaynağı
-- Evren'dir (gov.task); kanal yalnızca teslim ve iş takibidir:
--   * evren       — uygulama içi (Size düşen işler, Görevler sayfası); dış kayıt yok.
--   * servicenow  — görev çalışanı (evren_app_governance) ServiceNow'da standart görev tablosuna, standart alanlarla
--                   kayıt açar (correlation_id = görev sys_id), durumu geri okur, Evren'de kapanınca kapatır.
--                   ServiceNow'da iş kuralı, ACL veya alan değişikliği gerekmez (kural 11).
-- Karar Evren'de verilir. ServiceNow'da kararsız kapatılan görev Evren'de açık kalır ve işaretlenir.
BEGIN;

CREATE SCHEMA IF NOT EXISTS gov;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evren_app_governance') THEN
    CREATE ROLE evren_app_governance NOLOGIN INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;

-- ---------------------------------------------------------------- kanallar
CREATE TABLE IF NOT EXISTS gov.task_channel (
  name        text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_-]{1,40}$'),
  kind        text NOT NULL CHECK (kind IN ('evren', 'servicenow')),
  label_tr    text NOT NULL,
  lifecycle   text NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('shadow', 'active', 'retiring', 'retired')),
  is_default  boolean NOT NULL DEFAULT false,
  -- Sır içermez: hedef tablo, durum eşlemesi, varsayılan atama grubu
  settings    jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(settings) = 'object'),
  updated_by  text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT is_default OR lifecycle = 'active'),
  CHECK (kind <> 'evren' OR lifecycle = 'active')  -- Evren kanalı her zaman vardır
);
CREATE UNIQUE INDEX IF NOT EXISTS task_channel_one_default ON gov.task_channel (is_default) WHERE is_default;

INSERT INTO gov.task_channel (name, kind, label_tr, lifecycle, is_default, settings) VALUES
  ('evren', 'evren', 'Evren (uygulama içi)', 'active', false, '{}'),
  ('servicenow', 'servicenow', 'ServiceNow', 'active', true,
   '{"table": "sc_task", "close_state": "3", "cancel_state": "4",
     "closed_states": {"3": "done", "4": "cancelled", "7": "cancelled"}}')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------- görevler
CREATE SEQUENCE IF NOT EXISTS gov.task_number_seq START 1001;

CREATE TABLE IF NOT EXISTS gov.task (
  sys_id           meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  number           text NOT NULL UNIQUE DEFAULT 'EGT' || lpad(nextval('gov.task_number_seq')::text, 7, '0'),
  kind             text NOT NULL CHECK (kind IN ('manual', 'attestation', 'access_review', 'checklist', 'finding')),
  title            text NOT NULL CHECK (length(title) BETWEEN 3 AND 160),
  description      text NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
  ci_sys_id        meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  assignee_user    meta.sysid REFERENCES ref.sys_user(sys_id),
  assignee_group   meta.sysid REFERENCES ref.sys_user_group(sys_id),
  assignee_basis   text NOT NULL CHECK (assignee_basis IN ('owned_by', 'managed_by', 'support_group', 'assigned',
                                                           'unassigned')),
  state            text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'done', 'cancelled')),
  outcome          text CHECK (outcome IN ('confirmed', 'corrected', 'not_applicable', 'rejected', 'cancelled')),
  outcome_note     text CHECK (length(outcome_note) <= 2000),
  due_at           timestamptz NOT NULL,
  source           text NOT NULL CHECK (source IN ('manual', 'campaign', 'rule')),
  source_ref       text,
  dedup_key        text CHECK (length(dedup_key) <= 300),
  channel          text NOT NULL REFERENCES gov.task_channel(name),
  -- Teslim (yalnızca görev çalışanı yazar)
  delivery_status  text NOT NULL DEFAULT 'pending'
                   CHECK (delivery_status IN ('pending', 'delivered', 'not_needed', 'failed', 'closed_sent', 'kept_in_evren')),
  delivery_note    text CHECK (length(delivery_note) <= 500),
  delivery_attempts integer NOT NULL DEFAULT 0,
  external_ref     text CHECK (external_ref ~ '^[0-9a-f]{32}$'),
  external_number  text CHECK (length(external_number) <= 40),
  external_state   text CHECK (length(external_state) <= 40),
  external_closed  boolean NOT NULL DEFAULT false,
  last_synced_at   timestamptz,
  created_by       text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  closed_by        text,
  closed_at        timestamptz,
  CHECK ((state = 'open') = (closed_at IS NULL)),
  CHECK (state = 'open' OR outcome IS NOT NULL),
  CHECK (assignee_basis = 'unassigned' OR assignee_user IS NOT NULL OR assignee_group IS NOT NULL)
);
-- Aynı nedenle (kampanya + CI, kural + CI) yalnızca bir açık görev
CREATE UNIQUE INDEX IF NOT EXISTS task_open_dedup ON gov.task (dedup_key) WHERE state = 'open' AND dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS task_open_user ON gov.task (assignee_user) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS task_open_group ON gov.task (assignee_group) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS task_ci ON gov.task (ci_sys_id);
CREATE INDEX IF NOT EXISTS task_delivery ON gov.task (channel, delivery_status) WHERE delivery_status IN ('pending', 'failed', 'delivered');

-- Kapanan görev yeniden açılmaz ve kararı değişmez; yalnızca teslim alanları (dış kaydı kapatma) güncellenir
CREATE OR REPLACE FUNCTION gov.tg_task_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.state <> 'open' AND (NEW.state, NEW.outcome, NEW.outcome_note, NEW.closed_at, NEW.closed_by, NEW.title,
                              NEW.assignee_user, NEW.assignee_group, NEW.due_at)
       IS DISTINCT FROM (OLD.state, OLD.outcome, OLD.outcome_note, OLD.closed_at, OLD.closed_by, OLD.title,
                         OLD.assignee_user, OLD.assignee_group, OLD.due_at) THEN
    RAISE EXCEPTION 'Kapanan görev değiştirilemez (%)', OLD.number USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.sys_id <> OLD.sys_id OR NEW.number <> OLD.number OR NEW.created_by <> OLD.created_by
     OR NEW.created_at <> OLD.created_at OR NEW.source <> OLD.source THEN
    RAISE EXCEPTION 'Görevin kimliği değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS task_guard ON gov.task;
CREATE TRIGGER task_guard BEFORE UPDATE ON gov.task FOR EACH ROW EXECUTE FUNCTION gov.tg_task_guard();

-- ---------------------------------------------------------------- olaylar (salt ekleme)
CREATE TABLE IF NOT EXISTS gov.task_event (
  id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  task    meta.sysid NOT NULL REFERENCES gov.task(sys_id),
  at      timestamptz NOT NULL DEFAULT now(),
  actor   text NOT NULL,
  action  text NOT NULL CHECK (action IN ('created', 'delivered', 'delivery_failed', 'kept_in_evren', 'synced',
                                          'external_closed', 'completed', 'cancelled', 'closed_sent', 'reassigned')),
  detail  jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail) = 'object')
);
CREATE INDEX IF NOT EXISTS task_event_task ON gov.task_event (task, at DESC);
DROP TRIGGER IF EXISTS task_event_append_only ON gov.task_event;
CREATE TRIGGER task_event_append_only BEFORE UPDATE OR DELETE ON gov.task_event
  FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only();

-- ---------------------------------------------------------------- nabız türü
ALTER TABLE ingest.worker_heartbeat DROP CONSTRAINT IF EXISTS worker_heartbeat_kind_check;
ALTER TABLE ingest.worker_heartbeat ADD CONSTRAINT worker_heartbeat_kind_check
  CHECK (kind IN ('requests', 'ingest', 'indexer', 'connector', 'gateway', 'governance'));

-- ---------------------------------------------------------------- yetkiler
-- API: görev açar (tek seferlik), kapatır, kanal ayarını değiştirir; teslim alanlarına yazamaz
GRANT USAGE ON SCHEMA gov TO evren_app_api, evren_app_governance, evren_auditor_ro;
GRANT SELECT ON gov.task_channel, gov.task, gov.task_event TO evren_app_api, evren_app_governance, evren_auditor_ro;
GRANT INSERT (kind, title, description, ci_sys_id, assignee_user, assignee_group, assignee_basis, due_at, source,
              source_ref, dedup_key, channel, delivery_status, created_by) ON gov.task TO evren_app_api;
GRANT UPDATE (state, outcome, outcome_note, closed_by, closed_at) ON gov.task TO evren_app_api;
GRANT USAGE ON SEQUENCE gov.task_number_seq TO evren_app_api, evren_app_governance;
GRANT INSERT (task, actor, action, detail) ON gov.task_event TO evren_app_api, evren_app_governance;
GRANT UPDATE (lifecycle, is_default, settings, updated_by, updated_at) ON gov.task_channel TO evren_app_api;

-- Görev çalışanı: teslim ve eşitleme alanları; CMDB'yi okur, yazamaz
GRANT evren_readonly TO evren_app_governance WITH INHERIT TRUE;
GRANT UPDATE (delivery_status, delivery_note, delivery_attempts, external_ref, external_number, external_state,
              external_closed, last_synced_at) ON gov.task TO evren_app_governance;
GRANT USAGE ON SCHEMA ingest TO evren_app_governance;
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_governance;
-- ServiceNow'da da bulunan CI'ı ve sorumlunun kaynağını bilmek için (yalnızca gereken sütunlar)
GRANT SELECT (ci_sys_id, data_source, source_key, active) ON ingest.identity_link TO evren_app_governance;
GRANT SELECT (name, role) ON ingest.data_source TO evren_app_governance;

-- Okuma: kişi kendine ve grubuna atanan görevleri görür (API süzer); Konfigürasyon yöneticisi ve Denetçi hepsini

COMMIT;
