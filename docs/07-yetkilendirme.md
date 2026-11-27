# 07 · Rol ve yetki matrisi

Model: **RBAC + ABAC + alan hassasiyeti + görevler ayrılığı**. Tohum: `db/migrations/0002_iam_seed.sql`. Düzenleme F1'de arayüzdeki "Şema ve yetkiler" ekranından yapılır (F1-10).

## Boyutlar

Bir izin şu boyutlardan oluşur:

- **Rol:** Entra grubuyla eşlenir.
- **Kaynak:** sınıf adı (alt sınıflara kalıtılır), `cmdb_rel_ci`, `match_queue`, `schema`, `rules`, `iam` veya `*`.
- **İşlem:** `read`, `create`, `write`, `delete`, `merge`, `split`, `review_match`, `ingest`, `schema_draft`, `schema_approve`, `rules_admin`, `iam_admin`, `audit_read`, `connector_read`, `connector_admin` (0018: bağlayıcı kimlikleri ve çalışmayı açan kimlik; sır hiçbir zaman gösterilmez. 0019, ADR-0021: `connector_read` bağlayıcı örneklerini ve çalıştırma isteklerini de gösterir; `connector_admin` ile "şimdi çalıştır", istek iptali, kimlik verme — belirteç bir kez — ve kimlik iptali), `create` (`cmdb_ci` ve alt sınıfları; ADR-0026: elle CI oluşturma isteği, Konfigürasyon analisti; kök sınıfta açılmaz), `attest` (0025, ADR-0027: CI sahibi rolü; izin tek başına yetmez, kişi CI'ın `owned_by`/`managed_by` alanındaki kullanıcı olmalıdır — kendi CI'ını onaylar veya gerekçeyle reddeder; Veri sorumlusu başkası adına onay veremez), `retire` (0026, ADR-0028: Veri sorumlusu eskime önerisinde CI'ı gerekçeyle emekliye ayırır veya korur; emekliye ayırma yalnızca aday aşamasında; CI silinmez), `anomaly_review` (0021, ADR-0025: Veri sorumlusu yetkisiz değişiklik anomalisini "incelendi" veya gerekçeli "yanlış alarm" olarak işaretler ya da yeniden açar; "kapandı" durumunu yalnızca uzlaştırıcı verir; karar `audit.security_event`'e yazılır).
- **Alanlar:** liste; NULL tüm alanlar demektir.
- **En yüksek hassasiyet:** `public` < `internal` < `confidential` < `restricted`. Alanın `sys_dictionary.sensitivity` değeriyle karşılaştırılır.
  Kısıtlı güvenlik duruşu (`cmdb.ci_security_posture`, ADR-0023) CI ayrıntısında yalnızca sınıf için `read` yetkisi `restricted` olan rollere (Güvenlik okuyucu, Denetçi, break-glass) döner; her gösterim `audit.security_event`'e `restricted_read` olarak yazılır.
- **Koşul (ABAC):** satır düzeyi. Örnek: `{"field": "support_group", "op": "in", "value": "$user.groups"}`.
- **Etki:** `allow` / `deny`. Çakışmada `deny` kazanır.

## Matris (tohum)

| Rol | CI okuma | CI yazma | İlişki | Eşleştirme | Kurallar | Şema | IAM | Denetim izi | Kısıtlı alan | Bağlayıcılar | Sistem durumu |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Okuyucu | ✓ | — | okuma | — | — | — | — | — | — | — | — |
| CI sahibi | ✓ | yönetişim alanları, **yalnızca kendi destek grubu** | okuma | — | — | — | — | — | — | — | — |
| Konfigürasyon analisti | ✓ | ✓ (Manual kaynağı) | oluştur/sil (Manual) | — | — | — | — | — | — | — | — |
| Eşleştirme analisti | ✓ | — | okuma | karar, birleştir, ayır | — | — | — | — | — | — | — |
| Veri sorumlusu | ✓ | — | okuma | — | ✓ | — | — | — | — | görme + yönetim | ✓ |
| Şema tasarımcısı | ✓ | — | — | — | — | taslak | — | — | — | — | — |
| Şema onaylayıcı | ✓ | — | — | — | — | onay/yayın | — | — | — | — | — |
| Güvenlik okuyucu | ✓ | — | okuma | — | — | — | — | — | ✓ | — | — |
| Denetçi | ✓ (tümü) | — | okuma | — | — | — | — | ✓ | ✓ | görme | ✓ |
| Yetki yöneticisi | — | — | — | — | — | — | ✓ | — | — | — | — |
| Platform operatörü | — | — | — | — | — | — | — | — | — | — | ✓ |
| Platform yöneticisi (break-glass) | ✓ | ✓ | ✓ | — | — | — | — | — | ✓ | — | ✓ |
| Entegrasyon hesabı (makine) | ✓ | — | — | — | — | — | — | — | — | — | — |
| SN eşleme hesabı (makine) | ✓ | ServiceNow kaynağı olarak | — | — | — | — | — | — | — | — | — |

## Görevler ayrılığı (veritabanında zorlanır)

| Rol A | Rol B | Gerekçe |
| --- | --- | --- |
| Şema tasarımcısı | Şema onaylayıcı | Hazırlayan onaylayamaz |
| Denetçi | Konfigürasyon analisti | Denetçi denetlediği veriyi değiştiremez |
| Platform yöneticisi | Denetçi | Kendi denetçisi olamaz |
| Yetki yöneticisi | Şema onaylayıcı | Yetki ve şema kontrolü ayrı kişilerde |

Ek kurallar:

- Kimse kendine rol veremez (`granted_by <> principal_id`).
- Şema setinde `approved_by <> created_by`.
- Rol atamaları süreli olabilir (`expires_at`) ve gerekçe zorunludur.
- Her değişiklik `audit.security_event`'e yazılır.
- Grup üyeliği yoluyla oluşan SoD çatışmaları uygulama katmanında ve periyodik raporla denetlenir.

## Break-glass

Platform yöneticisi rolü yalnızca acil durumda, süreli ve gerekçeli atanır. Her kullanımı SIEM'de alarm üretir ve sonradan gözden geçirilir.

## Entra eşlemesi

- Entra grupları `iam.principal_role` içinde `principal_type='group'` ile rollere bağlanır.
- Kullanıcı oturumunda grup üyelikleri token'dan veya Graph'tan alınır.
- Rol hesabı istek başına önbelleklenir (kısa TTL).
