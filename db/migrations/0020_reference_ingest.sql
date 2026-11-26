-- 0020 — ServiceNow referans verilerinin alım yolu (F0-08, ADR-0022).
--
-- ServiceNow bağlayıcısının çektiği referans tabloları (grup, kişi, konum, şirket, departman, maliyet merkezi, model)
-- geçit ve Kafka üzerinden uzlaştırıcıya gelir; uzlaştırıcı aynı sys_id ile ref.<tablo>'ya yazar. Kimlik çözümlemesine
-- ve CI uzlaştırmasına girmez.
--
-- Uzlaştırıcıya ref şemasında yalnızca sütun düzeyi yetki verilir; sütunlar evren_core.reference.REFERENCE_TABLES ile
-- aynıdır (testli). Yazılamayanlar: source (satırın kaynağı; Entra/Evren satırı ezilmez), entra_oid, entra_group_id,
-- grup üyeliği (sys_user_grmember) ve diğer ref tabloları (değişiklik kayıtları). Silme yetkisi yoktur: silinen kayıt
-- yalnızca pasifleşir (active = false).
BEGIN;

GRANT USAGE ON SCHEMA ref TO evren_app_reconciler;
GRANT INSERT (sys_id, user_name, name, email, department, active, sys_updated_on) ON ref.sys_user TO evren_app_reconciler;
GRANT UPDATE (user_name, name, email, department, active, sys_updated_on) ON ref.sys_user TO evren_app_reconciler;
GRANT INSERT (sys_id, name, manager, email, active, sys_updated_on) ON ref.sys_user_group TO evren_app_reconciler;
GRANT UPDATE (name, manager, email, active, sys_updated_on) ON ref.sys_user_group TO evren_app_reconciler;
GRANT INSERT (sys_id, name, full_name, parent, city, country, sys_updated_on) ON ref.cmn_location TO evren_app_reconciler;
GRANT UPDATE (name, full_name, parent, city, country, sys_updated_on) ON ref.cmn_location TO evren_app_reconciler;
GRANT INSERT (sys_id, name, manufacturer, vendor, sys_updated_on) ON ref.core_company TO evren_app_reconciler;
GRANT UPDATE (name, manufacturer, vendor, sys_updated_on) ON ref.core_company TO evren_app_reconciler;
GRANT INSERT (sys_id, name, company, sys_updated_on) ON ref.cmn_department TO evren_app_reconciler;
GRANT UPDATE (name, company, sys_updated_on) ON ref.cmn_department TO evren_app_reconciler;
GRANT INSERT (sys_id, name, account_number, manager, parent, active, sys_updated_on) ON ref.cmn_cost_center TO evren_app_reconciler;
GRANT UPDATE (name, account_number, manager, parent, active, sys_updated_on) ON ref.cmn_cost_center TO evren_app_reconciler;
GRANT INSERT (sys_id, name, display_name, manufacturer, model_number, sys_updated_on) ON ref.cmdb_model TO evren_app_reconciler;
GRANT UPDATE (name, display_name, manufacturer, model_number, sys_updated_on) ON ref.cmdb_model TO evren_app_reconciler;

COMMIT;
