-- 0007 · Birleştirmeyi ayırma (split) — F0-12
--
-- Akış (CLAUDE.md kural 1 ve 3: golden record'a tek yazan uzlaştırıcıdır; birleştirme geri alınabilir):
--   API: yetki (cmdb_ci/split) → ingest.split_request + events.outbox
--   Uzlaştırıcı: bağı sonlandırır → kaydı yeni CI'a (veya seçilen CI'a) 'split' yöntemiyle bağlar →
--     asıl CI'ın kimlik profilini kalan kaynak kayıtlarından yeniden kurar → iki CI'ı yeniden uzlaştırır.
-- CI silinmez; bağ geçmişi identity_link'te kalır (active = false, end_reason).

BEGIN;

CREATE TABLE ingest.split_request (
  sys_id       meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  link_sys_id  meta.sysid NOT NULL REFERENCES ingest.identity_link(sys_id),
  ci_sys_id    meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  data_source  text NOT NULL,
  source_key   text NOT NULL,
  target_ci    meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),  -- NULL: kayıt için yeni CI açılır
  note         text CHECK (length(note) <= 2000),
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  result_ci    meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  result_note  text,
  applied_at   timestamptz,
  FOREIGN KEY (data_source, source_key) REFERENCES ingest.source_record(data_source, source_key),
  CHECK (target_ci IS DISTINCT FROM ci_sys_id),
  CHECK ((status = 'pending') = (applied_at IS NULL)),
  CHECK (status <> 'applied' OR result_ci IS NOT NULL)
);
-- Aynı bağ için aynı anda tek bekleyen istek.
CREATE UNIQUE INDEX split_request_pending ON ingest.split_request (link_sys_id) WHERE status = 'pending';
CREATE INDEX split_request_queue ON ingest.split_request (requested_at) WHERE status = 'pending';
CREATE INDEX split_request_ci ON ingest.split_request (ci_sys_id, requested_at DESC);

-- Kimlik dizinini bir CI için yeniden kurar. Uzlaştırıcıya identity_key üzerinde genel DELETE yetkisi
-- verilmez (0005); yalnızca bu işlev, tek CI'ın anahtarlarını değiştirebilir. Sabit search_path, tam nitelikli adlar.
CREATE FUNCTION ingest.replace_identity_keys(p_ci meta.sysid, p_family text, p_types text[], p_values text[])
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
  DELETE FROM ingest.identity_key WHERE ci_sys_id = p_ci;
  INSERT INTO ingest.identity_key (key_type, family, value, ci_sys_id)
  SELECT q.key_type, coalesce(p_family, ''), q.value, p_ci
    FROM unnest(p_types, p_values) AS q(key_type, value)
  ON CONFLICT DO NOTHING;
$$;
REVOKE ALL ON FUNCTION ingest.replace_identity_keys(meta.sysid, text, text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ingest.replace_identity_keys(meta.sysid, text, text[], text[]) TO evren_app_reconciler;

-- API: isteği yazar (yalnızca istek sütunları), durumu değiştiremez.
GRANT SELECT ON ingest.split_request TO evren_app_api;
GRANT INSERT (link_sys_id, ci_sys_id, data_source, source_key, target_ci, note, requested_by)
  ON ingest.split_request TO evren_app_api;
-- Uzlaştırıcı: uygular; isteğin içeriğini değiştiremez.
GRANT SELECT ON ingest.split_request TO evren_app_reconciler;
GRANT UPDATE (status, result_ci, result_note, applied_at) ON ingest.split_request TO evren_app_reconciler;
GRANT SELECT ON ingest.split_request TO evren_auditor_ro;

COMMENT ON TABLE ingest.split_request IS 'Birleştirmeyi ayırma istekleri: API yazar, uzlaştırıcı uygular (F0-12).';

COMMIT;
