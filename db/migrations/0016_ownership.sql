-- 0016 — Sahiplik ve yönetişim kök alanları (U4.3, ADR-0019).
--
-- * ref.cmn_cost_center: ServiceNow maliyet merkezi tablosunun yansıması (aynı sys_id; SN'den yüklenir).
-- * cmdb.cmdb_ci.cost_center: maliyet merkezi (yumuşak referans; ServiceNow'da da yabancı anahtar yoktur).
-- * cmdb.cmdb_ci.attested / attested_by / attested_date: sahiplik onayı. Yalnızca Evren'deki onay akışı yazar
--   (Manual kaynak kaydı → uzlaştırma; keşif ve izleme araçları yazamaz: reconciliation_rules.yaml).
-- Kolonlar metadata/classes.yaml'daki cmdb_ci alanlarıyla ve evren_core.metadata.CORE_COLUMNS ile aynı sıradadır.
BEGIN;

CREATE TABLE IF NOT EXISTS ref.cmn_cost_center (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  account_number text,
  manager        meta.sysid,
  parent         meta.sysid,
  active         boolean NOT NULL DEFAULT true,
  source         text NOT NULL DEFAULT 'servicenow' CHECK (source IN ('servicenow', 'evren')),
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cmn_cost_center_name ON ref.cmn_cost_center (lower(name));

ALTER TABLE cmdb.cmdb_ci ADD COLUMN IF NOT EXISTS cost_center   meta.sysid;
ALTER TABLE cmdb.cmdb_ci ADD COLUMN IF NOT EXISTS attested      boolean;
ALTER TABLE cmdb.cmdb_ci ADD COLUMN IF NOT EXISTS attested_by   meta.sysid;
ALTER TABLE cmdb.cmdb_ci ADD COLUMN IF NOT EXISTS attested_date timestamptz;
CREATE INDEX IF NOT EXISTS cmdb_ci_cost_center ON cmdb.cmdb_ci (cost_center) WHERE cost_center IS NOT NULL;
-- Onayı gecikmiş CI listesi (sertifikasyon kampanyası, F1-17)
CREATE INDEX IF NOT EXISTS cmdb_ci_attested_date ON cmdb.cmdb_ci (attested_date);

GRANT SELECT ON ref.cmn_cost_center TO evren_readonly;
GRANT SELECT, INSERT, UPDATE ON ref.cmn_cost_center TO evren_app_sync;

COMMIT;
