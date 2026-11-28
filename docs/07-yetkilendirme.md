# 07 · Rol ve yetki matrisi

Model: **RBAC + ABAC + alan hassasiyeti + görevler ayrılığı**. Tohum: `db/migrations/0002_iam_seed.sql`. Düzenleme F1'de arayüzdeki "Şema ve yetkiler" ekranından yapılır (F1-10).

## Boyutlar

Bir izin şu boyutlardan oluşur:

- **Rol:** Entra grubuyla eşlenir.
- **Kaynak:** sınıf adı (alt sınıflara kalıtılır), `cmdb_rel_ci`, `match_queue`, `schema`, `rules`, `iam` veya `*`.
- **İşlem:** `read`, `create`, `write`, `delete`, `merge`, `split`, `review_match`, `ingest`, `schema_draft`, `schema_approve`, `rules_admin`, `iam_admin`, `audit_read`, `audit_review` (0034, ADR-0039: mühür doğrulama ve dönem incelemesi kaydı; Denetçi), `ops_admin` (0035, ADR-0040: `platform` kaynağında; platform uyarısını gerekçeyle onaylama, bildirim yönlendirmesi, sınama bildirimi; Platform operatörü ve Platform yöneticisi), `replica_admin` (0036, ADR-0042: `platform` kaynağında; ServiceNow replikasının modu — kapalı/gölge/etkin —, kapsamı, bekletilen toplu itmenin onayı ve çelişki kararı; Konfigürasyon yöneticisi ve Platform yöneticisi; replikayı `platform` okuma izni olanlar da görür), `connector_read`, `connector_admin` (0018: bağlayıcı kimlikleri ve çalışmayı açan kimlik; sır hiçbir zaman gösterilmez. 0019, ADR-0021: `connector_read` bağlayıcı örneklerini ve çalıştırma isteklerini de gösterir; `connector_admin` ile "şimdi çalıştır", istek iptali, kimlik verme — belirteç bir kez — ve kimlik iptali), `create` (`cmdb_ci` ve alt sınıfları; ADR-0026: elle CI oluşturma isteği, Konfigürasyon analisti; kök sınıfta açılmaz), `attest` (0025, ADR-0027: CI sahibi rolü; izin tek başına yetmez, kişi CI'ın `owned_by`/`managed_by` alanındaki kullanıcı olmalıdır — kendi CI'ını onaylar veya gerekçeyle reddeder; Veri sorumlusu başkası adına onay veremez), `retire` (0026, ADR-0028: Veri sorumlusu eskime önerisinde CI'ı gerekçeyle emekliye ayırır veya korur; emekliye ayırma yalnızca aday aşamasında; CI silinmez), `anomaly_review` (0021, ADR-0025: Veri sorumlusu yetkisiz değişiklik anomalisini "incelendi" veya gerekçeli "yanlış alarm" olarak işaretler ya da yeniden açar; "kapandı" durumunu yalnızca uzlaştırıcı verir; karar `audit.security_event`'e yazılır).
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
| Konfigürasyon yöneticisi | ✓ | — | okuma | — | — | — | — | — | — | — | — |
| Platform yöneticisi (break-glass) | ✓ | ✓ | ✓ | — | — | — | — | — | ✓ | — | ✓ |
| Entegrasyon hesabı (makine) | ✓ | — | — | — | — | — | — | — | — | — | — |
| sn-sync (makine, ServiceNow replikası) | ✓ | — (yalnızca eşleme ve çalışma kaydı; ServiceNow'a IRE ile yazar) | okuma | — | — | — | — | — | — | — | — |

## Görevler ayrılığı (veritabanında zorlanır)

| Rol A | Rol B | Gerekçe |
| --- | --- | --- |
| Şema tasarımcısı | Şema onaylayıcı | Hazırlayan onaylayamaz |
| Denetçi | Konfigürasyon analisti | Denetçi denetlediği veriyi değiştiremez |
| Platform yöneticisi | Denetçi | Kendi denetçisi olamaz |
| Yetki yöneticisi | Şema onaylayıcı | Yetki ve şema kontrolü ayrı kişilerde |
| Denetçi | Konfigürasyon yöneticisi | Denetçi denetlediği yönetişim sürecini yönetemez |

Yapay zekâ önerilerini (ADR-0043) `cmdb_ci` okuma izni olan herkes (ve MCP ajanı) ekleyebilir; öneri Evren'e yazmaz. Kararı, uygulanacak işlemin yetkisi olan kişi verir: CI değişikliğinde ilgili sınıfa yazma (Konfigürasyon analisti), eşleştirme kararında `review_match` (Eşleştirme analisti). Öneren kendi önerisine karar veremez (veritabanı kısıtı). MCP araçları çağıranın rolleriyle çalışır.

ServiceNow replikasını (ADR-0042) `platform` okuma izni olanlar ve Konfigürasyon yöneticisi ile Platform yöneticisi görür; mod, kapsam, toplu itme onayı ve çelişki kararı `replica_admin` ister (bu iki rol). sn-sync kendi itmesini onaylayamaz (sütun yetkisi).

Bildirimleri (ADR-0040) herkes kendine, grubuna ve rollerine gelenlerle görür; okundu bilgisi kişi başınadır. Platform uyarılarını ve bildirim yönlendirmesini `platform` okuma izni olanlar görür (Platform operatörü, Veri sorumlusu, Denetçi); onay ve yönlendirme `ops_admin` ister.

Denetim izini (ADR-0039) yalnızca Denetçi görür (`audit` kaynağında `audit_read`); mühür doğrulama ve dönem incelemesi kaydı `audit_review` ister (0034, Denetçi). Platform yöneticisi ve Konfigürasyon yöneticisi denetim izini göremez (görevler ayrılığı). Okuma denetlenmez; dışa aktarma, doğrulama ve inceleme denetim olayıdır.

Konfigürasyon yöneticisi yönetişim görevlerini tanımlar (`governance` kaynağında `task_admin`; U8.4–U8.6): görev açar ve iptal eder, bütün görevleri ve görev kanallarını yönetir, zamanlanmış kampanya tanımlar ve yürütür (ADR-0036; durum değişikliği ve "şimdi çalıştır" gerekçe ister), kalite ve uyum kuralı tanımlar, bulguya istisna verir veya kaldırır (ADR-0037). CMDB sağlık panosunu, kuralları ve bulguları CI okuma izni olan herkes görür. Herkes kendine ve grubuna atanan görevi görür ve kararını verir; Denetçi bütün görevleri ve kampanyaları salt okunur görür (ADR-0035, ADR-0036). Rol–yetki matrisinin canlı hâli uygulamada "Yönetim → Kullanıcılar ve Roller → Roller ve yetkiler" sekmesindedir (ADR-0034).

Ek kurallar:

- Kimse kendine veya üyesi olduğu gruba rol veremez, kendi rolünü geri alamaz (`granted_by <> principal_id`; API "veren" alanını kişinin ham kimliğiyle yazar, böylece kural veritabanında da işler).
- Şema setinde `approved_by <> created_by`.
- Rol atamaları süreli olabilir (`expires_at`) ve gerekçe zorunludur (en az 10 karakter). Yetkili roller süreli verilir: Yetki yöneticisi ve Şema onaylayıcı en çok 90 gün, Platform yöneticisi (break-glass) en çok 1 gün. Makine rolleri kişiye veya gruba verilmez.
- Geri alma da gerekçelidir; pasifleştirilen kişinin doğrudan rolleri geri alınır.
- Her değişiklik `audit.security_event`'e yazılır.
- Grup üyeliği yoluyla oluşan SoD çatışmaları uygulama katmanında ve periyodik raporla denetlenir.

## Break-glass

Platform yöneticisi rolü yalnızca acil durumda, süreli ve gerekçeli atanır. Her kullanımı SIEM'de alarm üretir ve sonradan gözden geçirilir.

## Entra eşlemesi

- Entra grupları `iam.principal_role` içinde `principal_type='group'` ile, grup kimliğiyle (`ref.sys_user_group.entra_group_id`) rollere bağlanır.
- Kişiye verilen rol Entra nesne kimliğiyle (oid) eşlenir; e-posta ve oturum adı değişebildiği için kullanılmaz.
- Oturum çerezinde yalnızca Entra uygulama rollerinden gelen Evren rolleri ve rol verilmiş gruplar bulunur. `iam.principal_role`'deki roller **her istekte** okunur: verilen rol yeniden girişsiz geçerli olur, geri alınan veya süresi dolan rol hemen düşer (ADR-0034). Yeni grup ataması yeniden girişte görülür (çerez sınırı).
- Görevler ayrılığı girişte ve her istekte denetlenir; çatışan atama oturumu durdurur.
- Entra'dan kişi ve grup eşitlemesi (Graph) hedef ortamla gelecek (U10); o zamana kadar Entra kimliği "Kullanıcılar ve Roller" ekranında elle bağlanır.
