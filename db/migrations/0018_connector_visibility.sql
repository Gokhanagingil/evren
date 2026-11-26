-- 0018 — Bağlayıcı yönetiminin görünürlüğü (U5.3, ADR-0020).
--
-- * iam.permission işlemlerine 'connector_read' (bağlayıcı kimlikleri ve çalışmayı açan kimlik; sır yok) ve
--   'connector_admin' (kimlik verme/iptal ve çalışma isteği; U5.3'ün sonraki adımı) eklenir.
-- * ingest.connector_credential_status: kimlik tablosunun SIRSIZ görünümü. Belirteç özeti (token_sha256) görünümde
--   yoktur; API rolü tabloyu yine okuyamaz, yalnızca bu görünümü okur. security_barrier: süzgeçler sızıntı yapmaz.
BEGIN;

ALTER TABLE iam.permission DROP CONSTRAINT IF EXISTS permission_operation_check;
ALTER TABLE iam.permission ADD CONSTRAINT permission_operation_check CHECK (operation IN (
  'read', 'create', 'write', 'delete', 'merge', 'split', 'review_match', 'ingest', 'schema_draft', 'schema_approve',
  'rules_admin', 'iam_admin', 'audit_read', 'connector_read', 'connector_admin'));

INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description)
SELECT v.role, 'data_source', v.op, NULL, 'internal', NULL, v.descr
  FROM (VALUES
    ('evren_data_steward', 'connector_read', 'Bağlayıcı kimlikleri ve çalışmayı açan kimlik (sır yok)'),
    ('evren_data_steward', 'connector_admin', 'Bağlayıcı kimliği verme/iptal, çalışma isteği (U5.3)'),
    ('evren_auditor', 'connector_read', 'Bağlayıcı kimlikleri ve çalışmayı açan kimlik (sır yok)')) AS v(role, op, descr)
 WHERE NOT EXISTS (SELECT 1 FROM iam.permission p WHERE p.role = v.role AND p.resource = 'data_source'
                                                    AND p.operation = v.op);

CREATE OR REPLACE VIEW ingest.connector_credential_status WITH (security_barrier = true) AS
SELECT name, data_source, token_hint, max_records_per_minute, created_by, created_at, expires_at, last_used_at,
       revoked_at, revoked_by,
       CASE WHEN revoked_at IS NOT NULL THEN 'revoked'
            WHEN expires_at <= now() THEN 'expired'
            ELSE 'active' END AS state
  FROM ingest.connector_credential;
COMMENT ON VIEW ingest.connector_credential_status IS
  'Bağlayıcı kimliklerinin sırsız görünümü (U5.3). Belirteç özeti yoktur; arayüz ve denetim için.';

REVOKE ALL ON ingest.connector_credential_status FROM PUBLIC;
GRANT SELECT ON ingest.connector_credential_status TO evren_app_api, evren_auditor_ro;

COMMIT;
