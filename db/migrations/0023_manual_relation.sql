-- 0023 — Elle ilişki ekleme/kaldırma isteği (U6.2, ADR-0026 §5).
--
-- API yalnızca isteği yazar; uzlaştırıcı uygular: ilişkiyi CI'ın Manual kaynak kaydındaki ilişki listesine ekler veya
-- çıkarır ve cmdb_rel_ci'a "Manual" kaynağıyla yazar ya da yalnızca Manual kaynaklı satırı siler. İzinli ilişki
-- kuralları (ADR-0019) elle girişte zorunludur. Keşfin yazdığı ilişki elle silinmez (kaynağından kalkar).
BEGIN;

CREATE TABLE ingest.manual_relation (
  sys_id        meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  ci_sys_id     meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  target_sys_id meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  type          text NOT NULL REFERENCES cmdb.cmdb_rel_type(name) ON UPDATE CASCADE,
  direction     text NOT NULL CHECK (direction IN ('parent', 'child')),  -- ci_sys_id'nin ilişkideki rolü
  action        text NOT NULL CHECK (action IN ('add', 'remove')),
  note          text CHECK (length(note) <= 2000),
  requested_by  text NOT NULL,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  result_note   text,
  applied_at    timestamptz,
  CHECK ((status = 'pending') = (applied_at IS NULL)),
  CHECK (ci_sys_id <> target_sys_id)
);
CREATE INDEX manual_relation_queue ON ingest.manual_relation (requested_at) WHERE status = 'pending';
CREATE INDEX manual_relation_ci ON ingest.manual_relation (ci_sys_id, requested_at DESC);
COMMENT ON TABLE ingest.manual_relation IS 'Elle ilişki ekleme/kaldırma istekleri; uzlaştırıcı uygular (ADR-0026)';

CREATE TRIGGER manual_relation_notify AFTER INSERT ON ingest.manual_relation
  FOR EACH STATEMENT EXECUTE FUNCTION ingest.notify_work();

GRANT SELECT ON ingest.manual_relation TO evren_app_api;
GRANT INSERT (ci_sys_id, target_sys_id, type, direction, action, note, requested_by)
  ON ingest.manual_relation TO evren_app_api;
GRANT SELECT ON ingest.manual_relation TO evren_app_reconciler;
GRANT UPDATE (status, result_note, applied_at) ON ingest.manual_relation TO evren_app_reconciler;
GRANT SELECT ON ingest.manual_relation TO evren_auditor_ro;

COMMIT;
