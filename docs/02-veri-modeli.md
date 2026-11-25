# 02 · Veri modeli

## ServiceNow uyumu: neler aynı?

- Tablo ve sınıf adları (`cmdb_ci`, `cmdb_ci_linux_server`, `cmdb_rel_ci`, `cmdb_rel_type`).
- `sys_id` (32 haneli küçük harf hex), `sys_class_name` ve `sys_*` sistem alanları.
- Seçim değerleri (`operational_status`, `install_status`). ServiceNow'daki yazım hataları dahil alan adları korunur (`busines_criticality`).
- Kalıtım: bir sınıfın alanları alt sınıflara geçer. Alt sınıf üst sınıfın alanını yeniden tanımlayamaz.
- CSDM 5 sınıfları (iş uygulaması, servis örnekleri, servis sunumu) modele dahildir.

**ServiceNow kökenli CI'lar aynı sys_id'yi korur.** Böylece task tarafı taşındığında `task.cmdb_ci` referansları dönüşümsüz çözülür.

## Fiziksel tasarım: hibrit şema

| Katman | Nerede | Ne zaman |
| --- | --- | --- |
| Çekirdek alanlar (35) | `cmdb.cmdb_ci` fiziksel kolonları | Tüm sınıflarda ortak, sık sorgulanan |
| Sınıfa özgü alanlar | `cmdb.cmdb_ci.class_attributes` (JSONB) | `ram`, `cpu_count`, `can_route`… |
| Sık sorgulanan JSONB alanı | `indexed: true` → ifade indeksi | `bios_uuid`, `vcenter_uuid`, `ram` |

- Sözlük (`meta.sys_dictionary`) her alanın tipini, etiketini, hassasiyetini ve ServiceNow'a gidip gitmeyeceğini (`sn_sync`) tutar.
- `cmdb.tg_ci_validate` tetikleyicisi sözlükte olmayan veya yanlış tipteki JSONB alanını reddeder. Kardeş sınıfın alanı da kabul edilmez.
- **Yeni alan veya sınıf DDL gerektirmez.** Sözlüğe eklenir; `sn.<sınıf>` görünümü yeniden üretilir.
- `sn` şemasındaki görünümler (`sn.cmdb_ci_server`) ServiceNow tablo adlarıyla tipli kolonlar sunar. Görünüm alt sınıfları da kapsar; ServiceNow'daki tablo kalıtımıyla aynı davranır.
- Kalıtım kapanışı `meta.class_closure` tablosundadır. `INSTANCEOF` sorguları ve doğrulama bunu kullanır.

## Kaynak ve kimlik katmanı

| Tablo | İçerik |
| --- | --- |
| `ingest.data_source` | Kaynak tanımı, devre kesici eşikleri |
| `ingest.source_run` | Her çalışma: kayıt/değişim/hata sayısı, devre kesici durumu |
| `ingest.source_record` | Her kaynağın her nesne için son normalleştirilmiş durumu (kaynağa göre bölümlü) |
| `ingest.identity_link` | Kaynak kaydı → CI bağı; yöntem, olasılık, kanıtlar, karar veren. Geri alınabilir |
| `ingest.match_candidate` | Eşleştirme kuyruğu; adaylar, açıklama, yapay zekâ önerisi |
| `cmdb.ci_reconciliation_state` | Öznitelik başına kazanan kaynak, gözlem zamanı, gerekçe |

## İlişkiler

- `cmdb.cmdb_rel_ci (parent, child, type)` + `cmdb.cmdb_rel_type ("Runs on::Runs")`.
- VMware modeli ServiceNow'daki gibidir:
  - Misafir sunucu → `Virtualized by::Virtualizes` → VM örneği.
  - VM örneği → `Registered on::Has registered` → ESXi.
- Etki analizi derinlik sınırlı özyinelemeli sorguyla yapılır. Büyük servis haritaları için önbellekli görünüm F1'de değerlendirilecek.

## Referans veriler

`ref.sys_user`, `ref.sys_user_group`, `ref.cmn_location`, `ref.core_company`, `ref.cmn_department` ve `ref.cmdb_model` ServiceNow'dan aynı sys_id'lerle yansıtılır.

- CI üzerindeki referans alanları yumuşak referanstır; ServiceNow'da da yabancı anahtar yoktur.
- Kopuk referanslar CMDB sağlığında "yetim referans" metriği olarak izlenir.

## Yüksek hacimli tablolar

| Tablo | Tahmini satır | Strateji |
| --- | --- | --- |
| `cmdb.cmdb_ci` | 3–5 M | Sınıf, ad, seri, IP, durum indeksleri; trigram ad indeksi |
| `cmdb.cmdb_rel_ci` | 10–20 M | `(parent, child, type)` benzersiz, `(child, type)` indeksi |
| `cmdb.cmdb_sam_sw_install` | 8–10 M | 16 parçalı hash bölümleme |
| `ingest.source_record` | 5–10 M | Kaynağa göre liste bölümleme |
| `audit.ci_change` | ~100 M/yıl | Aylık aralık bölümleme; saklama süresi dolan bölüm DBA tarafından ayrılır |

## Hangi şey CI, hangisi ilişkili kayıt?

Ölçeği belirleyen asıl karar budur:

- **Switch portu:** başlangıçta CI değil, ağ cihazına bağlı ilişkili kayıt. Bağlantı haritası NNMi L2 verisinden `Connects to::Connected by` ilişkisiyle kurulur.
- **Yazılım kurulumu:** CI değil (`cmdb_sam_sw_install`).
- **Sunucu ağ adaptörü:** CI (`cmdb_ci_network_adapter`), ServiceNow'daki gibi.
- **IP adresi:** CI (`cmdb_ci_ip_address`), ServiceNow'daki gibi.

## Şema değişikliği yönetişimi

Kaynak tabloları: `meta.schema_change_set` ve `meta.schema_change`.

1. Taslak: Şema ve Değişiklikler sayfasında hazırlanır; işlemler kaydedilmeden önce tüm metadata kurallarıyla doğrulanır.
2. Onaya gönderme ve etki analizi: etkilenen CI sayısı, değerli CI sayısı (kullanımdan kaldırmada), kaynağın tek başına verdiği öznitelikler (emeklilikte).
3. Onay. Hazırlayan onaylayamaz ve reddedemez; API, veritabanı kısıtı ve tetikleyiciyle zorlanır.
4. Yayın: `metadata/changes/` altına konacak değişiklik dosyası (SHA-256). PR ile repoya girer; yükleyici temel dosyalardan sonra ad sırasıyla uygular (ADR-0015).

İşlemler: sınıf ekle (`u_`), alan ekle (`u_`, JSONB), alan etiketi/hassasiyeti/uzunluğu, alanı kullanımdan kaldır, seçim değeri ekle, veri kaynağı ekle (gölge), yaşam döngüsü değiştir.

Alan silme şu sırayla yapılır: `deprecate_field` → arayüzden gizleme → saklama süresi → `drop_field`.
