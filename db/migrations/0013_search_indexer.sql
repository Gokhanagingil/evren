-- 0013 — Arama dizinleyicisi (U3, ADR-0018): ayrı grup rolü ve nabız türü.
--
-- Dizinleyici golden record'u yalnızca okur (evren_readonly) ve CI'ın hangi kaynaklardan beslendiğini görmek için
-- kaynak bağlarının üç sütununu okur. Yazabildiği tek tablo kendi nabzıdır. Kafka'dan CI olaylarını okuyup
-- OpenSearch'e yazar; veritabanında hiçbir iş verisini değiştiremez.
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evren_app_indexer') THEN
    CREATE ROLE evren_app_indexer NOLOGIN INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;

GRANT evren_readonly TO evren_app_indexer WITH INHERIT TRUE;
GRANT USAGE ON SCHEMA ingest TO evren_app_indexer;
GRANT SELECT (ci_sys_id, data_source, active) ON ingest.identity_link TO evren_app_indexer;
GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_indexer;

ALTER TABLE ingest.worker_heartbeat DROP CONSTRAINT worker_heartbeat_kind_check;
ALTER TABLE ingest.worker_heartbeat
  ADD CONSTRAINT worker_heartbeat_kind_check CHECK (kind IN ('requests', 'ingest', 'indexer'));

COMMIT;
