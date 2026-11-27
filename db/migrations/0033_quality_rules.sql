-- 0033 — Kalite ve uyum kuralları, bulgular, CMDB sağlık ölçümü (U8.6, ADR-0037; F1-09, F1-18, F1-26).
--
-- Konfigürasyon yöneticisi panelden kural tanımlar: tür (ilişkisi olmayan CI, boş alan, bir kaynak rolünce
-- görülmeyen CI, koşula uyan CI, izinli olmayan ilişki), kapsam (sınıf + koşul), boyut ve önem, sıklık, görev açılıp
-- açılmayacağı. Görev çalışanı kuralı değerlendirir: eşleşen CI için **bulgu** açar, eşleşmeyen bulguyu kapatır, açık
-- bulgunun sorumlusuna tek görev açar. Bulgu kapanınca görev kendiliğinden kapanır; sorumlu "geçerli değil" derse
-- bulgu süreli istisna olur (tetikleyiciler; görev çalışanına karar yetkisi verilmez).
BEGIN;

CREATE TABLE IF NOT EXISTS insight.quality_rule (
  sys_id            meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name              text NOT NULL UNIQUE CHECK (length(name) BETWEEN 3 AND 120),
  description       text NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
  check_kind        text NOT NULL CHECK (check_kind IN ('no_relation', 'missing_fields', 'not_seen_by_role',
                                                         'matches_condition', 'relation_not_allowed')),
  params            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(params) = 'object' AND length(params::text) <= 4000),
  scope_class       text NOT NULL CHECK (scope_class ~ '^[a-z][a-z0-9_]{1,79}$'),
  scope_query       text NOT NULL DEFAULT '' CHECK (length(scope_query) <= 2000),
  dimension         text NOT NULL CHECK (dimension IN ('completeness', 'correctness', 'compliance', 'coverage')),
  severity          text NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  open_task         boolean NOT NULL DEFAULT true,
  task_title        text NOT NULL CHECK (length(task_title) BETWEEN 3 AND 120),
  task_instructions text NOT NULL DEFAULT '' CHECK (length(task_instructions) <= 4000),
  due_days          integer NOT NULL DEFAULT 30 CHECK (due_days BETWEEN 1 AND 180),
  channel           text REFERENCES gov.task_channel(name),
  exception_days    integer NOT NULL DEFAULT 180 CHECK (exception_days BETWEEN 1 AND 730),
  max_findings      integer NOT NULL DEFAULT 5000 CHECK (max_findings BETWEEN 1 AND 50000),
  interval_hours    integer NOT NULL DEFAULT 24 CHECK (interval_hours IN (1, 6, 24, 168)),
  state             text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'active', 'paused', 'retired')),
  next_run_at       timestamptz,
  last_run_at       timestamptz,
  created_by        text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        text,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (state <> 'active' OR next_run_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS quality_rule_due ON insight.quality_rule (next_run_at) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS insight.quality_rule_run (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rule          meta.sysid NOT NULL REFERENCES insight.quality_rule(sys_id),
  trigger       text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
  requested_by  text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running'
                CHECK (status IN ('requested', 'running', 'done', 'failed', 'inconclusive')),
  in_scope      integer,
  matched       integer,
  opened        integer,
  resolved      integer,
  tasks_opened  integer,
  note          text CHECK (length(note) <= 500)
);
CREATE INDEX IF NOT EXISTS quality_rule_run_rule ON insight.quality_rule_run (rule, started_at DESC);

-- Bulgu: kural × CI. Açık veya istisnalı bulgu CI başına tektir; kapanan bulgu geçmiş olarak kalır, tekrar ederse
-- yeni bulgu açılır.
CREATE TABLE IF NOT EXISTS insight.finding (
  sys_id           meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  rule             meta.sysid NOT NULL REFERENCES insight.quality_rule(sys_id),
  ci_sys_id        meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  state            text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'excepted', 'resolved')),
  details          jsonb NOT NULL DEFAULT '{}' CHECK (length(details::text) <= 8000),
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  resolved_at      timestamptz,
  triage           boolean NOT NULL DEFAULT false,      -- sorumlu görevi reddetti: sorumlu değişene kadar görev yok
  exception_until  timestamptz,
  exception_by     text,
  exception_note   text CHECK (length(exception_note) <= 2000),
  CHECK (state <> 'resolved' OR resolved_at IS NOT NULL),
  CHECK (state <> 'excepted' OR (exception_until IS NOT NULL AND exception_by IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS finding_current ON insight.finding (rule, ci_sys_id) WHERE state <> 'resolved';
CREATE INDEX IF NOT EXISTS finding_ci ON insight.finding (ci_sys_id) WHERE state <> 'resolved';
CREATE INDEX IF NOT EXISTS finding_rule_state ON insight.finding (rule, state);

-- İstisna yalnızca geçerli bir süreyle ve kim verdiğiyle; kapanan bulgu yeniden açılmaz (tekrar eden yeni bulgudur)
CREATE OR REPLACE FUNCTION insight.tg_finding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.state = 'resolved' AND NEW.state <> 'resolved' THEN
    RAISE EXCEPTION 'Kapanan bulgu yeniden açılmaz' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.state = 'excepted' AND OLD.state <> 'excepted' AND NEW.exception_until <= now() THEN
    RAISE EXCEPTION 'İstisna süresi gelecekte olmalı' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.sys_id <> OLD.sys_id OR NEW.rule <> OLD.rule OR NEW.ci_sys_id <> OLD.ci_sys_id
     OR NEW.first_seen_at <> OLD.first_seen_at THEN
    RAISE EXCEPTION 'Bulgunun kimliği değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS finding_guard ON insight.finding;
CREATE TRIGGER finding_guard BEFORE UPDATE ON insight.finding FOR EACH ROW EXECUTE FUNCTION insight.tg_finding_guard();

-- Bulgu kapanınca açık görevi "Bulgu giderildi" ile kapanır
ALTER TABLE gov.task DROP CONSTRAINT IF EXISTS task_outcome_check;
ALTER TABLE gov.task ADD CONSTRAINT task_outcome_check
  CHECK (outcome IN ('confirmed', 'corrected', 'not_applicable', 'rejected', 'cancelled', 'resolved'));

CREATE OR REPLACE FUNCTION insight.tg_finding_closes_task() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  r record;
BEGIN
  IF NEW.state = 'resolved' AND OLD.state <> 'resolved' THEN
    FOR r IN
      UPDATE gov.task t SET state = 'done', outcome = 'resolved', closed_by = 'evren:kural', closed_at = now(),
                            outcome_note = 'Bulgu giderildi: kural artık bu CI için bulgu üretmiyor.'
       WHERE t.source = 'rule' AND t.source_ref = NEW.sys_id AND t.state = 'open'
       RETURNING t.sys_id
    LOOP
      INSERT INTO gov.task_event (task, actor, action, detail)
      VALUES (r.sys_id, 'evren:kural', 'completed', jsonb_build_object('outcome', 'resolved', 'via', 'finding'));
      PERFORM pg_notify('evren_task', r.sys_id);
    END LOOP;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION insight.tg_finding_closes_task() FROM PUBLIC;
DROP TRIGGER IF EXISTS finding_closes_task ON insight.finding;
CREATE TRIGGER finding_closes_task AFTER UPDATE OF state ON insight.finding
  FOR EACH ROW EXECUTE FUNCTION insight.tg_finding_closes_task();

-- Görev kararı bulguya döner: "geçerli değil" → süreli istisna; "reddedildi" (sorumlu ben değilim) → ayıklama
CREATE OR REPLACE FUNCTION gov.tg_task_decides_finding() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  v_days integer;
BEGIN
  IF NEW.source <> 'rule' OR NEW.source_ref IS NULL OR OLD.state <> 'open' OR NEW.state <> 'done' THEN
    RETURN NEW;
  END IF;
  IF NEW.outcome = 'not_applicable' THEN
    SELECT q.exception_days INTO v_days FROM insight.finding f JOIN insight.quality_rule q ON q.sys_id = f.rule
     WHERE f.sys_id = NEW.source_ref;
    UPDATE insight.finding SET state = 'excepted', exception_until = now() + make_interval(days => coalesce(v_days, 180)),
                               exception_by = NEW.closed_by, exception_note = NEW.outcome_note, triage = false
     WHERE sys_id = NEW.source_ref AND state = 'open';
  ELSIF NEW.outcome = 'rejected' THEN
    UPDATE insight.finding SET triage = true WHERE sys_id = NEW.source_ref AND state = 'open';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION gov.tg_task_decides_finding() FROM PUBLIC;
DROP TRIGGER IF EXISTS task_decides_finding ON gov.task;
CREATE TRIGGER task_decides_finding AFTER UPDATE OF state ON gov.task
  FOR EACH ROW EXECUTE FUNCTION gov.tg_task_decides_finding();

-- CMDB sağlık ölçümü: görev çalışanı saatte bir yazar; pano son ölçümü ve günlük eğilimi gösterir
CREATE TABLE IF NOT EXISTS insight.health_snapshot (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  taken_at      timestamptz NOT NULL DEFAULT now(),
  overall       numeric(4, 1),
  completeness  numeric(4, 1),
  correctness   numeric(4, 1),
  compliance    numeric(4, 1),
  coverage      numeric(4, 1),
  details       jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS health_snapshot_at ON insight.health_snapshot (taken_at DESC);

-- ---------------------------------------------------------------- yetkiler
GRANT SELECT ON insight.quality_rule, insight.quality_rule_run, insight.finding, insight.health_snapshot
  TO evren_app_api, evren_app_governance, evren_auditor_ro;
-- API: kural tanımı ve durumu; "şimdi çalıştır" isteği; Konfigürasyon yöneticisinin istisnası ve geri alması
GRANT INSERT (name, description, check_kind, params, scope_class, scope_query, dimension, severity, open_task,
              task_title, task_instructions, due_days, channel, exception_days, max_findings, interval_hours, state,
              next_run_at, created_by) ON insight.quality_rule TO evren_app_api;
GRANT UPDATE (name, description, check_kind, params, scope_class, scope_query, dimension, severity, open_task,
              task_title, task_instructions, due_days, channel, exception_days, max_findings, interval_hours, state,
              next_run_at, updated_by, updated_at) ON insight.quality_rule TO evren_app_api;
GRANT INSERT (rule, trigger, requested_by, status) ON insight.quality_rule_run TO evren_app_api;
GRANT UPDATE (state, exception_until, exception_by, exception_note, triage) ON insight.finding TO evren_app_api;
-- Görev çalışanı: kuralı değerlendirir, bulgu açar/kapatır, sağlığı ölçer (istisna veremez, kural tanımlayamaz)
GRANT UPDATE (next_run_at, last_run_at) ON insight.quality_rule TO evren_app_governance;
GRANT INSERT, UPDATE ON insight.quality_rule_run TO evren_app_governance;
GRANT INSERT (rule, ci_sys_id, details) ON insight.finding TO evren_app_governance;
GRANT UPDATE (state, details, last_seen_at, resolved_at, triage) ON insight.finding TO evren_app_governance;
GRANT INSERT (overall, completeness, correctness, compliance, coverage, details) ON insight.health_snapshot
  TO evren_app_governance;
-- Kaynak sağlığı (devre kesici) ve ölçüm sayaçları
GRANT SELECT (data_source, started_at, completed, breaker_state) ON ingest.source_run TO evren_app_governance;
GRANT SELECT (status) ON ingest.match_candidate TO evren_app_governance;
GRANT SELECT (ci_sys_id, status) ON insight.staleness_case TO evren_app_governance;
GRANT SELECT ON cmdb.cmdb_rel_type TO evren_app_governance;

COMMIT;
