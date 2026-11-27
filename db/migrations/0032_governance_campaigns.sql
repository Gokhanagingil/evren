-- 0032 — Zamanlanmış yönetişim kampanyaları (U8.5, ADR-0036, F1-25).
--
-- Konfigürasyon yöneticisi panelden kampanya tanımlar: tür (veri güncelliği teyidi, erişim gözden geçirme, kontrol
-- listesi), kapsam (sınıf + encoded query koşulu), sıklık (5 alanlı cron, İstanbul saati), süre. Görev çalışanı dönem
-- gelince kapsamdaki her CI için sorumlusuna görev açar (ADR-0035 altyapısı). Aynı CI için açık görev varsa yenisi
-- açılmaz (tekillik anahtarı ``campaign:<kampanya>:<ci>``).
--
-- Veri güncelliği teyidi sahiplik onayına bağlıdır: CI'ın sahiplik onayı verilince veya reddedilince açık teyit
-- görevi kendiliğinden kapanır (tetikleyici; görev çalışanına karar yetkisi verilmez).
BEGIN;

CREATE TABLE IF NOT EXISTS gov.campaign (
  sys_id        meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name          text NOT NULL UNIQUE CHECK (length(name) BETWEEN 3 AND 120),
  kind          text NOT NULL CHECK (kind IN ('attestation', 'access_review', 'checklist')),
  title         text NOT NULL CHECK (length(title) BETWEEN 3 AND 120),
  instructions  text NOT NULL DEFAULT '' CHECK (length(instructions) <= 4000),
  scope_class   text NOT NULL CHECK (scope_class ~ '^[a-z][a-z0-9_]{1,79}$'),
  scope_query   text NOT NULL DEFAULT '' CHECK (length(scope_query) <= 2000),
  schedule      text NOT NULL CHECK (length(schedule) BETWEEN 9 AND 100),
  schedule_tz   text NOT NULL DEFAULT 'Europe/Istanbul' CHECK (schedule_tz ~ '^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$'),
  due_days      integer NOT NULL DEFAULT 30 CHECK (due_days BETWEEN 1 AND 180),
  channel       text REFERENCES gov.task_channel(name),   -- NULL: görev açılırken varsayılan kanal
  max_tasks     integer NOT NULL DEFAULT 2000 CHECK (max_tasks BETWEEN 1 AND 20000),
  state         text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'active', 'paused', 'retired')),
  next_run_at   timestamptz,
  last_run_at   timestamptz,
  created_by    text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    text,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (state <> 'active' OR next_run_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS campaign_due ON gov.campaign (next_run_at) WHERE state = 'active';

CREATE TABLE IF NOT EXISTS gov.campaign_run (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  campaign      meta.sysid NOT NULL REFERENCES gov.campaign(sys_id),
  trigger       text NOT NULL CHECK (trigger IN ('schedule', 'manual')),
  requested_by  text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running' CHECK (status IN ('requested', 'running', 'done', 'failed')),
  in_scope      integer,
  created       integer,
  already_open  integer,
  unassigned    integer,
  note          text CHECK (length(note) <= 500)
);
CREATE INDEX IF NOT EXISTS campaign_run_campaign ON gov.campaign_run (campaign, started_at DESC);

-- Sahiplik onayı → veri güncelliği teyidi görevi kapanır (onay: Doğrulandı; ret: Reddedildi)
CREATE OR REPLACE FUNCTION gov.tg_attestation_closes_tasks() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  r record;
  v_outcome text;
BEGIN
  IF NEW.attestation_status IS NOT DISTINCT FROM OLD.attestation_status
     AND NEW.attested_date IS NOT DISTINCT FROM OLD.attested_date THEN
    RETURN NEW;
  END IF;
  IF NEW.attestation_status NOT IN ('Attested', 'Rejected') THEN
    RETURN NEW;
  END IF;
  v_outcome := CASE WHEN NEW.attestation_status = 'Attested' THEN 'confirmed' ELSE 'rejected' END;
  FOR r IN
    UPDATE gov.task t
       SET state = 'done', outcome = v_outcome, closed_by = 'evren:sahiplik-onayi', closed_at = now(),
           outcome_note = CASE WHEN v_outcome = 'confirmed' THEN 'Sahiplik onayı verildi.'
                               ELSE 'Sahiplik onayında reddedildi.' END
     WHERE t.ci_sys_id = NEW.sys_id AND t.kind = 'attestation' AND t.state = 'open'
       AND t.created_at <= coalesce(NEW.attested_date, now())
     RETURNING t.sys_id
  LOOP
    INSERT INTO gov.task_event (task, actor, action, detail)
    VALUES (r.sys_id, 'evren:sahiplik-onayi', 'completed', jsonb_build_object('outcome', v_outcome, 'via', 'attestation'));
    PERFORM pg_notify('evren_task', r.sys_id);
  END LOOP;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION gov.tg_attestation_closes_tasks() FROM PUBLIC;
DROP TRIGGER IF EXISTS attestation_closes_tasks ON cmdb.cmdb_ci;
CREATE TRIGGER attestation_closes_tasks AFTER UPDATE OF attestation_status, attested_date ON cmdb.cmdb_ci
  FOR EACH ROW EXECUTE FUNCTION gov.tg_attestation_closes_tasks();

-- Görev olayı türleri: hatırlatma ve tırmanma
ALTER TABLE gov.task_event DROP CONSTRAINT IF EXISTS task_event_action_check;
ALTER TABLE gov.task_event ADD CONSTRAINT task_event_action_check CHECK (action IN (
  'created', 'delivered', 'delivery_failed', 'kept_in_evren', 'synced', 'external_closed', 'completed', 'cancelled',
  'closed_sent', 'reassigned', 'reminded', 'overdue'));

-- ---------------------------------------------------------------- yetkiler
GRANT SELECT ON gov.campaign, gov.campaign_run TO evren_app_api, evren_app_governance, evren_auditor_ro;
-- API: kampanya tanımı ve durumu (taslak → etkin → duraklatıldı → emekli); "şimdi çalıştır" = next_run_at
GRANT INSERT (name, kind, title, instructions, scope_class, scope_query, schedule, schedule_tz, due_days, channel,
              max_tasks, state, next_run_at, created_by) ON gov.campaign TO evren_app_api;
GRANT UPDATE (name, kind, title, instructions, scope_class, scope_query, schedule, schedule_tz, due_days, channel,
              max_tasks, state, next_run_at, updated_by, updated_at) ON gov.campaign TO evren_app_api;
-- "Şimdi çalıştır": API istek satırı yazar (durum requested), görev çalışanı yürütür
GRANT INSERT (campaign, trigger, requested_by, status) ON gov.campaign_run TO evren_app_api;
-- Görev çalışanı: kampanyayı çalıştırır, görev açar (karar veremez)
GRANT UPDATE (next_run_at, last_run_at) ON gov.campaign TO evren_app_governance;
GRANT INSERT, UPDATE ON gov.campaign_run TO evren_app_governance;
GRANT INSERT (kind, title, description, ci_sys_id, assignee_user, assignee_group, assignee_basis, due_at, source,
              source_ref, dedup_key, channel, delivery_status, created_by) ON gov.task TO evren_app_governance;

COMMIT;
