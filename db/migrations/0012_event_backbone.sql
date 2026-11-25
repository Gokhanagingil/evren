-- 0012 — Olay omurgası (U2, ADR-0017): alım geçidi kimlikleri, kalıcı çalışma sayaçları, outbox bildirimi.
--
-- * Bağlayıcı kimlik bilgisi: geçidin tek kimlik doğrulama kaynağı. Belirteç yalnızca SHA-256 özeti olarak tutulur;
--   düz metin belirteç yalnızca verildiği anda bir kez gösterilir. Her kimlik tek bir veri kaynağına bağlıdır.
-- * Çalışmayı geçit açar, Kafka'ya teslim edilen kayıtları sayar (accepted_count) ve kapanış ister (bildirilen
--   kayıt sayısı); uzlaştırıcı tüm kayıtları işleyince veya süre dolunca kapatır. Sayaçlar kaynak kayıtlarından hesaplanır: aynı mesajın Kafka'dan ikinci kez gelmesi sayılmaz.
-- * Alım geçidi en az yetkiyle çalışır: kaynak kaydı okuyamaz/yazamaz; çalışmayı yalnızca açar ve kapanış ister.
BEGIN;

CREATE TABLE ingest.connector_credential (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL UNIQUE CHECK (name ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$'),
  data_source   text NOT NULL REFERENCES ingest.data_source(name),
  token_sha256  text NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  token_hint    text NOT NULL CHECK (token_hint ~ '^[A-Za-z0-9_-]{4}$'),
  max_records_per_minute integer NOT NULL DEFAULT 60000 CHECK (max_records_per_minute BETWEEN 1 AND 10000000),
  created_by    text NOT NULL CHECK (length(created_by) BETWEEN 1 AND 200),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  revoked_at    timestamptz,
  revoked_by    text,
  last_used_at  timestamptz,
  CHECK (expires_at > created_at),
  CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);
COMMENT ON TABLE ingest.connector_credential IS
  'Alım geçidi bağlayıcı kimlikleri (ADR-0017). Belirteç düz metin saklanmaz; kimlik yalnızca kendi veri kaynağına yazar.';

ALTER TABLE ingest.source_run
  ADD COLUMN opened_by            text,
  ADD COLUMN accepted_count       integer NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
  ADD COLUMN reported_count       integer CHECK (reported_count >= 0),
  ADD COLUMN reported_completed   boolean,
  ADD COLUMN reported_error_count integer CHECK (reported_error_count >= 0),
  ADD COLUMN close_requested_at   timestamptz,
  ADD COLUMN new_count            integer NOT NULL DEFAULT 0 CHECK (new_count >= 0);

-- Kalıcı sayaçlar: kaydın ilk görüldüğü ve içeriğinin son değiştiği çalışma. Çalışmanın sayıları bunlardan
-- hesaplanır (bellekte sayaç yok; birden çok tüketici ve yeniden teslim güvenli).
ALTER TABLE ingest.source_record
  ADD COLUMN first_run   uuid,
  ADD COLUMN changed_run uuid;
CREATE INDEX source_record_last_run ON ingest.source_record (last_run);

-- Çalışan nabzı: insan isteklerini uygulayan çalışan ve alım tüketicisi ayrı görünür.
ALTER TABLE ingest.worker_heartbeat
  ADD COLUMN kind text NOT NULL DEFAULT 'requests' CHECK (kind IN ('requests', 'ingest'));

-- Outbox'a yazılan olay çalışanı uyandırır (yayıncı); deyim başına bir bildirim.
CREATE TRIGGER outbox_notify AFTER INSERT ON events.outbox
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();

-- ---------------------------------------------------------------- yetkiler
-- Alım geçidi: kaynak kaydına erişimi yok (uzlaştırıcı yazar). Çalışmayı açar (yalnızca açılış sütunları) ve
-- kapanış ister (yalnızca bildirim sütunları). Kimlik bilgisini okur, yalnızca son kullanım zamanını günceller.
REVOKE ALL ON ingest.source_record FROM evren_app_ingest;
REVOKE INSERT, UPDATE ON ingest.source_run FROM evren_app_ingest;
GRANT SELECT ON ingest.source_run TO evren_app_ingest;
GRANT INSERT (run_id, data_source, mode, started_at, opened_by, notes) ON ingest.source_run TO evren_app_ingest;
GRANT UPDATE (accepted_count, reported_count, reported_completed, reported_error_count, close_requested_at)
  ON ingest.source_run TO evren_app_ingest;
GRANT SELECT ON ingest.connector_credential TO evren_app_ingest;
GRANT UPDATE (last_used_at) ON ingest.connector_credential TO evren_app_ingest;
GRANT USAGE ON SCHEMA audit TO evren_app_ingest;
GRANT INSERT ON audit.security_event TO evren_app_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO evren_app_ingest;

-- Uzlaştırıcı ve API kimlik bilgisini görmez; denetçi belirteç özeti dışındaki sütunları okur.
GRANT SELECT (id, name, data_source, token_hint, max_records_per_minute, created_by, created_at, expires_at,
              revoked_at, revoked_by, last_used_at) ON ingest.connector_credential TO evren_auditor_ro;

COMMIT;
