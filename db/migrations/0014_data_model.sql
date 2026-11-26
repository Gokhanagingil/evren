-- 0014 — CMDB veri modeli (U4, ADR-0019): bağımlı seçim değerleri ve izinli ilişki kuralları.
--
-- * meta.sys_choice.dependent_value: ServiceNow'daki gibi bir değerin hangi üst değere bağlı olduğu
--   (ör. life_cycle_stage_status 'In Use' → life_cycle_stage 'Operational').
-- * meta.cmdb_rel_rule: hangi sınıf hangi ilişki tipiyle hangi sınıfa bağlanabilir. ServiceNow bunu yalnızca
--   önerir (cmdb_rel_type_suggest); Evren denetler. İçerik metadata/relationship_rules.yaml'dan tohumlanır;
--   elle yazılmaz. Herkes okur, yalnızca göç/tohum rolü yazar.
BEGIN;

ALTER TABLE meta.sys_choice ADD COLUMN IF NOT EXISTS dependent_value text;

CREATE TABLE IF NOT EXISTS meta.cmdb_rel_rule (
  sys_id            meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  parent_class      text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  rel_type          text NOT NULL REFERENCES cmdb.cmdb_rel_type(name) ON UPDATE CASCADE,
  child_class       text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  description       text,
  verify_against_sn boolean NOT NULL DEFAULT false,
  UNIQUE (parent_class, rel_type, child_class)
);
COMMENT ON TABLE meta.cmdb_rel_rule IS 'İzinli ilişki kuralları (ADR-0019); alt sınıflara kalıtılır';

GRANT SELECT ON meta.cmdb_rel_rule TO evren_readonly;

COMMIT;
