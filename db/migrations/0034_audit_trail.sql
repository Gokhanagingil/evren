-- 0034 — Denetim izi: birleşik görünüm, kaynak adresi ve istek kimliği, değiştirilemezlik mühürleri, inceleme kanıtı,
-- kurum SIEM'ine aktarım (U8.8, ADR-0039; F1-16).
--
-- * audit.security_event olayına kaynak IP ve istek kimliği eklenir: API her istekte oturum ayarına yazar, tetikleyici
--   olaya işler (her ekleme noktasını değiştirmeden).
-- * Her güvenlik olayı ve her mühür aynı işlemde events.outbox'a yazılır → olay omurgası → evren.audit.v1 → SIEM.
-- * audit.seal: akış (güvenlik, CI değişikliği, görev) başına saatlik özet ve zincir; görev çalışanı mühürler.
-- * audit.seal_check ve audit.review: doğrulama ve inceleme kanıtı. Hepsi salt eklemedir.
-- * audit.trail: üç akışın birleşik görünümü (ileride süreç uygulamasının olaylarıyla genişler).
BEGIN;

-- ---------------------------------------------------------------- olay bağlamı
ALTER TABLE audit.security_event ADD COLUMN IF NOT EXISTS client_ip inet;
ALTER TABLE audit.security_event ADD COLUMN IF NOT EXISTS request_id text CHECK (request_id ~ '^[0-9a-f]{32}$');
CREATE INDEX IF NOT EXISTS security_event_principal ON audit.security_event (principal, at DESC);
CREATE INDEX IF NOT EXISTS security_event_action ON audit.security_event (action, at DESC);
CREATE INDEX IF NOT EXISTS security_event_request ON audit.security_event (request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS task_event_at ON gov.task_event (at DESC);

CREATE OR REPLACE FUNCTION audit.tg_security_event_context() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  v_ip text := nullif(current_setting('evren.client_ip', true), '');
  v_rid text := nullif(current_setting('evren.request_id', true), '');
BEGIN
  IF NEW.client_ip IS NULL AND v_ip IS NOT NULL THEN
    BEGIN
      NEW.client_ip := v_ip::inet;
    EXCEPTION WHEN invalid_text_representation THEN
      NEW.client_ip := NULL;  -- geçersiz değer olayı engellemez
    END;
  END IF;
  IF NEW.request_id IS NULL AND v_rid ~ '^[0-9a-f]{32}$' THEN
    NEW.request_id := v_rid;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS security_event_context ON audit.security_event;
CREATE TRIGGER security_event_context BEFORE INSERT ON audit.security_event
  FOR EACH ROW EXECUTE FUNCTION audit.tg_security_event_context();

-- ---------------------------------------------------------------- SIEM: olay omurgasına (outbox → evren.audit.v1)
CREATE OR REPLACE FUNCTION audit.tg_security_event_outbox() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload)
  VALUES ('audit', NEW.id::text, 'audit.security_event',
          jsonb_build_object('id', NEW.id, 'at', NEW.at, 'principal', NEW.principal, 'action', NEW.action,
                             'resource', NEW.resource, 'decision', NEW.decision, 'client_ip', host(NEW.client_ip),
                             'request_id', NEW.request_id, 'detail', NEW.detail));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION audit.tg_security_event_outbox() FROM PUBLIC;
DROP TRIGGER IF EXISTS security_event_outbox ON audit.security_event;
CREATE TRIGGER security_event_outbox AFTER INSERT ON audit.security_event
  FOR EACH ROW EXECUTE FUNCTION audit.tg_security_event_outbox();

-- ---------------------------------------------------------------- mühürler
CREATE TABLE IF NOT EXISTS audit.seal (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  stream        text NOT NULL CHECK (stream IN ('security', 'ci', 'task')),
  period_start  timestamptz NOT NULL,
  period_end    timestamptz NOT NULL CHECK (period_end > period_start),
  row_count     integer NOT NULL CHECK (row_count >= 0),
  digest        text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
  prev_chain    text NOT NULL CHECK (prev_chain ~ '^[0-9a-f]{64}$'),
  chain         text NOT NULL CHECK (chain ~ '^[0-9a-f]{64}$'),
  sealed_at     timestamptz NOT NULL DEFAULT now(),
  sealed_by     text NOT NULL,
  UNIQUE (stream, period_start)
);
CREATE INDEX IF NOT EXISTS seal_stream ON audit.seal (stream, period_start DESC);

CREATE TABLE IF NOT EXISTS audit.seal_check (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  seal        bigint NOT NULL REFERENCES audit.seal(id),
  checked_at  timestamptz NOT NULL DEFAULT now(),
  checked_by  text NOT NULL,
  ok          boolean NOT NULL,
  problem     text CHECK (length(problem) <= 300)
);
CREATE INDEX IF NOT EXISTS seal_check_seal ON audit.seal_check (seal, checked_at DESC);
CREATE INDEX IF NOT EXISTS seal_check_failed ON audit.seal_check (checked_at DESC) WHERE NOT ok;

CREATE TABLE IF NOT EXISTS audit.review (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  reviewed_at    timestamptz NOT NULL DEFAULT now(),
  reviewer       text NOT NULL,
  period_start   timestamptz NOT NULL,
  period_end     timestamptz NOT NULL CHECK (period_end > period_start),
  events         integer NOT NULL CHECK (events >= 0),
  denied         integer NOT NULL CHECK (denied >= 0),
  seals_checked  integer NOT NULL CHECK (seals_checked >= 0),
  seals_ok       boolean NOT NULL,
  outcome        text NOT NULL CHECK (outcome IN ('no_findings', 'findings')),
  note           text NOT NULL CHECK (length(note) BETWEEN 10 AND 4000)
);
CREATE INDEX IF NOT EXISTS review_at ON audit.review (reviewed_at DESC);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['seal', 'seal_check', 'review'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %1$s_append_only ON audit.%1$s', t);
    EXECUTE format('CREATE TRIGGER %1$s_append_only BEFORE UPDATE OR DELETE ON audit.%1$s '
                   'FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only()', t);
    EXECUTE format('DROP TRIGGER IF EXISTS %1$s_no_truncate ON audit.%1$s', t);
    EXECUTE format('CREATE TRIGGER %1$s_no_truncate BEFORE TRUNCATE ON audit.%1$s '
                   'FOR EACH STATEMENT EXECUTE FUNCTION audit.tg_append_only()', t);
  END LOOP;
END $$;

-- Mühür zincirde bir öncekine bağlanır: ardışık dönem ve önceki zincir değeri (yarış ve atlama önlenir)
CREATE OR REPLACE FUNCTION audit.tg_seal_link() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
  prev record;
BEGIN
  SELECT period_end, chain INTO prev FROM audit.seal WHERE stream = NEW.stream ORDER BY period_start DESC LIMIT 1;
  IF FOUND AND (NEW.period_start <> prev.period_end OR NEW.prev_chain <> prev.chain) THEN
    RAISE EXCEPTION 'Mühür zincire bağlanmıyor (%)', NEW.stream USING ERRCODE = 'check_violation';
  END IF;
  IF NOT FOUND AND NEW.prev_chain <> repeat('0', 64) THEN
    RAISE EXCEPTION 'İlk mühür başlangıç değeriyle başlar' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS seal_link ON audit.seal;
CREATE TRIGGER seal_link BEFORE INSERT ON audit.seal FOR EACH ROW EXECUTE FUNCTION audit.tg_seal_link();

CREATE OR REPLACE FUNCTION audit.tg_seal_outbox() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload)
  VALUES ('audit', 'seal:' || NEW.id, 'audit.seal',
          jsonb_build_object('id', NEW.id, 'stream', NEW.stream, 'period_start', NEW.period_start,
                             'period_end', NEW.period_end, 'row_count', NEW.row_count, 'digest', NEW.digest,
                             'prev_chain', NEW.prev_chain, 'chain', NEW.chain, 'sealed_at', NEW.sealed_at));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION audit.tg_seal_outbox() FROM PUBLIC;
DROP TRIGGER IF EXISTS seal_outbox ON audit.seal;
CREATE TRIGGER seal_outbox AFTER INSERT ON audit.seal FOR EACH ROW EXECUTE FUNCTION audit.tg_seal_outbox();

-- Doğrulama sonucu da SIEM'e gider: bozulmuş bir mühür kurum güvenlik izlemesinde alarm olur
CREATE OR REPLACE FUNCTION audit.tg_seal_check_outbox() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
  INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload)
  VALUES ('audit', 'seal_check:' || NEW.id, 'audit.seal_check',
          jsonb_build_object('id', NEW.id, 'seal', NEW.seal, 'checked_at', NEW.checked_at, 'checked_by', NEW.checked_by,
                             'ok', NEW.ok, 'problem', NEW.problem));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION audit.tg_seal_check_outbox() FROM PUBLIC;
DROP TRIGGER IF EXISTS seal_check_outbox ON audit.seal_check;
CREATE TRIGGER seal_check_outbox AFTER INSERT ON audit.seal_check
  FOR EACH ROW EXECUTE FUNCTION audit.tg_seal_check_outbox();

-- ---------------------------------------------------------------- birleşik görünüm
CREATE OR REPLACE VIEW audit.trail AS
SELECT 'security'::text AS source, e.id, e.at, e.principal AS actor, e.action, e.resource AS object,
       e.decision AS outcome, e.detail, host(e.client_ip) AS client_ip, e.request_id
  FROM audit.security_event e
UNION ALL
SELECT 'ci', c.id, c.changed_at, c.actor, 'ci_attribute_' || c.reason, 'ci:' || c.ci_sys_id, 'allow',
       jsonb_build_object('attribute', c.attribute, 'old', c.old_value, 'new', c.new_value, 'data_source', c.data_source,
                          'class', c.sys_class_name, 'correlation', c.correlation),
       NULL, NULL
  FROM audit.ci_change c
UNION ALL
SELECT 'task', t.id, t.at, t.actor, 'task_' || t.action, 'task:' || t.task, 'allow', t.detail, NULL, NULL
  FROM gov.task_event t;
COMMENT ON VIEW audit.trail IS 'Denetim izi: güvenlik/yönetim olayları, CI değişiklikleri ve görev olayları (ADR-0039)';

-- ---------------------------------------------------------------- yetkiler
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest', 'retire',
  'task_admin', 'audit_review'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT 'evren_auditor', 'audit', 'audit_review', NULL, 'internal', NULL,
       'Denetim izi incelemesini ve mühür doğrulamasını kayda geçirir (ADR-0039)'
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission WHERE role = 'evren_auditor' AND operation = 'audit_review');

GRANT SELECT ON audit.trail, audit.seal, audit.seal_check, audit.review TO evren_app_api, evren_auditor_ro;
GRANT INSERT (seal, checked_by, ok, problem) ON audit.seal_check TO evren_app_api;
GRANT INSERT (reviewer, period_start, period_end, events, denied, seals_checked, seals_ok, outcome, note)
  ON audit.review TO evren_app_api;
-- Görev çalışanı mühürler: akışları okur, mühür ekler (değiştiremez, silemez)
GRANT USAGE ON SCHEMA audit TO evren_app_governance;
GRANT SELECT ON audit.security_event, audit.ci_change, audit.seal TO evren_app_governance;
GRANT INSERT (stream, period_start, period_end, row_count, digest, prev_chain, chain, sealed_by) ON audit.seal
  TO evren_app_governance;
-- ... ve günde bir son mühürleri kendiliğinden doğrular (kanıt: audit.seal_check)
GRANT SELECT, INSERT (seal, checked_by, ok, problem) ON audit.seal_check TO evren_app_governance;

COMMIT;
