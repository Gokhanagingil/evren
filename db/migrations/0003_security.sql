-- =============================================================================
-- Evren CMDB — veritabanı güvenlik katmanı
--
-- İlkeler (docs/06-guvenlik.md)
--   1. En az yetki: her servis yalnızca kendi işinin gerektirdiği tablolara erişir.
--   2. Golden record'a (cmdb.cmdb_ci, cmdb.cmdb_rel_ci) TEK yazan uzlaştırıcıdır. Arayüzdeki elle
--      düzenlemeler bile "Manual" kaynak kaydı olarak yazılır ve uzlaştırmadan geçer; böylece her
--      değer kurallara, denetim izine ve kaynak bilgisine tabi olur.
--   3. Denetim izi salt eklemelidir: UPDATE/DELETE/TRUNCATE tetikleyiciyle engellenir. Saklama
--      süresi dolan aylık bölümler yalnızca DBA tarafından DETACH/DROP edilir (ayrı prosedür).
--   4. Kısıtlı güvenlik verisi (Defender maruziyet/zafiyet) ayrı tabloda, ayrı yetkiyle tutulur.
--   5. Bu dosya NOLOGIN grup rolleri oluşturur. Oturum açan servis hesapları ve parolaları/sertifikaları
--      operasyon tarafından kurum sır kasasından yönetilir; repoda hiçbir kimlik bilgisi yoktur.
-- =============================================================================
BEGIN;

-- -----------------------------------------------------------------------------
-- Kısıtlı güvenlik duruşu verisi
-- -----------------------------------------------------------------------------
CREATE TABLE cmdb.ci_security_posture (
  ci_sys_id                meta.sysid PRIMARY KEY REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  data_source              text NOT NULL REFERENCES ingest.data_source(name),
  protection_status        text,
  health_status            text,
  exposure_level           text CHECK (exposure_level IN ('None', 'Low', 'Medium', 'High')),
  risk_score               text CHECK (risk_score IN ('None', 'Informational', 'Low', 'Medium', 'High')),
  vulnerabilities_critical integer CHECK (vulnerabilities_critical >= 0),
  vulnerabilities_high     integer CHECK (vulnerabilities_high >= 0),
  last_seen                timestamptz,
  observed_at              timestamptz NOT NULL
);
COMMENT ON TABLE cmdb.ci_security_posture IS 'KISITLI: yalnızca evren_security_reader/denetçi rolleri API üzerinden okur';

-- -----------------------------------------------------------------------------
-- Salt ekleme denetim izi
-- -----------------------------------------------------------------------------
CREATE FUNCTION audit.tg_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Denetim izi değiştirilemez (% on %)', TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER ci_change_append_only BEFORE UPDATE OR DELETE ON audit.ci_change
  FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only();
CREATE TRIGGER ci_change_no_truncate BEFORE TRUNCATE ON audit.ci_change
  FOR EACH STATEMENT EXECUTE FUNCTION audit.tg_append_only();
CREATE TRIGGER security_event_append_only BEFORE UPDATE OR DELETE ON audit.security_event
  FOR EACH ROW EXECUTE FUNCTION audit.tg_append_only();
CREATE TRIGGER security_event_no_truncate BEFORE TRUNCATE ON audit.security_event
  FOR EACH STATEMENT EXECUTE FUNCTION audit.tg_append_only();

-- Yetki değişiklikleri de denetim izine düşer.
CREATE FUNCTION iam.tg_audit_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO audit.security_event (principal, action, resource, decision, detail)
  VALUES (COALESCE(current_setting('evren.actor', true), session_user),
          'role_' || lower(TG_OP),
          COALESCE(NEW.principal_type, OLD.principal_type) || ':' || COALESCE(NEW.principal_id, OLD.principal_id),
          'allow',
          jsonb_build_object('role', COALESCE(NEW.role, OLD.role), 'granted_by', COALESCE(NEW.granted_by, OLD.granted_by),
                             'expires_at', COALESCE(NEW.expires_at, OLD.expires_at)));
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER principal_role_audit AFTER INSERT OR UPDATE OR DELETE ON iam.principal_role
  FOR EACH ROW EXECUTE FUNCTION iam.tg_audit_grant();

-- -----------------------------------------------------------------------------
-- Grup rolleri (NOLOGIN)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['evren_readonly', 'evren_app_api', 'evren_app_ingest', 'evren_app_reconciler',
                           'evren_app_sync', 'evren_app_mcp', 'evren_auditor_ro'] LOOP
    -- INHERIT: servis hesabı (LOGIN) bu grup rolüne üye yapılınca yetkileri doğrudan kullanır;
    -- grup rolleri de birbirinin yetkisini (ör. evren_readonly) devralır.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION', r);
    ELSE
      EXECUTE format('ALTER ROLE %I NOLOGIN INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION', r);
    END IF;
  END LOOP;
END $$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA meta, ref, cmdb, ingest, sync, audit, insight, iam, events, sn FROM PUBLIC;

GRANT USAGE ON SCHEMA meta, ref, cmdb, sn, insight
  TO evren_readonly, evren_app_api, evren_app_reconciler, evren_app_sync, evren_app_mcp, evren_auditor_ro;
GRANT USAGE ON SCHEMA ingest TO evren_app_api, evren_app_ingest, evren_app_reconciler, evren_app_sync, evren_auditor_ro;
GRANT USAGE ON SCHEMA meta TO evren_app_ingest;
GRANT USAGE ON SCHEMA sync TO evren_app_reconciler, evren_app_sync, evren_auditor_ro;
GRANT USAGE ON SCHEMA audit TO evren_app_api, evren_app_reconciler, evren_app_sync, evren_auditor_ro;
GRANT USAGE ON SCHEMA iam TO evren_app_api, evren_app_mcp, evren_auditor_ro;
GRANT USAGE ON SCHEMA events TO evren_app_api, evren_app_reconciler;

-- Okuyucu: kısıtlı tablo hariç CMDB, referans, sözlük, görünümler, içgörüler
GRANT SELECT ON ALL TABLES IN SCHEMA meta, ref, sn, insight TO evren_readonly;
GRANT SELECT ON cmdb.cmdb_ci, cmdb.cmdb_rel_ci, cmdb.cmdb_rel_type, cmdb.cmdb_serial_number,
                cmdb.cmdb_key_value, cmdb.cmdb_sam_sw_install, cmdb.ci_reconciliation_state TO evren_readonly;

-- MCP sunucusu: okuyucu hakları + iam okuma (araç yetkisi kontrolü için); yazma YOK.
GRANT evren_readonly TO evren_app_mcp WITH INHERIT TRUE;
GRANT SELECT ON iam.role, iam.role_contains, iam.permission, iam.principal_role TO evren_app_mcp;

-- API: okur; yalnızca Manual kaynak kaydı, eşleştirme kararları, şema değişiklik setleri, rol atamaları yazar.
GRANT evren_readonly TO evren_app_api WITH INHERIT TRUE;
GRANT SELECT ON cmdb.ci_security_posture TO evren_app_api;  -- alan/rol denetimi API katmanında
GRANT SELECT ON ingest.data_source, ingest.source_run, ingest.identity_link TO evren_app_api;
GRANT SELECT, INSERT, UPDATE ON ingest.source_record, ingest.match_candidate TO evren_app_api;
GRANT SELECT, INSERT, UPDATE ON meta.schema_change_set, meta.schema_change TO evren_app_api;
GRANT SELECT ON iam.role, iam.role_contains, iam.permission, iam.sod_constraint TO evren_app_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON iam.principal_role TO evren_app_api;
GRANT SELECT, INSERT ON audit.ci_change, audit.security_event TO evren_app_api;
GRANT INSERT ON events.outbox TO evren_app_api;
GRANT UPDATE (status) ON insight.anomaly TO evren_app_api;

-- Alım geçidi: yalnızca kaynak kayıtları ve çalışmalar.
GRANT SELECT ON ingest.data_source TO evren_app_ingest;
GRANT SELECT, INSERT, UPDATE ON ingest.source_record, ingest.source_run TO evren_app_ingest;

-- Uzlaştırıcı: golden record'un tek yazarı. CI silme yetkisi YOK (yaşam döngüsü ile emekliye ayrılır).
GRANT evren_readonly TO evren_app_reconciler WITH INHERIT TRUE;
GRANT SELECT, INSERT, UPDATE ON cmdb.cmdb_ci, cmdb.cmdb_serial_number, cmdb.cmdb_key_value,
                                cmdb.ci_reconciliation_state, cmdb.ci_security_posture TO evren_app_reconciler;
GRANT SELECT, INSERT, UPDATE, DELETE ON cmdb.cmdb_rel_ci, cmdb.cmdb_sam_sw_install TO evren_app_reconciler;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ingest TO evren_app_reconciler;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA insight TO evren_app_reconciler;
GRANT SELECT, INSERT, UPDATE ON sync.sn_ci_map TO evren_app_reconciler;
GRANT SELECT ON sync.sn_sync_scope TO evren_app_reconciler;
GRANT INSERT ON audit.ci_change TO evren_app_reconciler;
GRANT INSERT ON events.outbox TO evren_app_reconciler;
GRANT SELECT, UPDATE (published_at) ON events.outbox TO evren_app_reconciler;

-- ServiceNow eşleme: referans verileri yansıtır, eşleme durumunu günceller, SN'deki değişiklikleri
-- "ServiceNow" kaynak kaydı olarak yazar (golden record'a doğrudan yazmaz).
GRANT evren_readonly TO evren_app_sync WITH INHERIT TRUE;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ref TO evren_app_sync;
GRANT SELECT, UPDATE ON sync.sn_ci_map TO evren_app_sync;
GRANT SELECT ON sync.sn_sync_scope TO evren_app_sync;
GRANT SELECT, INSERT, UPDATE ON ingest.source_record, ingest.source_run TO evren_app_sync;
GRANT SELECT ON ingest.data_source TO evren_app_sync;
GRANT INSERT ON audit.security_event TO evren_app_sync;

-- Denetçi: her şeyi okur, hiçbir şey yazmaz.
GRANT SELECT ON ALL TABLES IN SCHEMA meta, ref, cmdb, ingest, sync, audit, insight, iam, events, sn TO evren_auditor_ro;

-- Yükleyicinin sonradan ürettiği sn.* görünümleri için varsayılan okuma yetkisi
ALTER DEFAULT PRIVILEGES IN SCHEMA sn GRANT SELECT ON TABLES TO evren_readonly, evren_auditor_ro;

-- Kimlik sütunlu tablolar için dizi kullanımı
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit, events TO evren_app_api, evren_app_reconciler, evren_app_sync;

COMMIT;
