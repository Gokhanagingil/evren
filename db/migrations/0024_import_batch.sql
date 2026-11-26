-- 0024 — CSV ile toplu içe aktarma (U6.3, ADR-0026 §6).
--
-- Önizleme hiçbir şey yazmaz. Onayda geçerli satırlar bir toplu iş (ingest.import_batch) altında satır başına elle
-- oluşturma isteği olur (ingest.manual_create + batch_id, row_no); uzlaştırıcı her satırı tek oluşturma gibi kimlik
-- çözümlemesinden geçirir ve satır sırasıyla işler (aynı dosyada iki satır aynı CI'sa ikincisi "zaten var" olur).
BEGIN;

CREATE TABLE ingest.import_batch (
  sys_id        meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  file_name     text CHECK (length(file_name) <= 255),
  default_class text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  row_count     integer NOT NULL CHECK (row_count BETWEEN 1 AND 5000),
  skipped_count integer NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
  note          text CHECK (length(note) <= 2000),
  requested_by  text NOT NULL,
  requested_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX import_batch_requester ON ingest.import_batch (requested_by, requested_at DESC);
COMMENT ON TABLE ingest.import_batch IS 'CSV toplu içe aktarma işleri; satırlar ingest.manual_create''de (ADR-0026)';

ALTER TABLE ingest.manual_create
  ADD COLUMN batch_id meta.sysid REFERENCES ingest.import_batch(sys_id),
  ADD COLUMN row_no   integer CHECK (row_no > 0),
  ADD CONSTRAINT manual_create_batch_row CHECK ((batch_id IS NULL) = (row_no IS NULL));
CREATE UNIQUE INDEX manual_create_batch ON ingest.manual_create (batch_id, row_no) WHERE batch_id IS NOT NULL;

GRANT SELECT ON ingest.import_batch TO evren_app_api, evren_app_reconciler, evren_auditor_ro;
GRANT INSERT (file_name, default_class, row_count, skipped_count, note, requested_by)
  ON ingest.import_batch TO evren_app_api;
GRANT INSERT (batch_id, row_no) ON ingest.manual_create TO evren_app_api;

COMMIT;
