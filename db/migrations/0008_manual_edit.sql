-- 0008 · Elle düzenleme (F0-09) — CLAUDE.md kural 1
--
-- Arayüz/API düzenlemesi golden record'a yazılmaz. Akış:
--   API: yetki (cmdb_ci/write + alan hassasiyeti) + alan sözlükten çözülür + tip/seçim/referans doğrulaması
--        → ingest.manual_edit + events.outbox
--   Uzlaştırıcı: CI'ın 'Manual' kaynak kaydını (source_key = CI sys_id) istekle birleştirir, uzlaştırmadan
--        geçirir (rol önceliği geçerli: keşif sahipli alanda Manual yalnızca yedektir) → sonuç alan başına.
-- Değer null ise o alandaki elle girilmiş değer kaldırılır (golden record'daki değeri diğer kaynaklar belirler).

BEGIN;

CREATE TABLE ingest.manual_edit (
  sys_id       meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  ci_sys_id    meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  attributes   jsonb NOT NULL CHECK (jsonb_typeof(attributes) = 'object' AND attributes <> '{}'::jsonb),
  note         text CHECK (length(note) <= 2000),
  requested_by text NOT NULL,
  requested_at timestamptz NOT NULL DEFAULT now(),
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected')),
  result       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(result) = 'object'),
  result_note  text,
  applied_at   timestamptz,
  CHECK ((status = 'pending') = (applied_at IS NULL))
);
CREATE INDEX manual_edit_queue ON ingest.manual_edit (requested_at) WHERE status = 'pending';
CREATE INDEX manual_edit_ci ON ingest.manual_edit (ci_sys_id, requested_at DESC);

-- API: isteği yazar (yalnızca istek sütunları); durumu ve sonucu değiştiremez.
GRANT SELECT ON ingest.manual_edit TO evren_app_api;
GRANT INSERT (ci_sys_id, attributes, note, requested_by) ON ingest.manual_edit TO evren_app_api;
-- Uzlaştırıcı: uygular; isteğin içeriğini değiştiremez.
GRANT SELECT ON ingest.manual_edit TO evren_app_reconciler;
GRANT UPDATE (status, result, result_note, applied_at) ON ingest.manual_edit TO evren_app_reconciler;
GRANT SELECT ON ingest.manual_edit TO evren_auditor_ro;

-- API'nin kaynak kaydı yazma yetkisi (0003) artık gerekmez: elle düzenleme de istek üzerinden gider.
-- En az yetki: kaynak kaydını yalnızca alım geçidi ve uzlaştırıcı yazar.
REVOKE INSERT, UPDATE ON ingest.source_record FROM evren_app_api;

COMMENT ON TABLE ingest.manual_edit IS 'Elle düzenleme istekleri: API yazar, uzlaştırıcı Manual kaynak kaydı olarak uygular.';

COMMIT;
