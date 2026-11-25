-- 0004 · Kaynak rolleri — ürün bağımsızlığı (ADR-0009)
--
-- Uzlaştırma ve veri tazeleme kuralları ürün adına (BMC, vCenter) değil kaynak rolüne
-- (Discovery, Virtualization…) bağlanır. Veri kaynağı bir rolü dolduran somut ürün kurulumudur ve
-- yaşam döngüsüyle (active / shadow / retiring / retired) devreye alınır veya emekliye ayrılır.
--
-- Not: Bu göç, kural tablolarının henüz tohumlanmadığı (üretimde yayımlanmamış) şemaya uygulanır;
-- kural tabloları tohum SQL'i (tools/load_metadata.py) ile yeniden doldurulur.

BEGIN;

CREATE TABLE meta.source_role (
  name                text PRIMARY KEY CHECK (name ~ '^[A-Za-z][A-Za-z0-9_.-]{1,63}$'),
  label_tr            text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('discovery', 'monitoring', 'security', 'itsm', 'manual', 'platform')),
  ci_attribute_source boolean NOT NULL DEFAULT true,
  description         text
);

-- Veri kaynağı: rol, rol içi sıra, yaşam döngüsü ve ürün bilgisi
ALTER TABLE ingest.data_source
  ADD COLUMN role      text REFERENCES meta.source_role(name),
  ADD COLUMN role_rank integer NOT NULL DEFAULT 1 CHECK (role_rank > 0),
  ADD COLUMN lifecycle text NOT NULL DEFAULT 'active' CHECK (lifecycle IN ('active', 'shadow', 'retiring', 'retired')),
  ADD COLUMN product   jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(product) = 'object');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM ingest.data_source WHERE role IS NULL) THEN
    RAISE EXCEPTION 'ingest.data_source satırları rolsüz; önce tohum SQL''ini 0004 ile uyumlu hale getirin';
  END IF;
END $$;
ALTER TABLE ingest.data_source ALTER COLUMN role SET NOT NULL;

-- Aynı rolde yazan (active/retiring) iki kaynak aynı sırada olamaz: uzlaştırma sırası belirsiz kalır.
CREATE UNIQUE INDEX data_source_role_rank_writer ON ingest.data_source (role, role_rank)
  WHERE lifecycle IN ('active', 'retiring');

-- Uzlaştırma tanımı: data_source → role
DELETE FROM meta.cmdb_reconciliation_definition;
DROP INDEX meta.recon_def_lookup;
ALTER TABLE meta.cmdb_reconciliation_definition DROP COLUMN data_source;
ALTER TABLE meta.cmdb_reconciliation_definition ADD COLUMN role text NOT NULL REFERENCES meta.source_role(name);
CREATE INDEX recon_def_lookup ON meta.cmdb_reconciliation_definition (applies_to, role) WHERE active;

-- Veri tazeleme: data_source → role
DROP TABLE meta.cmdb_datasource_refresh;
CREATE TABLE meta.cmdb_datasource_refresh (
  applies_to         text NOT NULL REFERENCES meta.sys_db_object(name),
  role               text NOT NULL REFERENCES meta.source_role(name),
  effective_duration interval NOT NULL CHECK (effective_duration > interval '0'),
  PRIMARY KEY (applies_to, role)
);

-- Yetkiler (0003 ile aynı model: meta salt okunur; yazma yalnızca şema sahibi/tohum)
GRANT SELECT ON meta.source_role, meta.cmdb_datasource_refresh TO evren_readonly, evren_auditor_ro;
GRANT SELECT ON meta.source_role TO evren_app_ingest;

COMMENT ON TABLE meta.source_role IS 'Kaynak rolleri: kurallar ürün adına değil role bağlanır (ADR-0009)';
COMMENT ON COLUMN ingest.data_source.lifecycle IS
  'active: yazar ve CI açar · shadow: yalnızca karşılaştırma · retiring: yazar, CI açmaz · retired: yok sayılır';

COMMIT;
