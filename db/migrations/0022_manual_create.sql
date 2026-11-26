-- 0022 — Elle CI oluşturma isteği (U6.1, ADR-0026).
--
-- API yalnızca isteği yazar (istek sütunları); uzlaştırıcı kimlik çözümlemesinden geçirip uygular ve sonucu yazar
-- (kural 1). Sonuç: created (CI açıldı), duplicate (kesin eşleşme; var olan CI result_ci), needs_decision (belirsiz
-- eşleşme; adaylar result'ta, gerekçeli force_new ile yeniden istenebilir), rejected (uygulanamadı).
-- CSV toplu içe aktarma (U6.3) bu tabloyu satır başına kullanır.
BEGIN;

CREATE TABLE ingest.manual_create (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  sys_class_name text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  attributes     jsonb NOT NULL CHECK (jsonb_typeof(attributes) = 'object' AND attributes ? 'name'),
  force_new      boolean NOT NULL DEFAULT false,
  note           text CHECK (length(note) <= 2000),
  requested_by   text NOT NULL,
  requested_at   timestamptz NOT NULL DEFAULT now(),
  status         text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'created', 'duplicate', 'needs_decision', 'rejected')),
  result_ci      meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  result         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result) = 'object'),
  result_note    text,
  applied_at     timestamptz,
  CHECK ((status = 'pending') = (applied_at IS NULL)),
  CHECK (status NOT IN ('created', 'duplicate') OR result_ci IS NOT NULL),
  -- belirsiz eşleşmeye rağmen yeni CI açmak gerekçeli bir insan kararıdır
  CHECK (NOT force_new OR length(btrim(coalesce(note, ''))) > 0)
);
CREATE INDEX manual_create_queue ON ingest.manual_create (requested_at) WHERE status = 'pending';
CREATE INDEX manual_create_requester ON ingest.manual_create (requested_by, requested_at DESC);
COMMENT ON TABLE ingest.manual_create IS 'Elle CI oluşturma istekleri; uzlaştırıcı kimlik çözümlemesinden geçirip uygular (ADR-0026)';

CREATE TRIGGER manual_create_notify AFTER INSERT ON ingest.manual_create
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();

-- API: isteği yazar (yalnızca istek sütunları); durumu ve sonucu değiştiremez.
GRANT SELECT ON ingest.manual_create TO evren_app_api;
GRANT INSERT (sys_class_name, attributes, force_new, note, requested_by) ON ingest.manual_create TO evren_app_api;
-- Uzlaştırıcı: uygular; isteğin içeriğini değiştiremez.
GRANT SELECT ON ingest.manual_create TO evren_app_reconciler;
GRANT UPDATE (status, result_ci, result, result_note, applied_at) ON ingest.manual_create TO evren_app_reconciler;
GRANT SELECT ON ingest.manual_create TO evren_auditor_ro;

COMMIT;
