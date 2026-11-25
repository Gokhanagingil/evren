-- 0009 · Sürekli çalışan uzlaştırıcı (F0-10)
--
-- API'nin yazdığı insan istekleri (kuyruk kararı, ayırma, elle düzenleme) uzlaştırıcıyı PostgreSQL bildirimiyle
-- (LISTEN/NOTIFY, kanal 'evren_work') hemen uyandırır; uzlaştırıcı ayrıca düzenli aralıkla tarar (bildirim
-- kaçarsa iş kaybolmaz). Bildirim yalnızca uyandırır; iş her zaman tablolardan okunur (yük taşımaz).
--
-- Uygulanamayan istek kuyruğu kilitlemez: hata kimliğiyle reddedilir (match_candidate.apply_error).
-- Çalışan nabzı (ingest.worker_heartbeat) arayüzde "uzlaştırıcı çalışıyor mu" göstergesini besler.

BEGIN;

CREATE FUNCTION ingest.notify_work() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  PERFORM pg_notify('evren_work', TG_TABLE_NAME);
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION ingest.notify_work() FROM PUBLIC;

CREATE TRIGGER split_request_notify AFTER INSERT ON ingest.split_request
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();
CREATE TRIGGER manual_edit_notify AFTER INSERT ON ingest.manual_edit
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();
CREATE TRIGGER match_decision_notify AFTER UPDATE OF status ON ingest.match_candidate
  FOR EACH ROW WHEN (NEW.applied_at IS NULL AND NEW.status IN ('accepted', 'created_new', 'rejected'))
  EXECUTE FUNCTION ingest.notify_work();

-- Kuyruk kararı uygulanamadıysa neden (hata kimliğiyle); applied_at dolu olsa da karar uygulanmamıştır.
ALTER TABLE ingest.match_candidate ADD COLUMN apply_error text;

CREATE TABLE ingest.worker_heartbeat (
  worker_id   text PRIMARY KEY CHECK (length(worker_id) BETWEEN 1 AND 200),
  started_at  timestamptz NOT NULL,
  seen_at     timestamptz NOT NULL,
  stopped_at  timestamptz,
  processed   bigint NOT NULL DEFAULT 0 CHECK (processed >= 0),
  failed      bigint NOT NULL DEFAULT 0 CHECK (failed >= 0),
  last_error_at timestamptz,
  poll_seconds integer NOT NULL CHECK (poll_seconds BETWEEN 1 AND 3600)
);

GRANT SELECT, INSERT, UPDATE ON ingest.worker_heartbeat TO evren_app_reconciler;
GRANT SELECT ON ingest.worker_heartbeat TO evren_app_api, evren_auditor_ro;

COMMENT ON TABLE ingest.worker_heartbeat IS 'Uzlaştırıcı çalışanlarının nabzı: son görülme, işlenen/başarısız istek sayısı.';

COMMIT;
