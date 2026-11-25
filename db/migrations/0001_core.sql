-- =============================================================================
-- Evren CMDB — çekirdek şema (PostgreSQL 16+)
--
-- Şemalar
--   meta     : sözlük (sys_db_object, sys_dictionary, sys_choice), kurallar, şema değişiklik setleri
--   ref      : ServiceNow'dan aynı sys_id'lerle yansıtılan referans veriler (kullanıcı, grup, konum…)
--   cmdb     : golden record'lar (cmdb_ci, cmdb_rel_ci, …)
--   ingest   : veri kaynakları, çalışmalar, kaynak kayıtları, kimlik bağlantıları, eşleştirme kuyruğu
--   sync     : ServiceNow eşleme durumu
--   audit    : öznitelik değişim geçmişi (sys_audit eşdeğeri), güvenlik olayları
--   insight  : anomaliler, CMDB sağlık metrikleri
--   iam      : roller, izinler (RBAC + ABAC), görevler ayrılığı (SoD)
--   events   : transactional outbox (Kafka'ya yayın)
--   sn       : ServiceNow tablo adlarıyla sorgulanabilen sınıf görünümleri (yükleyici üretir)
--
-- Kurallar
--   * sys_id: 32 haneli küçük harf hex (ServiceNow biçimi). SN kökenli CI'larda SN sys_id'si aynen korunur.
--   * Referans alanlar (support_group, location…) yumuşak referanstır: FK yok, sağlık metriği
--     "yetim referans" olarak izlenir (ServiceNow davranışıyla aynı; alımı bloke etmez).
--   * CI'lar fiziksel olarak silinmez; yaşam döngüsü durumlarıyla emekliye ayrılır.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS cmdb;
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE SCHEMA IF NOT EXISTS sync;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS insight;
CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS events;
CREATE SCHEMA IF NOT EXISTS sn;

-- -----------------------------------------------------------------------------
-- Ortak tipler ve fonksiyonlar
-- -----------------------------------------------------------------------------
CREATE DOMAIN meta.sysid AS char(32) CHECK (VALUE ~ '^[0-9a-f]{32}$');

CREATE FUNCTION meta.new_sys_id() RETURNS meta.sysid
LANGUAGE sql VOLATILE AS $$ SELECT replace(gen_random_uuid()::text, '-', '')::meta.sysid $$;

CREATE FUNCTION meta.tg_touch() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.sys_updated_on := now();
  NEW.sys_mod_count := COALESCE(OLD.sys_mod_count, 0) + 1;
  RETURN NEW;
END $$;

-- -----------------------------------------------------------------------------
-- meta: sözlük
-- -----------------------------------------------------------------------------
CREATE TABLE meta.sys_db_object (
  sys_id            meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name              text NOT NULL UNIQUE CHECK (name ~ '^[a-z][a-z0-9_]{1,79}$'),
  label             text NOT NULL,
  label_tr          text NOT NULL,
  super_class       text REFERENCES meta.sys_db_object(name) DEFERRABLE INITIALLY DEFERRED,
  is_cmdb_class     boolean NOT NULL DEFAULT true,
  is_abstract       boolean NOT NULL DEFAULT false,
  verify_against_sn boolean NOT NULL DEFAULT false,
  origin            text NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'servicenow', 'evren')),
  active            boolean NOT NULL DEFAULT true,
  sys_created_on    timestamptz NOT NULL DEFAULT now(),
  sys_updated_on    timestamptz NOT NULL DEFAULT now(),
  sys_mod_count     integer NOT NULL DEFAULT 0,
  CHECK (super_class IS DISTINCT FROM name)
);
CREATE TRIGGER sys_db_object_touch BEFORE UPDATE ON meta.sys_db_object FOR EACH ROW EXECUTE FUNCTION meta.tg_touch();

-- Kalıtım kapanışı (ata → torun). Yükleyici ve şema yayınlama işlemi günceller.
CREATE TABLE meta.class_closure (
  ancestor   text NOT NULL REFERENCES meta.sys_db_object(name) ON DELETE CASCADE,
  descendant text NOT NULL REFERENCES meta.sys_db_object(name) ON DELETE CASCADE,
  depth      smallint NOT NULL CHECK (depth >= 0),
  PRIMARY KEY (ancestor, descendant)
);
CREATE INDEX class_closure_descendant ON meta.class_closure (descendant);

CREATE TABLE meta.sys_dictionary (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name           text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  element        text NOT NULL CHECK (element ~ '^[a-z][a-z0-9_]{0,79}$'),
  column_label   text,
  label_tr       text NOT NULL,
  internal_type  text NOT NULL CHECK (internal_type IN
                   ('string', 'integer', 'decimal', 'boolean', 'glide_date_time', 'glide_date', 'reference')),
  max_length     integer CHECK (max_length IS NULL OR max_length BETWEEN 1 AND 65000),
  reference      text,
  is_choice      boolean NOT NULL DEFAULT false,
  mandatory      boolean NOT NULL DEFAULT false,
  read_only      boolean NOT NULL DEFAULT false,
  default_value  text,
  storage        text NOT NULL DEFAULT 'jsonb' CHECK (storage IN ('column', 'jsonb')),
  indexed        boolean NOT NULL DEFAULT false,
  sn_sync        boolean NOT NULL DEFAULT true,
  sensitivity    text NOT NULL DEFAULT 'internal' CHECK (sensitivity IN ('public', 'internal', 'confidential', 'restricted')),
  verify_against_sn boolean NOT NULL DEFAULT false,
  origin         text NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'servicenow', 'evren')),
  active         boolean NOT NULL DEFAULT true,
  deprecated_on  timestamptz,
  sys_created_on timestamptz NOT NULL DEFAULT now(),
  sys_updated_on timestamptz NOT NULL DEFAULT now(),
  sys_mod_count  integer NOT NULL DEFAULT 0,
  UNIQUE (name, element),
  CHECK (internal_type <> 'reference' OR reference IS NOT NULL),
  CHECK (storage <> 'column' OR name = 'cmdb_ci')
);
CREATE TRIGGER sys_dictionary_touch BEFORE UPDATE ON meta.sys_dictionary FOR EACH ROW EXECUTE FUNCTION meta.tg_touch();

CREATE TABLE meta.sys_choice (
  sys_id    meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name      text NOT NULL,
  element   text NOT NULL,
  value     text NOT NULL,
  label     text NOT NULL,
  label_tr  text NOT NULL,
  sequence  integer NOT NULL DEFAULT 0,
  inactive  boolean NOT NULL DEFAULT false,
  UNIQUE (name, element, value),
  FOREIGN KEY (name, element) REFERENCES meta.sys_dictionary(name, element) ON UPDATE CASCADE
);

-- Şema değişiklik setleri: taslak → inceleme → onay → yayın. Talep eden onaylayamaz (SoD).
CREATE TABLE meta.schema_change_set (
  sys_id       meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name         text NOT NULL,
  description  text,
  state        text NOT NULL DEFAULT 'draft' CHECK (state IN ('draft', 'in_review', 'approved', 'published', 'rejected')),
  created_by   text NOT NULL,
  created_on   timestamptz NOT NULL DEFAULT now(),
  approved_by  text,
  approved_on  timestamptz,
  published_on timestamptz,
  impact       jsonb,
  CHECK (approved_by IS NULL OR approved_by <> created_by),
  CHECK (state NOT IN ('approved', 'published') OR approved_by IS NOT NULL)
);

CREATE TABLE meta.schema_change (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  change_set     meta.sysid NOT NULL REFERENCES meta.schema_change_set(sys_id) ON DELETE CASCADE,
  sequence       integer NOT NULL,
  operation      text NOT NULL CHECK (operation IN ('add_class', 'modify_class', 'deactivate_class', 'add_field',
                   'modify_field', 'deprecate_field', 'drop_field', 'promote_field_to_column', 'add_choice',
                   'modify_choice', 'add_relationship_type')),
  target_table   text NOT NULL,
  target_element text,
  payload        jsonb NOT NULL DEFAULT '{}',
  UNIQUE (change_set, sequence)
);

-- -----------------------------------------------------------------------------
-- ingest: veri kaynakları (kural tablolarından önce; FK hedefi)
-- -----------------------------------------------------------------------------
CREATE TABLE ingest.data_source (
  name                text PRIMARY KEY CHECK (name ~ '^[A-Za-z][A-Za-z0-9_.-]{1,63}$'),
  label_tr            text NOT NULL,
  kind                text NOT NULL CHECK (kind IN ('discovery', 'monitoring', 'security', 'itsm', 'manual', 'platform')),
  connector           text,
  schedule            text,
  ci_attribute_source boolean NOT NULL DEFAULT true,
  active              boolean NOT NULL DEFAULT true,
  breaker_thresholds  jsonb NOT NULL DEFAULT '{}',
  owner_group         meta.sysid,
  notes               text
);

-- -----------------------------------------------------------------------------
-- meta: kimlik ve uzlaştırma kuralları
-- -----------------------------------------------------------------------------
CREATE TABLE meta.cmdb_identifier (
  sys_id          meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  applies_to      text NOT NULL UNIQUE REFERENCES meta.sys_db_object(name),
  independent     boolean NOT NULL DEFAULT true,
  description     text,
  dependency      jsonb,
  active          boolean NOT NULL DEFAULT true
);

CREATE TABLE meta.cmdb_identifier_entry (
  sys_id          meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  identifier      meta.sysid NOT NULL REFERENCES meta.cmdb_identifier(sys_id) ON DELETE CASCADE,
  priority        integer NOT NULL CHECK (priority > 0),
  search_table    text,
  attributes      text[] NOT NULL CHECK (cardinality(attributes) > 0),
  evren_extension boolean NOT NULL DEFAULT false,
  active          boolean NOT NULL DEFAULT true,
  UNIQUE (identifier, priority)
);

CREATE TABLE meta.cmdb_reconciliation_definition (
  sys_id       meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  applies_to   text NOT NULL REFERENCES meta.sys_db_object(name),
  data_source  text NOT NULL REFERENCES ingest.data_source(name),
  priority     integer NOT NULL CHECK (priority > 0),
  attributes   text[],             -- NULL: tüm öznitelikler (yalnızca cmdb_ci için önerilir)
  description  text,
  active       boolean NOT NULL DEFAULT true
);
CREATE INDEX recon_def_lookup ON meta.cmdb_reconciliation_definition (applies_to, data_source) WHERE active;

CREATE TABLE meta.cmdb_datasource_refresh (
  applies_to         text NOT NULL REFERENCES meta.sys_db_object(name),
  data_source        text NOT NULL REFERENCES ingest.data_source(name),
  effective_duration interval NOT NULL CHECK (effective_duration > interval '0'),
  PRIMARY KEY (applies_to, data_source)
);

CREATE TABLE meta.cmdb_staleness_rule (
  applies_to                 text PRIMARY KEY REFERENCES meta.sys_db_object(name),
  stale_after                interval NOT NULL,
  absent_candidate_after     interval NOT NULL,
  require_all_sources_absent boolean NOT NULL DEFAULT true,
  CHECK (absent_candidate_after > stale_after)
);

-- -----------------------------------------------------------------------------
-- ref: ServiceNow'dan yansıtılan referans veriler (aynı sys_id)
-- -----------------------------------------------------------------------------
CREATE TABLE ref.sys_user (
  sys_id         meta.sysid PRIMARY KEY,
  user_name      text NOT NULL,
  name           text,
  email          text,
  department     meta.sysid,
  entra_oid      uuid UNIQUE,
  active         boolean NOT NULL DEFAULT true,
  source         text NOT NULL DEFAULT 'servicenow' CHECK (source IN ('servicenow', 'entra', 'evren')),
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX sys_user_user_name ON ref.sys_user (lower(user_name));

CREATE TABLE ref.sys_user_group (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  manager        meta.sysid,
  email          text,
  entra_group_id uuid UNIQUE,
  active         boolean NOT NULL DEFAULT true,
  source         text NOT NULL DEFAULT 'servicenow' CHECK (source IN ('servicenow', 'entra', 'evren')),
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sys_user_group_name ON ref.sys_user_group (lower(name));

CREATE TABLE ref.sys_user_grmember (
  sys_id     meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  "group"    meta.sysid NOT NULL REFERENCES ref.sys_user_group(sys_id) ON DELETE CASCADE,
  "user"     meta.sysid NOT NULL REFERENCES ref.sys_user(sys_id) ON DELETE CASCADE,
  UNIQUE ("group", "user")
);
CREATE INDEX sys_user_grmember_user ON ref.sys_user_grmember ("user");

CREATE TABLE ref.cmn_location (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  full_name      text,
  parent         meta.sysid,
  city           text,
  country        text,
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ref.core_company (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  manufacturer   boolean NOT NULL DEFAULT false,
  vendor         boolean NOT NULL DEFAULT false,
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX core_company_name_trgm ON ref.core_company USING gin (name gin_trgm_ops);

CREATE TABLE ref.cmn_department (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  company        meta.sysid,
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ref.cmdb_model (
  sys_id         meta.sysid PRIMARY KEY,
  name           text NOT NULL,
  display_name   text,
  manufacturer   meta.sysid,
  model_number   text,
  sys_class_name text,
  sys_updated_on timestamptz NOT NULL DEFAULT now()
);

-- Değişiklik kayıtları: yetkili değişiklik penceresi ve yetkisiz değişiklik tespiti için
CREATE TABLE ref.sn_change_request (
  sys_id            meta.sysid PRIMARY KEY,
  number            text NOT NULL UNIQUE,
  short_description text,
  type              text,
  state             text,
  approval          text,
  start_date        timestamptz,
  end_date          timestamptz,
  cmdb_ci           meta.sysid,
  sys_updated_on    timestamptz NOT NULL
);
CREATE INDEX sn_change_window ON ref.sn_change_request (start_date, end_date);

CREATE TABLE ref.sn_task_ci (
  sys_id   meta.sysid PRIMARY KEY,
  task     meta.sysid NOT NULL,
  ci_item  meta.sysid NOT NULL
);
CREATE INDEX sn_task_ci_ci ON ref.sn_task_ci (ci_item);

-- -----------------------------------------------------------------------------
-- cmdb: golden record'lar
-- -----------------------------------------------------------------------------
CREATE TABLE cmdb.cmdb_ci (
  sys_id                  meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  sys_class_name          text NOT NULL REFERENCES meta.sys_db_object(name) ON UPDATE CASCADE,
  -- cmdb_ci fiziksel kolonları: metadata/classes.yaml (storage: column) ile birebir aynı olmalı
  name                    text,
  short_description       text,
  serial_number           text,
  asset_tag               text,
  ip_address              text,
  mac_address             text,
  fqdn                    text,
  dns_domain              text,
  operational_status      smallint,
  install_status          smallint,
  used_for                text,
  discovery_source        text,
  first_discovered        timestamptz,
  last_discovered         timestamptz,
  manufacturer            meta.sysid,
  model_id                meta.sysid,
  location                meta.sysid,
  company                 meta.sysid,
  department              meta.sysid,
  support_group           meta.sysid,
  assignment_group        meta.sysid,
  change_control          meta.sysid,
  managed_by              meta.sysid,
  owned_by                meta.sysid,
  assigned_to             meta.sysid,
  duplicate_of            meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  correlation_id          text,
  object_id               text,
  category                text,
  subcategory             text,
  install_date            timestamptz,
  warranty_expiration     date,
  life_cycle_stage        text,
  life_cycle_stage_status text,
  comments                text,
  -- sınıfa özgü alanlar (sözlükte storage='jsonb')
  class_attributes        jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(class_attributes) = 'object'),
  -- Evren
  golden_version          bigint NOT NULL DEFAULT 1,
  sys_created_on          timestamptz NOT NULL DEFAULT now(),
  sys_created_by          text NOT NULL DEFAULT 'system',
  sys_updated_on          timestamptz NOT NULL DEFAULT now(),
  sys_updated_by          text NOT NULL DEFAULT 'system',
  sys_mod_count           integer NOT NULL DEFAULT 0,
  CHECK (duplicate_of IS DISTINCT FROM sys_id)
);

CREATE INDEX cmdb_ci_class          ON cmdb.cmdb_ci (sys_class_name);
CREATE INDEX cmdb_ci_name_lower     ON cmdb.cmdb_ci (lower(name));
CREATE INDEX cmdb_ci_name_trgm      ON cmdb.cmdb_ci USING gin (name gin_trgm_ops);
CREATE INDEX cmdb_ci_serial         ON cmdb.cmdb_ci (upper(serial_number)) WHERE serial_number IS NOT NULL;
CREATE INDEX cmdb_ci_ip             ON cmdb.cmdb_ci (ip_address) WHERE ip_address IS NOT NULL;
CREATE INDEX cmdb_ci_fqdn           ON cmdb.cmdb_ci (lower(fqdn)) WHERE fqdn IS NOT NULL;
CREATE INDEX cmdb_ci_correlation    ON cmdb.cmdb_ci (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX cmdb_ci_object_id      ON cmdb.cmdb_ci (object_id) WHERE object_id IS NOT NULL;
CREATE INDEX cmdb_ci_last_disc      ON cmdb.cmdb_ci (last_discovered);
CREATE INDEX cmdb_ci_support_group  ON cmdb.cmdb_ci (support_group) WHERE support_group IS NOT NULL;
CREATE INDEX cmdb_ci_assign_group   ON cmdb.cmdb_ci (assignment_group) WHERE assignment_group IS NOT NULL;
CREATE INDEX cmdb_ci_status         ON cmdb.cmdb_ci (operational_status, install_status);
CREATE INDEX cmdb_ci_attrs          ON cmdb.cmdb_ci USING gin (class_attributes jsonb_path_ops);

-- class_attributes anahtar ve tip doğrulaması: sözlükte tanımsız alan yazılamaz.
CREATE FUNCTION cmdb.tg_ci_validate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  bad text;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    NEW.sys_updated_on := now();
    NEW.sys_mod_count := OLD.sys_mod_count + 1;
    NEW.golden_version := OLD.golden_version + 1;
  END IF;
  IF NEW.class_attributes = '{}'::jsonb THEN
    RETURN NEW;
  END IF;
  SELECT string_agg(k.key || COALESCE(' (' || d.internal_type || ' beklenir)', ' (sözlükte yok)'), ', ')
    INTO bad
    FROM jsonb_each(NEW.class_attributes) AS k(key, value)
    LEFT JOIN LATERAL (
      SELECT dd.internal_type
        FROM meta.sys_dictionary dd
        JOIN meta.class_closure cc ON cc.ancestor = dd.name
       WHERE cc.descendant = NEW.sys_class_name
         AND dd.element = k.key
         AND dd.storage = 'jsonb'
         AND dd.active
       LIMIT 1
    ) d ON true
   WHERE d.internal_type IS NULL
      OR (jsonb_typeof(k.value) <> 'null' AND NOT (
            (d.internal_type IN ('integer', 'decimal') AND jsonb_typeof(k.value) = 'number')
         OR (d.internal_type = 'boolean' AND jsonb_typeof(k.value) = 'boolean')
         OR (d.internal_type IN ('string', 'reference', 'glide_date_time', 'glide_date') AND jsonb_typeof(k.value) = 'string')));
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'Geçersiz class_attributes (%): %', NEW.sys_class_name, bad USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cmdb_ci_validate BEFORE INSERT OR UPDATE ON cmdb.cmdb_ci FOR EACH ROW EXECUTE FUNCTION cmdb.tg_ci_validate();

CREATE TABLE cmdb.cmdb_rel_type (
  sys_id            meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  name              text NOT NULL UNIQUE CHECK (name ~ '^[^:]+::[^:]+$'),
  parent_descriptor text NOT NULL,
  child_descriptor  text NOT NULL,
  label_tr          text,
  origin            text NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'servicenow', 'evren'))
);

CREATE TABLE cmdb.cmdb_rel_ci (
  sys_id           meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  parent           meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  child            meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  type             meta.sysid NOT NULL REFERENCES cmdb.cmdb_rel_type(sys_id),
  discovery_source text,
  first_discovered timestamptz,
  last_discovered  timestamptz,
  sys_created_on   timestamptz NOT NULL DEFAULT now(),
  sys_updated_on   timestamptz NOT NULL DEFAULT now(),
  sys_mod_count    integer NOT NULL DEFAULT 0,
  UNIQUE (parent, child, type),
  CHECK (parent <> child)
);
CREATE INDEX cmdb_rel_ci_child ON cmdb.cmdb_rel_ci (child, type);
CREATE INDEX cmdb_rel_ci_type  ON cmdb.cmdb_rel_ci (type);
CREATE TRIGGER cmdb_rel_ci_touch BEFORE UPDATE ON cmdb.cmdb_rel_ci FOR EACH ROW EXECUTE FUNCTION meta.tg_touch();

CREATE TABLE cmdb.cmdb_serial_number (
  sys_id             meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  cmdb_ci            meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  serial_number      text NOT NULL,
  serial_number_type text NOT NULL DEFAULT 'system',
  valid              boolean NOT NULL DEFAULT true,
  UNIQUE (cmdb_ci, serial_number_type, serial_number)
);
CREATE INDEX cmdb_serial_number_value ON cmdb.cmdb_serial_number (upper(serial_number)) WHERE valid;

CREATE TABLE cmdb.cmdb_key_value (
  sys_id             meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  configuration_item meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  key                text NOT NULL,
  value              text,
  data_source        text,
  UNIQUE (configuration_item, key)
);

-- Yazılım kurulumları (ServiceNow cmdb_sam_sw_install) — milyonlarca satır; hash bölümleme.
CREATE TABLE cmdb.cmdb_sam_sw_install (
  sys_id           meta.sysid NOT NULL DEFAULT meta.new_sys_id(),
  installed_on     meta.sysid NOT NULL,
  display_name     text NOT NULL,
  version          text,
  publisher        text,
  install_date     timestamptz,
  discovery_source text NOT NULL,
  last_discovered  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (installed_on, sys_id)
) PARTITION BY HASH (installed_on);
DO $$
BEGIN
  FOR i IN 0..15 LOOP
    EXECUTE format('CREATE TABLE cmdb.cmdb_sam_sw_install_p%s PARTITION OF cmdb.cmdb_sam_sw_install
                    FOR VALUES WITH (MODULUS 16, REMAINDER %s)', lpad(i::text, 2, '0'), i);
  END LOOP;
END $$;
CREATE INDEX cmdb_sam_sw_install_name ON cmdb.cmdb_sam_sw_install (lower(display_name), version);

-- Uzlaştırma durumu: her özniteliğin kazanan kaynağı ve gerekçesi ("bu değer nereden geldi?")
CREATE TABLE cmdb.ci_reconciliation_state (
  ci_sys_id   meta.sysid PRIMARY KEY REFERENCES cmdb.cmdb_ci(sys_id) ON DELETE CASCADE,
  winners     jsonb NOT NULL DEFAULT '{}',
  conflicts   jsonb NOT NULL DEFAULT '{}',
  input_hash  text NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- ingest: çalışmalar, kaynak kayıtları, kimlik bağlantıları, eşleştirme kuyruğu
-- -----------------------------------------------------------------------------
CREATE TABLE ingest.source_run (
  run_id          uuid PRIMARY KEY,
  data_source     text NOT NULL REFERENCES ingest.data_source(name),
  mode            text NOT NULL CHECK (mode IN ('snapshot', 'delta')),
  started_at      timestamptz NOT NULL,
  finished_at     timestamptz,
  completed       boolean NOT NULL DEFAULT false,
  record_count    integer NOT NULL DEFAULT 0,
  changed_count   integer NOT NULL DEFAULT 0,
  error_count     integer NOT NULL DEFAULT 0,
  breaker_state   text CHECK (breaker_state IN ('ok', 'warn', 'open')),
  breaker_reasons text[] NOT NULL DEFAULT '{}',
  baseline        numeric,
  notes           text
);
CREATE INDEX source_run_recent ON ingest.source_run (data_source, started_at DESC);

-- Her kaynağın her nesne için son normalleştirilmiş durumu. Değişmeyen kayıtlar (aynı content_hash)
-- uzlaştırmaya hiç girmez: günlük milyonlarca kaydın yalnızca küçük bir yüzdesi işlenir.
CREATE TABLE ingest.source_record (
  data_source  text NOT NULL REFERENCES ingest.data_source(name),
  source_key   text NOT NULL,
  class_hint   text NOT NULL,
  attributes   jsonb NOT NULL DEFAULT '{}',
  lookup       jsonb NOT NULL DEFAULT '{}',
  external_ids jsonb NOT NULL DEFAULT '{}',
  relations    jsonb NOT NULL DEFAULT '[]',
  content_hash text NOT NULL,
  raw_ref      text,
  status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'missing', 'deleted', 'quarantined')),
  first_seen   timestamptz NOT NULL DEFAULT now(),
  last_seen    timestamptz NOT NULL DEFAULT now(),
  last_changed timestamptz NOT NULL DEFAULT now(),
  last_run     uuid,
  PRIMARY KEY (data_source, source_key)
) PARTITION BY LIST (data_source);
CREATE TABLE ingest.source_record_default PARTITION OF ingest.source_record DEFAULT;
CREATE INDEX source_record_seen ON ingest.source_record (data_source, last_seen);

CREATE TABLE ingest.identity_link (
  sys_id       meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  data_source  text NOT NULL,
  source_key   text NOT NULL,
  ci_sys_id    meta.sysid NOT NULL REFERENCES cmdb.cmdb_ci(sys_id),
  method       text NOT NULL CHECK (method IN ('rule', 'score', 'manual', 'import', 'split')),
  rule_ref     text,
  probability  numeric(6, 5),
  features     jsonb NOT NULL DEFAULT '[]',
  decided_by   text NOT NULL,
  decided_at   timestamptz NOT NULL DEFAULT now(),
  active       boolean NOT NULL DEFAULT true,
  ended_at     timestamptz,
  end_reason   text,
  FOREIGN KEY (data_source, source_key) REFERENCES ingest.source_record(data_source, source_key),
  CHECK (active OR ended_at IS NOT NULL)
);
CREATE UNIQUE INDEX identity_link_active ON ingest.identity_link (data_source, source_key) WHERE active;
CREATE INDEX identity_link_ci ON ingest.identity_link (ci_sys_id) WHERE active;

CREATE TABLE ingest.match_candidate (
  sys_id        meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  data_source   text NOT NULL,
  source_key    text NOT NULL,
  reason        text NOT NULL,
  candidate_ci  meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  probability   numeric(6, 5),
  features      jsonb NOT NULL DEFAULT '[]',
  candidates    jsonb NOT NULL DEFAULT '[]',
  status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'rejected', 'created_new', 'expired')),
  priority      smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  ai_suggestion jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_by    text,
  decided_at    timestamptz,
  decision_note text,
  FOREIGN KEY (data_source, source_key) REFERENCES ingest.source_record(data_source, source_key),
  CHECK (status = 'open' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE UNIQUE INDEX match_candidate_open ON ingest.match_candidate (data_source, source_key) WHERE status = 'open';
CREATE INDEX match_candidate_queue ON ingest.match_candidate (status, priority, created_at);

-- -----------------------------------------------------------------------------
-- sync: ServiceNow eşleme durumu
-- -----------------------------------------------------------------------------
CREATE TABLE sync.sn_sync_scope (
  sys_class_name text PRIMARY KEY REFERENCES meta.sys_db_object(name),
  enabled        boolean NOT NULL DEFAULT false,
  include_descendants boolean NOT NULL DEFAULT true,
  fields         text[],          -- NULL: sözlükte sn_sync=true olan tüm alanlar
  notes          text
);

CREATE TABLE sync.sn_ci_map (
  evren_sys_id     meta.sysid PRIMARY KEY REFERENCES cmdb.cmdb_ci(sys_id),
  sn_sys_id        meta.sysid UNIQUE,
  sn_class         text,
  state            text NOT NULL DEFAULT 'pending_push' CHECK (state IN
                     ('in_sync', 'pending_push', 'pending_pull', 'conflict', 'error', 'excluded')),
  last_pushed_hash text,
  last_pushed_at   timestamptz,
  last_pulled_at   timestamptz,
  attempts         integer NOT NULL DEFAULT 0,
  last_error       text
);
CREATE INDEX sn_ci_map_pending ON sync.sn_ci_map (state) WHERE state IN ('pending_push', 'pending_pull', 'error', 'conflict');

-- -----------------------------------------------------------------------------
-- audit: değişim geçmişi (aylık bölümleme) ve güvenlik olayları
-- -----------------------------------------------------------------------------
CREATE TABLE audit.ci_change (
  id             bigint GENERATED ALWAYS AS IDENTITY,
  changed_at     timestamptz NOT NULL DEFAULT now(),
  ci_sys_id      meta.sysid NOT NULL,
  sys_class_name text NOT NULL,
  attribute      text NOT NULL,
  old_value      jsonb,
  new_value      jsonb,
  data_source    text,
  actor          text NOT NULL,
  reason         text NOT NULL CHECK (reason IN ('reconciliation', 'manual', 'merge', 'split', 'reclassification',
                   'staleness', 'import', 'schema', 'sn_pull')),
  correlation    jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (changed_at, id)
) PARTITION BY RANGE (changed_at);
CREATE TABLE audit.ci_change_default PARTITION OF audit.ci_change DEFAULT;
DO $$
DECLARE
  m date := date '2026-11-01';
BEGIN
  WHILE m < date '2028-01-01' LOOP
    EXECUTE format('CREATE TABLE audit.ci_change_%s PARTITION OF audit.ci_change FOR VALUES FROM (%L) TO (%L)',
                   to_char(m, 'YYYYMM'), m, (m + interval '1 month')::date);
    m := (m + interval '1 month')::date;
  END LOOP;
END $$;
CREATE INDEX ci_change_ci ON audit.ci_change (ci_sys_id, changed_at DESC);

CREATE TABLE audit.security_event (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  principal  text NOT NULL,
  action     text NOT NULL,
  resource   text,
  decision   text NOT NULL CHECK (decision IN ('allow', 'deny')),
  detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX security_event_at ON audit.security_event (at DESC);

-- -----------------------------------------------------------------------------
-- insight: anomaliler ve sağlık metrikleri
-- -----------------------------------------------------------------------------
CREATE TABLE insight.anomaly (
  sys_id         meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  kind           text NOT NULL CHECK (kind IN ('unauthorized_change', 'source_volume', 'mass_change', 'duplicate_spike',
                   'attribute_drift', 'coverage_gap', 'stale_population', 'relationship_break', 'other')),
  severity       text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
  ci_sys_id      meta.sysid REFERENCES cmdb.cmdb_ci(sys_id),
  data_source    text REFERENCES ingest.data_source(name),
  detected_at    timestamptz NOT NULL DEFAULT now(),
  window_start   timestamptz,
  window_end     timestamptz,
  details        jsonb NOT NULL DEFAULT '{}',
  status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved', 'false_positive')),
  related_change text,
  explanation    text,
  explanation_model text,
  assigned_group meta.sysid
);
CREATE INDEX anomaly_open ON insight.anomaly (status, severity, detected_at DESC);

CREATE TABLE insight.health_metric (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  taken_at    timestamptz NOT NULL DEFAULT now(),
  scope_class text NOT NULL,
  metric      text NOT NULL CHECK (metric IN ('completeness', 'correctness_duplicate', 'correctness_orphan',
                'correctness_stale', 'compliance', 'coverage_monitoring', 'coverage_security', 'orphan_reference')),
  numerator   bigint NOT NULL CHECK (numerator >= 0),
  denominator bigint NOT NULL CHECK (denominator >= 0),
  value       numeric GENERATED ALWAYS AS (CASE WHEN denominator > 0 THEN numerator::numeric / denominator END) STORED,
  details     jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (taken_at, id)
);

-- -----------------------------------------------------------------------------
-- iam: RBAC + ABAC + SoD
-- -----------------------------------------------------------------------------
CREATE TABLE iam.role (
  name        text PRIMARY KEY CHECK (name ~ '^[a-z][a-z0-9_]{2,63}$'),
  label_tr    text NOT NULL,
  description text,
  elevated    boolean NOT NULL DEFAULT false,
  machine     boolean NOT NULL DEFAULT false
);

CREATE TABLE iam.role_contains (
  role     text NOT NULL REFERENCES iam.role(name) ON DELETE CASCADE,
  contains text NOT NULL REFERENCES iam.role(name) ON DELETE CASCADE,
  PRIMARY KEY (role, contains),
  CHECK (role <> contains)
);

CREATE TABLE iam.permission (
  sys_id      meta.sysid PRIMARY KEY DEFAULT meta.new_sys_id(),
  role        text NOT NULL REFERENCES iam.role(name) ON DELETE CASCADE,
  resource    text NOT NULL,     -- sınıf adı (alt sınıflara kalıtılır), 'cmdb_rel_ci', 'match_queue', 'schema', '*'
  operation   text NOT NULL CHECK (operation IN ('read', 'create', 'write', 'delete', 'merge', 'split',
                'review_match', 'ingest', 'schema_draft', 'schema_approve', 'rules_admin', 'iam_admin', 'audit_read')),
  fields      text[],            -- NULL: tüm alanlar
  max_sensitivity text NOT NULL DEFAULT 'internal' CHECK (max_sensitivity IN ('public', 'internal', 'confidential', 'restricted')),
  condition   jsonb,             -- ABAC koşulu (docs/07); NULL: koşulsuz
  effect      text NOT NULL DEFAULT 'allow' CHECK (effect IN ('allow', 'deny')),
  description text
);
CREATE INDEX permission_role ON iam.permission (role);

CREATE TABLE iam.principal_role (
  principal_type text NOT NULL CHECK (principal_type IN ('user', 'group', 'service')),
  principal_id   text NOT NULL,   -- Entra oid / grup id / servis hesabı adı
  role           text NOT NULL REFERENCES iam.role(name),
  granted_by     text NOT NULL,
  granted_at     timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz,
  justification  text NOT NULL,
  PRIMARY KEY (principal_type, principal_id, role),
  CHECK (granted_by <> principal_id)
);

CREATE TABLE iam.sod_constraint (
  role_a      text NOT NULL REFERENCES iam.role(name),
  role_b      text NOT NULL REFERENCES iam.role(name),
  description text NOT NULL,
  PRIMARY KEY (role_a, role_b),
  CHECK (role_a < role_b)
);

-- Aynı principal'a çatışan iki rol doğrudan atanamaz (grup üyeliği yoluyla çatışma uygulama katmanında denetlenir).
CREATE FUNCTION iam.tg_check_sod() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  conflict text;
BEGIN
  SELECT s.description INTO conflict
    FROM iam.sod_constraint s
    JOIN iam.principal_role p
      ON p.principal_type = NEW.principal_type AND p.principal_id = NEW.principal_id
     AND (p.expires_at IS NULL OR p.expires_at > now())
   WHERE (s.role_a = NEW.role AND s.role_b = p.role) OR (s.role_b = NEW.role AND s.role_a = p.role)
   LIMIT 1;
  IF conflict IS NOT NULL THEN
    RAISE EXCEPTION 'Görevler ayrılığı ihlali: %', conflict USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER principal_role_sod BEFORE INSERT OR UPDATE ON iam.principal_role FOR EACH ROW EXECUTE FUNCTION iam.tg_check_sod();

-- -----------------------------------------------------------------------------
-- events: transactional outbox (CloudEvents → Kafka)
-- -----------------------------------------------------------------------------
CREATE TABLE events.outbox (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  aggregate_type text NOT NULL,
  aggregate_id   text NOT NULL,
  event_type     text NOT NULL,
  payload        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  published_at   timestamptz
);
CREATE INDEX outbox_unpublished ON events.outbox (id) WHERE published_at IS NULL;

COMMIT;
