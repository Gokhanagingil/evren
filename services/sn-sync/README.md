# services/sn-sync — ServiceNow eşleme (F0-08 ilk yükleme, F1-12 çekme, F2-01 itme)

## 1. İlk yükleme (F0-08)

- ServiceNow CMDB'deki `cmdb_ci` soyağacı ve referans tabloları **aynı sys_id ile** Evren'e alınır:
  - `sys_user`, `sys_user_group`, `sys_user_grmember`, `cmn_location`, `core_company`, `cmn_department`, `cmdb_model`, `cmdb_rel_ci`.
- Golden record'a doğrudan yazılmaz. Her kayıt `data_source='ServiceNow'` kaynak kaydı olarak girer; kimlik bağı `method='import'` ile kurulur.
- Bu fazda ServiceNow'a **hiçbir yazma yapılmaz** (gölge mod).

## 2. Çekme (F1-12)

- `sys_updated_on > son_çekme` ile artımlı okuma.
- ServiceNow'da elle değiştirilen yönetişim alanları uzlaştırmaya "ServiceNow" kaynağı olarak girer.
- ServiceNow tarafında business rule gerekmez.

## 3. İtme (F2-01)

- Kapsam: `sync.sn_sync_scope` tablosu (sınıf + alan listesi). Yalnızca süreçlerin ihtiyaç duyduğu sınıflar.
- Yol: ServiceNow IRE API `POST /api/now/identifyreconcile`.
  - ServiceNow tarafında "Evren" veri kaynağı ve kapsamdaki alanlar için en yüksek öncelikli uzlaştırma kuralı tanımlanır.
- Yeni CI için ServiceNow'un verdiği sys_id `sync.sn_ci_map`'e yazılır. İçe aktarılmış CI'larda iki sys_id zaten aynıdır.
- Aynı içerik tekrar gönderilmez (`last_pushed_hash`). Hata durumunda üstel geri çekilmeyle yeniden denenir.

## Güvenlik

- Ayrı ServiceNow entegrasyon kullanıcısı, en az yetki: okuma + IRE.
- OAuth belirteci dosyadan okunur. İstek ve yanıt gövdeleri loglanmaz.

## Açık konu

ITOM abonelik birimlerinin üçüncü taraf kaynaklı CI'larda nasıl sayıldığı ServiceNow ile teyit edilecek (F2-04).
