-- 0030 — Kullanıcılar ve roller yönetim panelinden (U8.3, ADR-0034).
--
-- * Kişi kaydı: Evren'de açılan kişi (ref.sys_user.source = 'evren'). API yalnızca kendi açtığı kişiyi yazar;
--   ServiceNow veya Entra kaynaklı kişide yalnızca Entra nesne kimliğini (entra_oid) bağlayabilir. ServiceNow eşlemesi
--   Evren ve Entra kaynaklı satırı ezmez (ADR-0022), entra_oid sütununa da yazamaz.
-- * Grup: yalnızca Entra grup kimliği (entra_group_id) bağlanır; grup rolü bu kimlikle verilir.
-- * Rol olayları denetim izine gerekçe ve süreyle düşer (evren.reason oturum ayarı: geri alma gerekçesi).
-- * Konfigürasyon yöneticisi rolü ve yeni işlem task_admin (yönetişim görevleri, U8.4–U8.6).
BEGIN;

-- Kişi kaydı ---------------------------------------------------------------
GRANT INSERT (sys_id, user_name, name, email, entra_oid, active, source, sys_updated_on) ON ref.sys_user TO evren_app_api;
GRANT UPDATE (name, email, entra_oid, active, sys_updated_on) ON ref.sys_user TO evren_app_api;
GRANT UPDATE (entra_group_id, sys_updated_on) ON ref.sys_user_group TO evren_app_api;

CREATE OR REPLACE FUNCTION ref.tg_people_api_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_user <> 'evren_app_api' THEN
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'sys_user' THEN
    IF TG_OP = 'INSERT' AND NEW.source <> 'evren' THEN
      RAISE EXCEPTION 'Evren yalnızca kendi açtığı kişi kaydını yazar' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.source <> 'evren'
       AND (NEW.name IS DISTINCT FROM OLD.name OR NEW.email IS DISTINCT FROM OLD.email
            OR NEW.active IS DISTINCT FROM OLD.active) THEN
      RAISE EXCEPTION 'ServiceNow veya Entra kaynaklı kişide yalnızca Entra nesne kimliği bağlanabilir'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS sys_user_api_guard ON ref.sys_user;
CREATE TRIGGER sys_user_api_guard BEFORE INSERT OR UPDATE ON ref.sys_user
  FOR EACH ROW EXECUTE FUNCTION ref.tg_people_api_guard();

-- Rol olaylarının denetimi: verirken gerekçe, geri alırken geri alma gerekçesi -------------------------------
CREATE OR REPLACE FUNCTION iam.tg_audit_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO audit.security_event (principal, action, resource, decision, detail)
  VALUES (COALESCE(NULLIF(current_setting('evren.actor', true), ''), session_user),
          'role_' || lower(TG_OP),
          COALESCE(NEW.principal_type, OLD.principal_type) || ':' || COALESCE(NEW.principal_id, OLD.principal_id),
          'allow',
          jsonb_strip_nulls(jsonb_build_object(
            'role', COALESCE(NEW.role, OLD.role), 'granted_by', COALESCE(NEW.granted_by, OLD.granted_by),
            'expires_at', COALESCE(NEW.expires_at, OLD.expires_at),
            'justification', CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.justification END,
            'reason', NULLIF(current_setting('evren.reason', true), ''),
            'subject', NULLIF(current_setting('evren.subject', true), ''))));
  RETURN COALESCE(NEW, OLD);
END $$;

-- Konfigürasyon yöneticisi ---------------------------------------------------
ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin', 'anomaly_review', 'attest', 'retire',
  'task_admin'));

INSERT INTO iam.role (name, label_tr, description, elevated, machine) VALUES
  ('evren_config_manager', 'Konfigürasyon yöneticisi',
   'Yönetişim görevlerini yönetir: sorumlulara zamanlanmış görev kampanyaları, bulgu kuralları ve görev kanalları', false, false)
ON CONFLICT (name) DO NOTHING;
INSERT INTO iam.role_contains (role, contains) VALUES ('evren_config_manager', 'evren_reader') ON CONFLICT DO NOTHING;

INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT 'evren_config_manager', 'governance', 'task_admin', NULL, 'internal', NULL,
       'Yönetişim görevi tanımları: kampanya, bulgu kuralı, kanal (U8.4–U8.6)'
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = 'evren_config_manager' AND p.resource = 'governance'
                                                    AND p.operation = 'task_admin');

INSERT INTO iam.sod_constraint (role_a, role_b, description) VALUES
  ('evren_auditor', 'evren_config_manager', 'Denetçi denetlediği yönetişim sürecini yönetemez')
ON CONFLICT DO NOTHING;

COMMIT;
