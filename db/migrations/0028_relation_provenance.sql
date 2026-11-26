-- 0028 — İlişki kaynağı ve yaşam döngüsü (U7.4, ADR-0031).
--
-- Bir ilişkiyi hangi kaynak kaydının bildirdiği satır başına tutulur: aynı ilişkiyi iki kaynak (ör. keşif ve
-- ServiceNow) veya iki uç bildirebilir. Uzlaştırıcı, tam (snapshot) ve devre kesicisi sağlıklı bir çalışmada kaynak
-- kaydı geldiği hâlde ilişkiyi artık listelemiyorsa o bildirimi düşürür; hiçbir kaynağın bildirmediği ilişki
-- cmdb_rel_ci'dan kalkar ve iki ucun değişim geçmişine yazılır (zaman içinde sorgu bu izden çalışır, F1-19).
-- Elle girilen ilişki "Manual" bildirimidir: keşif bırakınca kalkmaz.
--
-- Göçten önceki ilişkilerin bildirimi yoktur; kaynak onları yeniden bildirene kadar hiçbir kural onlara dokunmaz.
BEGIN;

CREATE TABLE cmdb.cmdb_rel_ci_source (
  rel_sys_id     meta.sysid NOT NULL REFERENCES cmdb.cmdb_rel_ci(sys_id) ON DELETE CASCADE,
  data_source    text NOT NULL REFERENCES ingest.data_source(name),
  source_key     text NOT NULL CHECK (length(source_key) BETWEEN 1 AND 512),
  first_reported timestamptz NOT NULL DEFAULT now(),
  last_reported  timestamptz NOT NULL DEFAULT now(),
  last_run       uuid,
  PRIMARY KEY (rel_sys_id, data_source, source_key)
);
CREATE INDEX cmdb_rel_ci_source_holder ON cmdb.cmdb_rel_ci_source (data_source, source_key);
COMMENT ON TABLE cmdb.cmdb_rel_ci_source IS
  'İlişkiyi bildiren kaynak kayıtları; bildirimi kalmayan ilişki uzlaştırıcıca kaldırılır (ADR-0031)';

-- Geriye dönük doldurma: göçten önceki ilişkilerin bildirenleri, kaynak kayıtlarının ilişki listelerinden (uzlaştırıcının
-- ilişki yazarken yaptığı çözümlemenin aynısı: iki uç da etkin kimlik bağıyla CI'a bağlı)
INSERT INTO cmdb.cmdb_rel_ci_source (rel_sys_id, data_source, source_key, first_reported, last_reported, last_run)
SELECT DISTINCT ON (r.sys_id, sr.data_source, sr.source_key)
       r.sys_id, sr.data_source, sr.source_key, coalesce(r.first_discovered, r.sys_created_on),
       coalesce(sr.last_seen, r.sys_created_on), sr.last_run
  FROM ingest.source_record sr
  JOIN ingest.identity_link hl ON hl.data_source = sr.data_source AND hl.source_key = sr.source_key AND hl.active
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(sr.relations) = 'array' THEN sr.relations
                                              ELSE '[]'::jsonb END) e
  JOIN ingest.identity_link tl ON tl.data_source = coalesce(e->>'target_data_source', sr.data_source)
                              AND tl.source_key = e->>'target_source_key' AND tl.active
  JOIN cmdb.cmdb_rel_type t ON t.name = e->>'type'
  JOIN cmdb.cmdb_rel_ci r ON r.type = t.sys_id
   AND r.parent = CASE WHEN e->>'direction' = 'parent' THEN hl.ci_sys_id ELSE tl.ci_sys_id END
   AND r.child  = CASE WHEN e->>'direction' = 'parent' THEN tl.ci_sys_id ELSE hl.ci_sys_id END
ON CONFLICT DO NOTHING;

-- Göçten önce elle girilmiş ilişkiler "Manual" bildirimini alır (elle ilişki keşif bırakınca kalkmasın)
INSERT INTO cmdb.cmdb_rel_ci_source (rel_sys_id, data_source, source_key, first_reported, last_reported)
SELECT r.sys_id, 'Manual', r.parent, coalesce(r.first_discovered, r.sys_created_on),
       coalesce(r.last_discovered, r.sys_created_on)
  FROM cmdb.cmdb_rel_ci r
 WHERE r.discovery_source = 'Manual' AND EXISTS (SELECT 1 FROM ingest.data_source d WHERE d.name = 'Manual')
ON CONFLICT DO NOTHING;

-- Zaman içinde sorgu için ilişki olaylarını CI'dan bağımsız da bulabilmek (harita bir anda birçok CI'a bakar)
CREATE INDEX IF NOT EXISTS ci_change_relation ON audit.ci_change (ci_sys_id, changed_at) WHERE attribute = 'relation';

GRANT SELECT, INSERT, UPDATE, DELETE ON cmdb.cmdb_rel_ci_source TO evren_app_reconciler;
GRANT SELECT ON cmdb.cmdb_rel_ci_source TO evren_app_api, evren_auditor_ro;

COMMIT;
