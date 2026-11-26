# 02 · Veri modeli

## ServiceNow uyumu: neler aynı?

- Tablo ve sınıf adları (`cmdb_ci`, `cmdb_ci_linux_server`, `cmdb_rel_ci`, `cmdb_rel_type`).
- `sys_id` (32 haneli küçük harf hex), `sys_class_name` ve `sys_*` sistem alanları.
- Seçim değerleri (`operational_status`, `install_status`). ServiceNow'daki yazım hataları dahil alan adları korunur (`busines_criticality`).
- Kalıtım: bir sınıfın alanları alt sınıflara geçer. Alt sınıf üst sınıfın alanını yeniden tanımlayamaz.
- CSDM 5 sınıfları modele dahildir: iş yeteneği, iş uygulaması, iş süreci, bilgi nesnesi, iş/teknik servis ve sunumları, uygulama servisi, dinamik CI grubu.

**ServiceNow kökenli CI'lar aynı sys_id'yi korur.** Böylece task tarafı taşındığında `task.cmdb_ci` referansları dönüşümsüz çözülür.

## Sınıf kütüphanesi (ADR-0019)

82 sınıf, ServiceNow temel adlarıyla. Tam liste `metadata/classes.yaml`; sürüme göre değişebilenler `verify: true`.

| Dal | Sınıflar (örnek) | Kimlik ailesi |
| --- | --- | --- |
| Donanım | sunucu (Linux, Windows, UNIX, AIX, Solaris, ESXi, NAS), kişisel bilgisayar, yazıcı, kabin, ağ cihazı (switch, router, güvenlik duvarı, yük dengeleyici, kablosuz erişim noktası), depolama cihazı (disk, birim) | `cmdb_ci_hardware` |
| Sanallaştırma | VM örneği, VMware VM, vCenter, veri merkezi, küme, datastore | `cmdb_ci_vm_object` |
| Yazılım | uygulama, uygulama sunucusu (Tomcat, JBoss, WebLogic, WebSphere), web sunucusu (Apache, IIS, Nginx), veritabanı örneği (MSSQL, Oracle, MySQL, PostgreSQL, DB2) | `cmdb_ci_appl` |
| Mantıksal ağ | IP ağı, VLAN, DNS adı, IP adresi, ağ adaptörü | her biri kendi dalı |
| Küme ve konteyner | küme (Windows, Kubernetes), küme düğümü, konteyner (Docker), Kubernetes bileşenleri (ad alanı, düğüm, dağıtım, pod, servis) | `cmdb_ci_cluster`, `cmdb_ci_kubernetes_component`… |
| Bulut | bulut hizmet hesabı, mantıksal veri merkezi (Azure, AWS bölgesi) | kendi dalı |
| Diğer | dosya sistemi, veritabanı kataloğu | kendi dalı |
| CSDM | iş yeteneği, iş süreci, bilgi nesnesi, iş uygulaması, servis ve sunumlar, uygulama servisi (haritalanmış, hesaplanan, etiket tabanlı), dinamik CI grubu | `cmdb_ci_service`, `cmdb_ci_business_app`… |

Eşleştirme adayları aile içinde aranır. Aile listesinde olmayan bir dal, `cmdb_ci` altındaki ilk sınıfıyla kendi ailesini oluşturur. Böylece "prod" adlı bir Kubernetes ad alanı ile "prod" adlı bir IP ağı birbirinin adayı olmaz.

## Yaşam döngüsü (CSDM 5)

| Aşama | Durumlar | Eski alanlar (`operational_status` / `install_status`) |
| --- | --- | --- |
| Fikir | Taslak | Planlanan / — |
| Tasarım | Planlanıyor | Planlanan / — |
| Yapım | Yapılıyor | Planlanan / Kurulum bekliyor |
| Devreye alma | Hazır | Hazır / Kurulu |
| Operasyonel | Kullanımda, Bakımda, Emeklilik bekliyor | Çalışıyor·Onarımda / Kurulu·Bakımda |
| Ömür sonu | Emekli, Kayıp, Çalıntı, İmha edildi | Emekli·Çalışmıyor / Emekli·Bulunamıyor·Çalıntı |

- Durum aşamaya bağımlıdır (`sys_choice.dependent_value`); başka aşamanın durumu seçilemez.
- Her durum, eski iki alanda taşınması gereken değeri bilir (`legacy`). ServiceNow replikası ve eski raporlar bu alanları okur. Evren tutarsızlığı bu eşlemeden denetler (U7).
- ServiceNow'da bu iki alan referans tablosudur; replika değeri ilgili kaydın sys_id'sine çevirir.

## Sahiplik ve yönetişim alanları

| Alan | Anlamı | Kim yazar |
| --- | --- | --- |
| `owned_by`, `managed_by`, `support_group`, `assignment_group`, `change_control` | Sahip, yöneten, destek, atama ve onay grubu | Evren (elle) ve geçişte ServiceNow |
| `cost_center` → `cmn_cost_center` | Maliyet merkezi | Evren (elle) ve geçişte ServiceNow |
| `attested`, `attested_by`, `attested_date` | Sahiplik onayı: CI sahibinin "bu kayıt doğru ve benim" beyanı | Yalnızca Evren'deki onay akışı (U6, F1-17) |

Keşif ve izleme araçları bu alanları yazamaz (kural 5, `reconciliation_rules.yaml`). Bir aracın denemesi golden record'u değiştirmez, kaynak bilgisinde "yetkisiz" olarak görünür.

## Fiziksel tasarım: hibrit şema

| Katman | Nerede | Ne zaman |
| --- | --- | --- |
| Çekirdek alanlar (39) | `cmdb.cmdb_ci` fiziksel kolonları | Tüm sınıflarda ortak, sık sorgulanan; kök alan eklemek göç ister (0001 + sonraki `ADD COLUMN`) |
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

### İzinli ilişki kuralları (ADR-0019)

ServiceNow hangi sınıfın hangi sınıfa hangi tiple bağlanacağını yalnızca önerir. Evren bunu denetler: `metadata/relationship_rules.yaml` → `meta.cmdb_rel_rule`.

- Kural `parent --type--> child` biçimindedir ve alt sınıflara kalıtılır. `cmdb_ci_appl Runs on cmdb_ci_computer` kuralı Tomcat → Linux sunucu ilişkisini de kapsar.
- `cmdb_ci Depends on::Used by cmdb_ci` her düzeyde genel bağımlılıktır (CSDM).
- Karar üç değerlidir:
  - **izinli**;
  - **yön ters**: "sunucu Runs on uygulama" yazılmış, tersi izinli;
  - **izinsiz**.
- Zorlama kipi `off`, `warn` veya `enforce` olabilir. Bugün `warn`; golden record'a yazımda uygulanması U7'dedir. Bağlayıcı eşlemeleri ve demo verisi bu kurallarla test edilir.

## Referans veriler

`ref.sys_user`, `ref.sys_user_group`, `ref.cmn_location`, `ref.core_company`, `ref.cmn_department`, `ref.cmdb_model` ve `ref.cmn_cost_center` ServiceNow'dan aynı sys_id'lerle yansıtılır.

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
