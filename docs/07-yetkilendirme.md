# 07 · Rol ve yetki matrisi

Model: **RBAC + ABAC + alan hassasiyeti + görevler ayrılığı**. Tohum: `db/migrations/0002_iam_seed.sql`. Düzenleme F1'de arayüzdeki "Şema ve yetkiler" ekranından yapılır (F1-10).

## Boyutlar

Bir izin şu boyutlardan oluşur:

- **Rol:** Entra grubuyla eşlenir.
- **Kaynak:** sınıf adı (alt sınıflara kalıtılır), `cmdb_rel_ci`, `match_queue`, `schema`, `rules`, `iam` veya `*`.
- **İşlem:** `read`, `create`, `write`, `delete`, `merge`, `split`, `review_match`, `ingest`, `schema_draft`, `schema_approve`, `rules_admin`, `iam_admin`, `audit_read`, `connector_read`, `connector_admin` (0018: bağlayıcı kimlikleri ve çalışmayı açan kimlik; sır hiçbir zaman gösterilmez).
- **Alanlar:** liste; NULL tüm alanlar demektir.
- **En yüksek hassasiyet:** `public` < `internal` < `confidential` < `restricted`. Alanın `sys_dictionary.sensitivity` değeriyle karşılaştırılır.
- **Koşul (ABAC):** satır düzeyi. Örnek: `{"field": "support_group", "op": "in", "value": "$user.groups"}`.
- **Etki:** `allow` / `deny`. Çakışmada `deny` kazanır.

## Matris (tohum)

| Rol | CI okuma | CI yazma | İlişki | Eşleştirme | Kurallar | Şema | IAM | Denetim izi | Kısıtlı alan | Bağlayıcılar |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Okuyucu | ✓ | — | okuma | — | — | — | — | — | — | — |
| CI sahibi | ✓ | yönetişim alanları, **yalnızca kendi destek grubu** | okuma | — | — | — | — | — | — | — |
| Konfigürasyon analisti | ✓ | ✓ (Manual kaynağı) | oluştur/sil (Manual) | — | — | — | — | — | — | — |
| Eşleştirme analisti | ✓ | — | okuma | karar, birleştir, ayır | — | — | — | — | — | — |
| Veri sorumlusu | ✓ | — | okuma | — | ✓ | — | — | — | — | görme + yönetim |
| Şema tasarımcısı | ✓ | — | — | — | — | taslak | — | — | — | — |
| Şema onaylayıcı | ✓ | — | — | — | — | onay/yayın | — | — | — | — |
| Güvenlik okuyucu | ✓ | — | okuma | — | — | — | — | — | ✓ | — |
| Denetçi | ✓ (tümü) | — | okuma | — | — | — | — | ✓ | ✓ | görme |
| Yetki yöneticisi | — | — | — | — | — | — | ✓ | — | — | — |
| Platform yöneticisi (break-glass) | ✓ | ✓ | ✓ | — | — | — | — | — | ✓ | — |
| Entegrasyon hesabı (makine) | ✓ | — | — | — | — | — | — | — | — | — |
| SN eşleme hesabı (makine) | ✓ | ServiceNow kaynağı olarak | — | — | — | — | — | — | — | — |

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
