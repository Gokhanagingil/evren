-- 0019 — Bağlayıcı çalıştırma istekleri, çalıştırıcı servisi ve arayüzden kimlik verme (U5.3b, ADR-0021).
--
-- * API çalıştırmaz, istek yazar (ingest.connector_run_request). Ayrı çalıştırıcı servisi (evren_app_connector) isteği
--   alır, bağlayıcıyı alt süreç olarak çalıştırır; kaynak verisi veritabanına yalnızca alım geçidinden gider.
-- * Çalıştırıcı yapılandırdığı bağlayıcı örneklerini sırsız özetle bildirir (ingest.connector_instance): adres,
--   kullanıcı adı ve dosya yolu yok. API yalnızca bildirilmiş örnek için istek kabul eder.
-- * Durum geçişleri tetikleyiciyle zorlanır; sonuçlanmış istek değişmez. Örnek başına tek etkin istek; zamanlanmış
--   dakika bir kez açılır.
-- * Kimlik verme/iptal SECURITY DEFINER işlevlerle: API düz belirteci veritabanına göndermez (yalnızca SHA-256 özeti),
--   kimlik tablosuna doğrudan yazamaz ve özet sütununu okuyamaz.
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evren_app_connector') THEN
    CREATE ROLE evren_app_connector NOLOGIN INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;

-- ---------------------------------------------------------------- bağlayıcı örnekleri
CREATE TABLE ingest.connector_instance (
  name                text PRIMARY KEY CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$'),
  connector           text NOT NULL CHECK (connector ~ '^[a-z][a-z0-9_]{1,40}$'),
  data_source         text NOT NULL REFERENCES ingest.data_source(name),
  schedule            text CHECK (length(schedule) BETWEEN 9 AND 100),
  schedule_mode       text NOT NULL DEFAULT 'snapshot' CHECK (schedule_mode IN ('snapshot', 'delta')),
  schedule_tz         text NOT NULL CHECK (schedule_tz ~ '^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$'),
  next_run_at         timestamptz,
  enabled             boolean NOT NULL DEFAULT true,
  max_runtime_minutes integer NOT NULL CHECK (max_runtime_minutes BETWEEN 1 AND 1440),
  runner_id           text NOT NULL CHECK (length(runner_id) BETWEEN 1 AND 200),
  first_seen_at       timestamptz NOT NULL DEFAULT now(),
  seen_at             timestamptz NOT NULL,
  CHECK (schedule IS NOT NULL OR next_run_at IS NULL)
);
COMMENT ON TABLE ingest.connector_instance IS
  'Çalıştırıcının bildirdiği bağlayıcı örnekleri (ADR-0021 §2). Sırsız özet: adres, kullanıcı ve dosya yolu yok.';

-- ---------------------------------------------------------------- çalıştırma istekleri
CREATE TABLE ingest.connector_run_request (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  instance            text NOT NULL REFERENCES ingest.connector_instance(name),
  data_source         text NOT NULL REFERENCES ingest.data_source(name),
  mode                text NOT NULL DEFAULT 'snapshot' CHECK (mode IN ('snapshot', 'delta')),
  since               timestamptz,
  origin              text NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'schedule')),
  scheduled_for       timestamptz,
  requested_by        text NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 200),
  requested_at        timestamptz NOT NULL DEFAULT now(),
  note                text CHECK (length(note) <= 500),
  status              text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'rejected')),
  claimed_by          text CHECK (length(claimed_by) BETWEEN 1 AND 200),
  started_at          timestamptz,
  finished_at         timestamptz,
  run_id              uuid UNIQUE,
  exit_code           integer CHECK (exit_code BETWEEN -255 AND 255),
  summary             jsonb NOT NULL DEFAULT '{}'::jsonb
                      CHECK (jsonb_typeof(summary) = 'object' AND pg_column_size(summary) <= 8192),
  result_note         text CHECK (length(result_note) <= 500),
  cancel_requested_at timestamptz,
  cancel_requested_by text CHECK (length(cancel_requested_by) BETWEEN 1 AND 200),
  CHECK ((origin = 'schedule') = (scheduled_for IS NOT NULL)),
  CHECK (mode = 'delta' OR since IS NULL),
  CHECK ((cancel_requested_at IS NULL) = (cancel_requested_by IS NULL)),
  CHECK ((status IN ('pending', 'running')) = (finished_at IS NULL)),
  CHECK (status = 'pending' OR status IN ('cancelled', 'rejected') OR (claimed_by IS NOT NULL AND run_id IS NOT NULL
                                                                        AND started_at IS NOT NULL))
);
-- Örnek başına aynı anda tek etkin istek; zamanlanmış dakika bir kez (birden çok çalıştırıcı örneği güvenli).
CREATE UNIQUE INDEX connector_run_request_active ON ingest.connector_run_request (instance)
  WHERE status IN ('pending', 'running');
CREATE UNIQUE INDEX connector_run_request_slot ON ingest.connector_run_request (instance, scheduled_for)
  WHERE origin = 'schedule';
CREATE INDEX connector_run_request_queue ON ingest.connector_run_request (requested_at) WHERE status = 'pending';
CREATE INDEX connector_run_request_recent ON ingest.connector_run_request (data_source, requested_at DESC);
COMMENT ON TABLE ingest.connector_run_request IS
  'Bağlayıcı çalıştırma istekleri (ADR-0021 §1): API ve zamanlayıcı yazar, çalıştırıcı yürütür; run_id = source_run.';

-- Ekleme: veri kaynağı örneğinkiyle aynı olmalı; istek "bekliyor" olarak doğar.
CREATE FUNCTION ingest.tg_connector_request_insert() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NEW.status <> 'pending' OR NEW.claimed_by IS NOT NULL OR NEW.run_id IS NOT NULL OR NEW.started_at IS NOT NULL
     OR NEW.cancel_requested_at IS NOT NULL THEN
    RAISE EXCEPTION 'çalıştırma isteği bekliyor durumunda açılır' USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM ingest.connector_instance i
                  WHERE i.name = NEW.instance AND i.data_source = NEW.data_source) THEN
    RAISE EXCEPTION 'bağlayıcı örneği bu veri kaynağına ait değil' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER connector_request_insert BEFORE INSERT ON ingest.connector_run_request
  FOR EACH ROW EXECUTE FUNCTION ingest.tg_connector_request_insert();

-- Güncelleme: geçerli geçişler; sonuçlanmış istek ve isteğin içeriği değişmez; iptal isteği bir kez yazılır.
CREATE FUNCTION ingest.tg_connector_request_transition() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF OLD.status NOT IN ('pending', 'running') THEN
    RAISE EXCEPTION 'sonuçlanmış çalıştırma isteği değiştirilemez' USING ERRCODE = '23514';
  END IF;
  IF (NEW.instance, NEW.data_source, NEW.mode, NEW.since, NEW.origin, NEW.scheduled_for, NEW.requested_by,
      NEW.requested_at, NEW.note)
     IS DISTINCT FROM (OLD.instance, OLD.data_source, OLD.mode, OLD.since, OLD.origin, OLD.scheduled_for,
                       OLD.requested_by, OLD.requested_at, OLD.note) THEN
    RAISE EXCEPTION 'çalıştırma isteğinin içeriği değiştirilemez' USING ERRCODE = '23514';
  END IF;
  IF OLD.cancel_requested_at IS NOT NULL AND (NEW.cancel_requested_at, NEW.cancel_requested_by)
     IS DISTINCT FROM (OLD.cancel_requested_at, OLD.cancel_requested_by) THEN
    RAISE EXCEPTION 'iptal isteği bir kez yazılır' USING ERRCODE = '23514';
  END IF;
  IF NOT ((OLD.status = NEW.status)
          OR (OLD.status = 'pending' AND NEW.status IN ('running', 'cancelled', 'rejected'))
          OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))) THEN
    RAISE EXCEPTION 'geçersiz durum geçişi: % → %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  IF OLD.status = 'running' AND (NEW.claimed_by, NEW.run_id, NEW.started_at)
     IS DISTINCT FROM (OLD.claimed_by, OLD.run_id, OLD.started_at) THEN
    RAISE EXCEPTION 'yürüyen isteğin çalıştırıcısı ve çalışması değiştirilemez' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER connector_request_transition BEFORE UPDATE ON ingest.connector_run_request
  FOR EACH ROW EXECUTE FUNCTION ingest.tg_connector_request_transition();

-- Yeni istek ve iptal isteği çalıştırıcıyı uyandırır (yük taşımaz; iş tablodan okunur).
CREATE FUNCTION ingest.notify_connector() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  PERFORM pg_notify('evren_connector', TG_OP);
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ingest.notify_connector() FROM PUBLIC;
CREATE TRIGGER connector_request_notify AFTER INSERT ON ingest.connector_run_request
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_connector();
CREATE TRIGGER connector_cancel_notify AFTER UPDATE OF cancel_requested_at ON ingest.connector_run_request
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_connector();

-- İptal: bekleyen istek hemen iptal; yürüyen isteğe iptal isteği yazılır (çalıştırıcı süreci sonlandırır).
-- API'nin istek tablosunda güncelleme yetkisi yoktur; yalnızca bu işlev. Dönen değer: 'cancelled', 'running'
-- (iptal istendi), 'final' (zaten sonuçlanmış) veya NULL (istek yok).
CREATE FUNCTION ingest.cancel_connector_run_request(p_id uuid, p_by text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_status text;
BEGIN
  IF p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'iptal eden kişi gerekli' USING ERRCODE = '22023';
  END IF;
  SELECT status INTO v_status FROM ingest.connector_run_request WHERE id = p_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF v_status = 'pending' THEN
    UPDATE ingest.connector_run_request
       SET status = 'cancelled', finished_at = now(), cancel_requested_at = now(), cancel_requested_by = p_by,
           result_note = 'Çalışmadan önce iptal edildi.'
     WHERE id = p_id;
    v_status := 'cancelled';
  ELSIF v_status = 'running' THEN
    UPDATE ingest.connector_run_request SET cancel_requested_at = now(), cancel_requested_by = p_by
     WHERE id = p_id AND cancel_requested_at IS NULL;
  ELSE
    RETURN 'final';  -- sonuçlanmış: değişiklik yok, denetim kaydı yok
  END IF;
  INSERT INTO audit.security_event (principal, action, resource, decision, detail)
  VALUES (p_by, 'connector_run_cancel', 'connector_run_request:' || p_id, 'allow', jsonb_build_object('status', v_status));
  RETURN v_status;
END $$;
REVOKE ALL ON FUNCTION ingest.cancel_connector_run_request(uuid, text) FROM PUBLIC;

-- ---------------------------------------------------------------- kimlik verme ve iptal (ADR-0021 §5)
-- Düz belirteç API'den çıkmaz: işleve yalnızca SHA-256 özeti ve son dört karakter gelir. Biçimleri tablo
-- kısıtları da denetler (0012). Emekli veya tanımsız veri kaynağına kimlik verilmez.
CREATE FUNCTION ingest.issue_connector_credential(p_name text, p_data_source text, p_sha256 text, p_hint text,
                                                  p_per_minute integer, p_days integer, p_by text)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_lifecycle text;
  v_expires   timestamptz;
BEGIN
  IF p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'veren kişi gerekli' USING ERRCODE = '22023';
  END IF;
  IF p_days IS NULL OR p_days NOT BETWEEN 1 AND 730 THEN
    RAISE EXCEPTION 'geçerlilik 1–730 gün olmalı' USING ERRCODE = '22023';
  END IF;
  IF p_sha256 IS NULL OR p_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'belirteç özeti geçersiz' USING ERRCODE = '22023';
  END IF;
  SELECT lifecycle INTO v_lifecycle FROM ingest.data_source WHERE name = p_data_source;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'veri kaynağı tanımsız' USING ERRCODE = '22023';
  END IF;
  IF v_lifecycle = 'retired' THEN
    RAISE EXCEPTION 'emekli veri kaynağına kimlik verilmez' USING ERRCODE = '22023';
  END IF;
  v_expires := now() + make_interval(days => p_days);
  INSERT INTO ingest.connector_credential (name, data_source, token_sha256, token_hint, max_records_per_minute,
                                           created_by, expires_at)
  VALUES (p_name, p_data_source, p_sha256, p_hint, p_per_minute, p_by, v_expires);
  INSERT INTO audit.security_event (principal, action, resource, decision, detail)
  VALUES (p_by, 'connector_credential_issue', 'connector_credential:' || p_name, 'allow',
          jsonb_build_object('data_source', p_data_source, 'expires_at', v_expires, 'per_minute', p_per_minute,
                             'via', 'api'));
  RETURN v_expires;
END $$;
REVOKE ALL ON FUNCTION ingest.issue_connector_credential(text, text, text, text, integer, integer, text) FROM PUBLIC;

CREATE FUNCTION ingest.revoke_connector_credential(p_name text, p_by text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_source text;
BEGIN
  IF p_by IS NULL OR length(p_by) NOT BETWEEN 1 AND 200 THEN
    RAISE EXCEPTION 'iptal eden kişi gerekli' USING ERRCODE = '22023';
  END IF;
  UPDATE ingest.connector_credential SET revoked_at = now(), revoked_by = p_by
   WHERE name = p_name AND revoked_at IS NULL
   RETURNING data_source INTO v_source;
  IF v_source IS NOT NULL THEN
    INSERT INTO audit.security_event (principal, action, resource, decision, detail)
    VALUES (p_by, 'connector_credential_revoke', 'connector_credential:' || p_name, 'allow',
            jsonb_build_object('data_source', v_source, 'via', 'api'));
  END IF;
  RETURN v_source;  -- NULL: kimlik yok veya zaten iptal
END $$;
REVOKE ALL ON FUNCTION ingest.revoke_connector_credential(text, text) FROM PUBLIC;

-- ---------------------------------------------------------------- nabız
ALTER TABLE ingest.worker_heartbeat DROP CONSTRAINT worker_heartbeat_kind_check;
ALTER TABLE ingest.worker_heartbeat
  ADD CONSTRAINT worker_heartbeat_kind_check CHECK (kind IN ('requests', 'ingest', 'indexer', 'connector'));

-- ---------------------------------------------------------------- yetkiler
-- Çalıştırıcı: CMDB'ye erişimi yok (evren_readonly verilmez, cmdb şeması kullanılamaz). İsteğin içeriğini
-- değiştiremez; yalnızca durum/sonuç sütunları. Zamanlanmış istek ekler; kendi örneklerini bildirir.
GRANT USAGE ON SCHEMA ingest, audit TO evren_app_connector;
GRANT SELECT (name, lifecycle) ON ingest.data_source TO evren_app_connector;
GRANT SELECT, INSERT, UPDATE ON ingest.connector_instance TO evren_app_connector;
GRANT SELECT ON ingest.connector_run_request TO evren_app_connector;
GRANT INSERT (instance, data_source, mode, since, origin, scheduled_for, requested_by, note)
  ON ingest.connector_run_request TO evren_app_connector;
GRANT UPDATE (status, claimed_by, started_at, finished_at, run_id, exit_code, summary, result_note)
  ON ingest.connector_run_request TO evren_app_connector;
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_connector;
GRANT INSERT ON audit.security_event TO evren_app_connector;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO evren_app_connector;

-- API: örnekleri ve istekleri okur; yalnızca elle istek ekler (köken sütununa yetkisi yok → 'manual').
-- İptal ve kimlik verme/iptal yalnızca işlevlerle.
GRANT SELECT ON ingest.connector_instance, ingest.connector_run_request TO evren_app_api;
GRANT INSERT (instance, data_source, mode, since, requested_by, note) ON ingest.connector_run_request TO evren_app_api;
GRANT EXECUTE ON FUNCTION ingest.cancel_connector_run_request(uuid, text) TO evren_app_api;
GRANT EXECUTE ON FUNCTION ingest.issue_connector_credential(text, text, text, text, integer, integer, text)
  TO evren_app_api;
GRANT EXECUTE ON FUNCTION ingest.revoke_connector_credential(text, text) TO evren_app_api;

GRANT SELECT ON ingest.connector_instance, ingest.connector_run_request TO evren_auditor_ro;

COMMIT;
