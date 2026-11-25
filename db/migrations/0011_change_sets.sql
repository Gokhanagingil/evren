-- 0011 · Metadata değişiklik setleri: taslak → gönderildi → onaylandı → yayınlandı (F1-11, ADR-0015)
--
-- Yayın, metadata/changes/ altına konacak değişiklik dosyasını (YAML, SHA-256) üretir; YAML Git'te doğruluk
-- kaynağı olarak kalır (CLAUDE.md kural 6). Bu göç akışı veritabanında da zorlar:
--   * hazırlayan onaylayamaz (0001'deki kısıt) ve reddedemez;
--   * durum yalnızca izinli geçişlerle ilerler; yayınlanan/reddedilen set değişmez;
--   * işlemler yalnızca taslak sete eklenir; set ve işlemleri silinemez (denetim izi).

BEGIN;

ALTER TABLE meta.schema_change DROP CONSTRAINT schema_change_operation_check;
ALTER TABLE meta.schema_change ADD CONSTRAINT schema_change_operation_check CHECK (operation IN (
  'add_class', 'modify_class', 'deactivate_class', 'add_field', 'modify_field', 'deprecate_field', 'drop_field',
  'promote_field_to_column', 'add_choice', 'modify_choice', 'add_relationship_type',
  'add_data_source', 'set_source_lifecycle'));

ALTER TABLE meta.schema_change_set
  ADD COLUMN submitted_on    timestamptz,
  ADD COLUMN validation      jsonb,
  ADD COLUMN rejected_by     text,
  ADD COLUMN rejected_on     timestamptz,
  ADD COLUMN decision_note   text CHECK (length(decision_note) <= 2000),
  ADD COLUMN published_by    text,
  ADD COLUMN artifact_name   text CHECK (artifact_name ~ '^[0-9]{12}-[0-9a-f]{8}-[a-z0-9-]{1,60}\.yaml$'),
  ADD COLUMN artifact        text,
  ADD COLUMN artifact_sha256 text CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT schema_change_set_rejecter CHECK (rejected_by IS NULL OR rejected_by <> created_by),
  ADD CONSTRAINT schema_change_set_published CHECK (state <> 'published'
    OR (artifact IS NOT NULL AND artifact_sha256 IS NOT NULL AND published_by IS NOT NULL AND published_on IS NOT NULL));

CREATE FUNCTION meta.tg_change_set_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Değişiklik seti silinemez (denetim izi)' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.created_on IS DISTINCT FROM OLD.created_on THEN
    RAISE EXCEPTION 'Değişiklik setinin hazırlayanı değiştirilemez' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF OLD.state IN ('published', 'rejected') THEN
    RAISE EXCEPTION 'Yayınlanan veya reddedilen değişiklik seti değiştirilemez' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.state IS DISTINCT FROM OLD.state AND NOT (
       (OLD.state = 'draft' AND NEW.state IN ('in_review', 'rejected'))
    OR (OLD.state = 'in_review' AND NEW.state IN ('approved', 'rejected'))
    OR (OLD.state = 'approved' AND NEW.state IN ('published', 'rejected'))) THEN
    RAISE EXCEPTION 'İzinsiz durum geçişi: % → %', OLD.state, NEW.state USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER change_set_guard BEFORE UPDATE OR DELETE ON meta.schema_change_set
  FOR EACH ROW EXECUTE FUNCTION meta.tg_change_set_guard();

CREATE FUNCTION meta.tg_change_guard() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  s text;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Değişiklik seti işlemleri değiştirilemez veya silinemez' USING ERRCODE = 'insufficient_privilege';
  END IF;
  SELECT state INTO s FROM meta.schema_change_set WHERE sys_id = NEW.change_set;
  IF s IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'İşlem yalnızca taslak sete eklenebilir' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER change_guard BEFORE INSERT OR UPDATE OR DELETE ON meta.schema_change
  FOR EACH ROW EXECUTE FUNCTION meta.tg_change_guard();

-- API yalnız ekler ve durum/karar sütunlarını günceller (tablo düzeyindeki UPDATE 0003'ten geri alınır).
REVOKE UPDATE ON meta.schema_change_set, meta.schema_change FROM evren_app_api;
GRANT UPDATE (state, submitted_on, validation, impact, approved_by, approved_on, rejected_by, rejected_on,
              decision_note, published_by, published_on, artifact_name, artifact, artifact_sha256)
  ON meta.schema_change_set TO evren_app_api;
GRANT SELECT ON meta.schema_change_set, meta.schema_change TO evren_auditor_ro;

COMMENT ON COLUMN meta.schema_change_set.artifact IS 'metadata/changes/ dosyasının içeriği (YAML); Git''e PR ile eklenir.';

COMMIT;
