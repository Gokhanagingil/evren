-- 0035 — Gözlemlenebilirlik ve bildirimler: platform uyarıları, bildirim merkezi, teslim kanalları (U8.9, ADR-0040;
-- F1-15).
--
-- * ops.alert: görev çalışanı platform sağlığını (servis nabzı, Kafka, olay yayını, devre kesici, çalıştırma istekleri,
--   denetim mühürleri) kurallarla izler (metadata/alerts.yaml). Koşul "şu kadar süredir" sürünce uyarı ateşlenir,
--   koşul kalkınca çözülür. Platform operatörü gerekçeyle onaylar. Uyarının durum değişimi olay omurgasına gider.
-- * notify.*: bildirim merkezi — kişiye, gruba veya role adreslenen bildirim (uygulama içi her zaman; e-posta ve Teams
--   yönlendirmeyle). Görev atama, hatırlatma, süre aşımı ve tırmanma, uyarılar. İleride süreç uygulaması da kullanır.
--   Sır (SMTP parolası, Teams adresi) veritabanında yoktur; çalışanın sır dosyasındadır.
BEGIN;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS notify;

-- ---------------------------------------------------------------- uyarılar
CREATE TABLE IF NOT EXISTS ops.alert (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  rule           text NOT NULL CHECK (rule ~ '^[a-z][a-z0-9_]{2,60}$'),
  subject        text NOT NULL DEFAULT '' CHECK (length(subject) <= 120),
  severity       text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
  state          text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'firing', 'resolved')),
  title          text NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
  detail         text NOT NULL DEFAULT '' CHECK (length(detail) <= 1000),
  value          numeric,
  audience       text[] NOT NULL DEFAULT '{}',
  pending_since  timestamptz NOT NULL DEFAULT now(),
  fired_at       timestamptz,
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  resolved_at    timestamptz,
  acked_by       text,
  acked_at       timestamptz,
  ack_note       text CHECK (length(ack_note) BETWEEN 10 AND 1000),
  CHECK (state <> 'firing' OR fired_at IS NOT NULL),
  CHECK (state <> 'resolved' OR resolved_at IS NOT NULL),
  CHECK ((acked_by IS NULL) = (acked_at IS NULL) AND (acked_by IS NULL) = (ack_note IS NULL))
);
-- Kural ve konu başına tek açık uyarı
CREATE UNIQUE INDEX IF NOT EXISTS alert_open ON ops.alert (rule, subject) WHERE state <> 'resolved';
CREATE INDEX IF NOT EXISTS alert_recent ON ops.alert (coalesce(fired_at, pending_since) DESC);

-- Çözülen uyarı değişmez; onay yalnızca ateşlenmiş veya çözülmüş (ateşlenmiş) uyarıya ve bir kez verilir
CREATE OR REPLACE FUNCTION ops.tg_alert_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Uyarı silinemez (geçmiş)' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.state = 'resolved' AND (NEW.state, NEW.last_seen_at, NEW.resolved_at, NEW.detail, NEW.value)
       IS DISTINCT FROM (OLD.state, OLD.last_seen_at, OLD.resolved_at, OLD.detail, OLD.value) THEN
    RAISE EXCEPTION 'Çözülen uyarı değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.state = 'firing' AND NEW.state = 'pending' THEN
    RAISE EXCEPTION 'Ateşlenen uyarı bekleyene dönemez' USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.acked_by IS NOT NULL AND (NEW.acked_by, NEW.acked_at, NEW.ack_note)
       IS DISTINCT FROM (OLD.acked_by, OLD.acked_at, OLD.ack_note) THEN
    RAISE EXCEPTION 'Uyarı zaten onaylandı' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.acked_by IS NOT NULL AND OLD.acked_by IS NULL AND NEW.fired_at IS NULL THEN
    RAISE EXCEPTION 'Yalnızca ateşlenmiş uyarı onaylanır' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS alert_guard ON ops.alert;
CREATE TRIGGER alert_guard BEFORE UPDATE OR DELETE ON ops.alert FOR EACH ROW EXECUTE FUNCTION ops.tg_alert_guard();

-- Ateşlenme, çözülme ve onay olay omurgasına (izleme araçları, SIEM)
CREATE OR REPLACE FUNCTION ops.tg_alert_outbox() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  v_action text;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.state = 'firing' AND OLD.state = 'pending' THEN v_action := 'fired';
  ELSIF TG_OP = 'UPDATE' AND NEW.state = 'resolved' AND OLD.state = 'firing' THEN v_action := 'resolved';
  ELSIF TG_OP = 'UPDATE' AND NEW.acked_by IS NOT NULL AND OLD.acked_by IS NULL THEN v_action := 'acked';
  ELSIF TG_OP = 'INSERT' AND NEW.state = 'firing' THEN v_action := 'fired';
  ELSE RETURN NEW;
  END IF;
  INSERT INTO events.outbox (aggregate_type, aggregate_id, event_type, payload)
  VALUES ('ops', NEW.sys_id, 'ops.alert.' || v_action,
          jsonb_build_object('sys_id', NEW.sys_id, 'rule', NEW.rule, 'subject', NEW.subject, 'severity', NEW.severity,
                             'state', NEW.state, 'title', NEW.title, 'detail', NEW.detail, 'value', NEW.value,
                             'fired_at', NEW.fired_at, 'resolved_at', NEW.resolved_at, 'acked_by', NEW.acked_by));
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION ops.tg_alert_outbox() FROM PUBLIC;
DROP TRIGGER IF EXISTS alert_outbox ON ops.alert;
CREATE TRIGGER alert_outbox AFTER INSERT OR UPDATE ON ops.alert FOR EACH ROW EXECUTE FUNCTION ops.tg_alert_outbox();

-- ---------------------------------------------------------------- bildirimler
CREATE TABLE IF NOT EXISTS notify.notification (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  seq            bigint GENERATED ALWAYS AS IDENTITY UNIQUE,   -- teslim planlamasının sırası
  created_at     timestamptz NOT NULL DEFAULT now(),
  kind           text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]{2,40}$'),
  severity       text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'critical')),
  audience_kind  text NOT NULL CHECK (audience_kind IN ('user', 'group', 'role')),
  audience       text NOT NULL CHECK (length(audience) BETWEEN 1 AND 80),
  title          text NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
  body           text NOT NULL DEFAULT '' CHECK (length(body) <= 2000),
  -- Yalnızca uygulama içi bağlantı (dış adrese yönlendirme yok)
  link           text CHECK (link ~ '^#/[A-Za-z0-9/_=.%-]{0,200}$'),
  source         text NOT NULL CHECK (length(source) <= 120),
  -- Aynı olay için ikinci bildirim üretilmez (görev olayı, uyarı geçişi)
  dedup_key      text NOT NULL UNIQUE CHECK (length(dedup_key) <= 200),
  created_by     text NOT NULL DEFAULT 'evren:bildirim'
);
CREATE INDEX IF NOT EXISTS notification_audience ON notify.notification (audience_kind, audience, created_at DESC);

-- Okundu bilgisi kişi başına (rol ve grup bildirimini her alıcı ayrı okur); salt ekleme
CREATE TABLE IF NOT EXISTS notify.receipt (
  notification  meta.sysid NOT NULL REFERENCES notify.notification(sys_id),
  principal     text NOT NULL CHECK (length(principal) BETWEEN 1 AND 200),
  read_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (notification, principal)
);

-- Rol veya grup için dış kanal hedefi (e-posta adresi ya da Teams hedef adı; Teams adresi çalışanın sır dosyasında)
CREATE TABLE IF NOT EXISTS notify.route (
  audience_kind  text NOT NULL CHECK (audience_kind IN ('group', 'role')),
  audience       text NOT NULL CHECK (length(audience) BETWEEN 1 AND 80),
  channel        text NOT NULL CHECK (channel IN ('email', 'teams')),
  target         text NOT NULL CHECK (length(target) BETWEEN 3 AND 254),
  min_severity   text NOT NULL DEFAULT 'info' CHECK (min_severity IN ('info', 'warning', 'critical')),
  active         boolean NOT NULL DEFAULT true,
  updated_by     text NOT NULL,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (audience_kind, audience, channel, target),
  CHECK (channel <> 'email' OR target ~ '^[^@\s<>"'',;]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'),
  CHECK (channel <> 'teams' OR target ~ '^[a-z][a-z0-9_-]{1,40}$')
);

CREATE TABLE IF NOT EXISTS notify.delivery (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  notification  meta.sysid NOT NULL REFERENCES notify.notification(sys_id),
  channel       text NOT NULL CHECK (channel IN ('email', 'teams')),
  target        text NOT NULL CHECK (length(target) BETWEEN 3 AND 254),
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
  attempts      integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error    text CHECK (length(last_error) <= 300),
  next_try_at   timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now(),
  sent_at       timestamptz,
  UNIQUE (notification, channel, target)
);
CREATE INDEX IF NOT EXISTS delivery_due ON notify.delivery (next_try_at) WHERE status = 'pending';

-- Bildirim üreticisinin kaldığı yer (akış başına son işlenen kimlik)
CREATE TABLE IF NOT EXISTS notify.cursor (
  stream      text PRIMARY KEY CHECK (stream ~ '^[a-z_]{3,40}$'),
  last_id     bigint NOT NULL DEFAULT 0,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
-- İlk kurulumda geçmiş görev olayları toplu bildirime dönmez: üretici bugünden başlar
INSERT INTO notify.cursor (stream, last_id)
SELECT 'task_event', coalesce(max(id), 0) FROM gov.task_event
ON CONFLICT (stream) DO NOTHING;
INSERT INTO notify.cursor (stream, last_id) VALUES ('delivery_plan', 0) ON CONFLICT (stream) DO NOTHING;

-- API yalnızca "sınama" bildirimi ekleyebilir (kanal kurulumunu doğrulamak için); diğerlerini görev çalışanı üretir
CREATE OR REPLACE FUNCTION notify.tg_notification_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Bildirim değiştirilemez' USING ERRCODE = 'check_violation';
  END IF;
  IF current_user = 'evren_app_api' AND NEW.kind <> 'test' THEN
    RAISE EXCEPTION 'API yalnızca sınama bildirimi ekler' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS notification_guard ON notify.notification;
CREATE TRIGGER notification_guard BEFORE INSERT OR UPDATE OR DELETE ON notify.notification
  FOR EACH ROW EXECUTE FUNCTION notify.tg_notification_guard();
DROP TRIGGER IF EXISTS receipt_append_only ON notify.receipt;
CREATE TRIGGER receipt_append_only BEFORE UPDATE OR DELETE ON notify.receipt
  FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only();

-- ---------------------------------------------------------------- yetkiler
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest', 'retire',
  'task_admin', 'audit_review', 'ops_admin'));
INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT r, 'platform', 'ops_admin', NULL, 'internal', NULL,
       'Platform uyarısını onaylar, bildirim yönlendirmesini düzenler, sınama bildirimi gönderir (ADR-0040)'
  FROM unnest(ARRAY['evren_operator', 'evren_admin']) AS r
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = r AND p.operation = 'ops_admin');

GRANT USAGE ON SCHEMA ops, notify TO evren_app_api, evren_app_governance, evren_auditor_ro;
-- Görev çalışanı: uyarıyı açar, ateşler, çözer (onaylayamaz); bildirim üretir ve teslim eder
GRANT SELECT, INSERT (rule, subject, severity, state, title, detail, value, audience, pending_since, fired_at,
                      last_seen_at, resolved_at) ON ops.alert TO evren_app_governance;
GRANT UPDATE (severity, state, title, detail, value, fired_at, last_seen_at, resolved_at) ON ops.alert
  TO evren_app_governance;
GRANT SELECT, INSERT (kind, severity, audience_kind, audience, title, body, link, source, dedup_key, created_by)
  ON notify.notification TO evren_app_governance;
GRANT SELECT ON notify.route TO evren_app_governance;
GRANT SELECT, INSERT (notification, channel, target, status, attempts, last_error, next_try_at, sent_at),
      UPDATE (status, attempts, last_error, next_try_at, sent_at) ON notify.delivery TO evren_app_governance;
GRANT SELECT, INSERT, UPDATE ON notify.cursor TO evren_app_governance;
-- Sağlık izlemesi için okuma (yalnızca gereken sütunlar)
GRANT USAGE ON SCHEMA events TO evren_app_governance;
GRANT SELECT (id, created_at, published_at) ON events.outbox TO evren_app_governance;
GRANT SELECT (status, requested_at, scheduled_for, data_source) ON ingest.connector_run_request TO evren_app_governance;
GRANT SELECT (name, role, lifecycle) ON ingest.data_source TO evren_app_governance;
GRANT SELECT (data_source, started_at, finished_at, completed, breaker_state) ON ingest.source_run TO evren_app_governance;
GRANT SELECT (ok, checked_at) ON audit.seal_check TO evren_app_governance;
-- API: uyarıyı okur ve yalnızca onay sütunlarını yazar; bildirimleri okur, okundu ekler, sınama bildirimi ekler,
-- yönlendirmeyi yönetir; teslim durumunu okur
GRANT SELECT ON ops.alert TO evren_app_api, evren_auditor_ro;
GRANT UPDATE (acked_by, acked_at, ack_note) ON ops.alert TO evren_app_api;
GRANT SELECT ON notify.notification, notify.receipt, notify.route, notify.delivery TO evren_app_api, evren_auditor_ro;
GRANT INSERT (kind, severity, audience_kind, audience, title, body, link, source, dedup_key, created_by)
  ON notify.notification TO evren_app_api;
GRANT INSERT (notification, principal) ON notify.receipt TO evren_app_api;
GRANT INSERT, UPDATE (min_severity, active, updated_by, updated_at) ON notify.route TO evren_app_api;

COMMIT;
