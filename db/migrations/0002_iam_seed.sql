-- =============================================================================
-- Evren CMDB — rol/izin matrisi tohumları
-- Ayrıntılı açıklama ve matris görünümü: docs/07-yetkilendirme.md
-- ABAC koşul dili: {"field": "<alan>", "op": "in|eq", "value": "$user.groups|$user.sys_id|<sabit>"}
-- =============================================================================
BEGIN;

INSERT INTO iam.role (name, label_tr, description, elevated, machine) VALUES
  ('evren_reader',          'Okuyucu',                   'CMDB kayıtlarını ve ilişkileri okur (gizli/kısıtlı alanlar hariç)', false, false),
  ('evren_ci_owner',        'CI sahibi',                 'Destek grubu olduğu CI''ların yönetişim alanlarını düzenler', false, false),
  ('evren_cmdb_editor',     'Konfigürasyon analisti',    'CI ve ilişkileri elle düzenler (Manual kaynak önceliğiyle)', false, false),
  ('evren_match_reviewer',  'Eşleştirme analisti',       'Eşleştirme kuyruğunu karara bağlar; birleştirir/ayırır', false, false),
  ('evren_data_steward',    'Veri sorumlusu',            'Veri kaynakları, tanımlama/uzlaştırma kurallarını yönetir', false, false),
  ('evren_schema_designer', 'Şema tasarımcısı',          'Sınıf/alan değişiklik setlerini taslak olarak hazırlar', false, false),
  ('evren_schema_approver', 'Şema onaylayıcı',           'Şema değişiklik setlerini onaylar ve yayınlar', true, false),
  ('evren_security_reader', 'Güvenlik okuyucu',          'Kısıtlı güvenlik alanlarını (maruziyet, zafiyet) okur', false, false),
  ('evren_auditor',         'Denetçi',                   'Tüm kayıt ve denetim izlerini salt okunur görür', false, false),
  ('evren_iam_admin',       'Yetki yöneticisi',          'Rol atamalarını yönetir', true, false),
  ('evren_admin',           'Platform yöneticisi',       'Acil durum (break-glass) yetkisi; her kullanım denetlenir', true, false),
  ('evren_integration',     'Entegrasyon hesabı',        'Bağlayıcı ve dış uygulama makine hesabı: alım + okuma', false, true),
  ('evren_sn_sync',         'ServiceNow eşleme hesabı',  'ServiceNow replika eşlemesi için makine hesabı', false, true);

INSERT INTO iam.role_contains (role, contains) VALUES
  ('evren_ci_owner', 'evren_reader'),
  ('evren_cmdb_editor', 'evren_reader'),
  ('evren_match_reviewer', 'evren_reader'),
  ('evren_data_steward', 'evren_reader'),
  ('evren_schema_designer', 'evren_reader'),
  ('evren_schema_approver', 'evren_reader'),
  ('evren_security_reader', 'evren_reader');

INSERT INTO iam.permission (role, resource, operation, fields, max_sensitivity, condition, description) VALUES
  ('evren_reader', 'cmdb_ci', 'read', NULL, 'internal', NULL, 'Tüm CI sınıfları, iç kullanım alanları'),
  ('evren_reader', 'cmdb_rel_ci', 'read', NULL, 'internal', NULL, 'İlişkiler ve bağımlılık haritası'),
  ('evren_ci_owner', 'cmdb_ci', 'write',
     ARRAY['support_group','assignment_group','owned_by','managed_by','assigned_to','used_for','short_description',
           'comments','life_cycle_stage','life_cycle_stage_status'],
     'internal', '{"field": "support_group", "op": "in", "value": "$user.groups"}',
     'Yalnızca destek grubu üyesi olunan CI''lar, yalnızca yönetişim alanları'),
  ('evren_cmdb_editor', 'cmdb_ci', 'create', NULL, 'internal', NULL, NULL),
  ('evren_cmdb_editor', 'cmdb_ci', 'write', NULL, 'internal', NULL, 'Değerler Manual kaynağı olarak uzlaştırmaya girer'),
  ('evren_cmdb_editor', 'cmdb_rel_ci', 'create', NULL, 'internal', NULL, NULL),
  ('evren_cmdb_editor', 'cmdb_rel_ci', 'delete', NULL, 'internal', NULL, 'Yalnızca Manual kaynaklı ilişkiler'),
  ('evren_match_reviewer', 'match_queue', 'review_match', NULL, 'internal', NULL, NULL),
  ('evren_match_reviewer', 'cmdb_ci', 'merge', NULL, 'internal', NULL, 'Birleştirme geri alınabilir (split)'),
  ('evren_match_reviewer', 'cmdb_ci', 'split', NULL, 'internal', NULL, NULL),
  ('evren_data_steward', 'rules', 'rules_admin', NULL, 'internal', NULL, 'Tanımlama/uzlaştırma/tazeleme/eskime kuralları'),
  ('evren_data_steward', 'data_source', 'rules_admin', NULL, 'internal', NULL, NULL),
  ('evren_schema_designer', 'schema', 'schema_draft', NULL, 'internal', NULL, NULL),
  ('evren_schema_approver', 'schema', 'schema_approve', NULL, 'internal', NULL, 'Kendi hazırladığı seti onaylayamaz'),
  ('evren_security_reader', 'cmdb_ci', 'read', NULL, 'restricted', NULL, 'Defender maruziyet/zafiyet alanları dahil'),
  ('evren_auditor', '*', 'read', NULL, 'restricted', NULL, NULL),
  ('evren_auditor', '*', 'audit_read', NULL, 'restricted', NULL, 'Değişim geçmişi ve güvenlik olayları'),
  ('evren_iam_admin', 'iam', 'iam_admin', NULL, 'internal', NULL, 'Kendine rol atayamaz'),
  ('evren_admin', '*', 'read', NULL, 'restricted', NULL, 'Break-glass; oturum süresi kısıtlı'),
  ('evren_admin', '*', 'write', NULL, 'restricted', NULL, 'Break-glass; oturum süresi kısıtlı'),
  ('evren_integration', 'cmdb_ci', 'read', NULL, 'internal', NULL, NULL),
  ('evren_integration', 'ingest', 'ingest', NULL, 'internal', NULL, 'Yalnızca kendi data_source değeriyle'),
  ('evren_sn_sync', 'cmdb_ci', 'read', NULL, 'internal', NULL, NULL),
  ('evren_sn_sync', 'cmdb_ci', 'write', NULL, 'internal',
     '{"field": "$data_source", "op": "eq", "value": "ServiceNow"}', 'SN''den çekilen değişiklikler ServiceNow kaynağı olarak');

INSERT INTO iam.sod_constraint (role_a, role_b, description) VALUES
  ('evren_schema_approver', 'evren_schema_designer', 'Şema değişikliğini hazırlayan onaylayamaz'),
  ('evren_auditor', 'evren_cmdb_editor', 'Denetçi denetlediği veriyi değiştiremez'),
  ('evren_admin', 'evren_auditor', 'Platform yöneticisi kendi denetçisi olamaz'),
  ('evren_iam_admin', 'evren_schema_approver', 'Yetki yöneticisi ile şema onaylayıcı ayrı kişiler olmalı');

COMMIT;
