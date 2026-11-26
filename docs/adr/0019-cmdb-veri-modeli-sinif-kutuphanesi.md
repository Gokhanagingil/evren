# ADR-0019: CMDB veri modeli — ServiceNow uyumlu sınıf kütüphanesi, CSDM 5 yaşam döngüsü ve denetlenen ilişki kuralları

- Durum: Kabul edildi
- Tarih: 26.11.2026

## Bağlam

U1–U3 ile altyapı, olay omurgası ve arama hazırdı. Model ise ilk iş için yeterli olan 36 sınıfta kalmıştı: sunucu, ağ cihazı, VMware, uygulama, hizmet. Bağlayıcılar (U5) ancak hedef sınıf varsa doğru yazar. Sınıf yoksa veri yanlış sınıfa düşer: dizüstü bilgisayar "sunucu", Kubernetes pod'u "uygulama" olur. Sonradan yeniden sınıflandırmak geçmişi, ilişkileri ve kimlik bağlarını bozar.

Üç boşluk daha vardı:

- **Yaşam döngüsü.** CSDM 5 aşama ve aşama durumu alanları vardı ama değer listesi yoktu. Serbest metin olarak "Operational", "operasyonel" ve "Prod" yan yana birikir.
- **İlişkiler.** 14 ilişki tipi vardı, hangi sınıfın hangi sınıfa hangi tiple bağlanabileceği tanımlı değildi. ServiceNow bunu yalnızca önerir (`cmdb_rel_type_suggest`). Bu yüzden ServiceNow CMDB'lerinde "sunucu, uygulamanın üzerinde çalışır" gibi ters ilişkiler sık görülür ve etki analizini bozar.
- **Kimlik ailesi.** Eşleştirme, adayları sınıf ailesi içinde arar. Aile listesinde olmayan yeni dallar (küme, Kubernetes, bulut, IP ağı) ortak bir "ailesiz" havuza düşüyordu. Sonuçta "prod" adlı bir Kubernetes ad alanı ile "prod" adlı bir IP ağı birbirinin adayı oluyordu.

## Karar

### 1. Sınıf kütüphanesi ServiceNow temel adlarıyla genişler; belirsiz olan işaretlenir

`metadata/classes.yaml` 36 sınıftan 82 sınıfa çıktı. Eklenen dallar şunlar:

- uç nokta (`cmdb_ci_pc_hardware`, yazıcı)
- kabin
- depolama (cihaz, disk, birim, NAS, dosya sistemi)
- mantıksal ağ (IP ağı, VLAN, DNS adı)
- küme ve küme düğümü
- konteyner ve Kubernetes (küme, ad alanı, düğüm, dağıtım, pod, servis)
- bulut (hizmet hesabı, mantıksal veri merkezi, Azure ve AWS bölgeleri)
- ara katman yazılımı (uygulama sunucusu: Tomcat, JBoss, WebLogic, WebSphere; web sunucusu: Apache, IIS, Nginx)
- veritabanı (MySQL, PostgreSQL, DB2 örneği, veritabanı kataloğu)
- CSDM iş katmanı (iş yeteneği, iş süreci, bilgi nesnesi, dinamik CI grubu, hesaplanan ve etiket tabanlı uygulama servisi; iş uygulaması alanları)

Sınıf alanları JSONB'dedir; DDL yazılmadı (kural 6). Sürüme veya eklentiye göre farklılık gösterebilen sınıf ve alanlar `verify: true` taşır. Doğrulama listesi `verification_backlog()` ile türüne göre çıkar: sınıf, alan, seçim listesi, ilişki tipi, ilişki kuralı, kimlik kuralı. Bugün 113 öğedir. Kurum örneğinden salt okunur dışa aktarımla kapanır (F0-07, `tools/sn_metadata_export.py`).

Bu sürüm ilk kez bir dal yerleşimini bilerek ServiceNow'a bırakıyor: Kubernetes bileşenleri ortak bir `cmdb_ci_kubernetes_component` altında toplandı. Kurum örneği farklıysa değişiklik seti ile düzeltilir.

### 2. Kimlik ailesi varsayılanı: cmdb_ci'ın doğrudan alt dalı

`ClassHierarchy.family()` tanımlı aile köklerinden (donanım, VM nesnesi, uygulama…) birini bulamazsa sınıfın `cmdb_ci` altındaki ilk dalını döndürür (ör. `cmdb_ci_kubernetes_component`, `cmdb_ci_ip_network`). Var olan sınıfların ailesi değişmez. Yeni bir dal eklendiğinde aday havuzu kendiliğinden ayrışır; ortak "ailesiz" havuz yoktur. Sınıf çelişkisi denetimi (`class_conflict`) ikinci savunma hattı olarak kalır.

### 3. Yaşam döngüsü: bağımlı değer listesi ve eski alanlara eşleme

- `life_cycle_stage` ve `life_cycle_stage_status` kök kolonları `choice: true` oldu. Altı aşama tanımlı: Fikir, Tasarım, Yapım, Devreye alma, Operasyonel, Ömür sonu. Bunlara bağlı on bir durum var.
- Durum, aşamaya **bağımlıdır**. ServiceNow `sys_choice.dependent_value` ile aynı biçim kullanılır: `depends_on: life_cycle_stage`, her değerde `dependent_value`. Doğrulayıcı listede olmayan bir üst değeri ve `depends_on` olmadan kullanılan `dependent_value`'yu reddeder. `meta.sys_choice.dependent_value` kolonu göç 0014 ile eklendi.
- **Daha iyisi:** her durum `legacy` eşlemesi taşır, ör. "Kullanımda" → `operational_status=1`, `install_status=1`. ServiceNow replikası ve eski raporlar bu iki alanı okumaya devam eder; Evren tutarsızlığı bu eşlemeden denetleyecek (U7). Eşlemedeki her değer hedef alanın seçim listesinde olmalıdır; doğrulayıcı bunu denetler.
- ServiceNow'da bu alanlar `life_cycle_stage` / `life_cycle_stage_status` tablolarına referanstır. Evren'de değer listesidir. Replika (U9) değeri ilgili kaydın sys_id'sine çevirir.

### 4. İzinli ilişki kuralları: tanım metadata'da, karar üç değerli, zorlama kademeli

- Kurallar `metadata/relationship_rules.yaml` içindedir. Her kural bir `parent`, bir `type` ve bir `child` sınıfından oluşur. Tip, ebeveynin ağzından okunur ("uygulama Runs on sunucu").
- Kurallar **alt sınıflara kalıtılır**: `cmdb_ci_appl Runs on cmdb_ci_computer` kuralı Tomcat → Linux sunucu ilişkisini de kapsar. `cmdb_ci Depends on::Used by cmdb_ci` genel bağımlılıktır.
- Bugün 36 kural vardır. Ürün adı içermezler (kural 8, testle denetlenir).
- `evren_core.relations.RelationRules.check()` üç sonuç döndürür:
  - `allowed`: izinli; açıklamada en özel kural gösterilir.
  - `reversed`: yön ters; tersi izinli. Türkçe ipucu verilir: "ebeveyn ve çocuk yer değiştirmeli".
  - `not_allowed`: izinli kural yok.
- `suggestions(sınıf)` arayüzdeki "ilişki ekle" önerisini iki yönde üretir.
- Zorlama kipi: `off`, `warn` (varsayılan) veya `enforce`. U4 tanımı ve denetimi getirir. Golden record'a yazılan ilişkiye uygulanması U7'dedir:
  - `warn` kipinde izinsiz ilişki kalite bulgusu olur.
  - `enforce` kipinde izinsiz ilişki reddedilir ve kayıt kuyruğa gider.
- Kurallar `meta.cmdb_rel_rule` tablosuna tohumlanır. Yabancı anahtarlar sınıfa ve ilişki tipine bağlıdır. Herkes okuyabilir; yalnızca göç/tohum rolü yazar. Yetki testleri bunu doğrular.
- Bağlayıcı eşleme dosyalarındaki ve demo verisindeki her ilişki bu kurallarla test edilir. Yeni bir eşleme izinsiz ilişki üretirse CI kırmızıya döner.

### 5. Sınıfa özgü kimlik anahtarı ve yeni sınıfların kimlik kuralları (U4.2)

- Kimlik anahtarlarına `attr:<alan>` türü eklendi (`IdentityKeys.class_keys`). Kurum genelinde tekil sınıf öznitelikleri `CLASS_KEY_ATTRIBUTES` listesindedir: `account_id`, `cluster_id`, `subnet`. Bunlar aday dizinine girer.
  - Eşleşme güçlü kanıttır (+8). Aynı alanda farklı değer çelişkidir (−5) ve kural eşleşse bile otomatik birleştirmeyi durdurur.
  - Alt ağ CIDR'ye çevrilir; geçersiz değer anahtar üretmez.
- Bağlam öznitelikleri (`CONTEXT_KEY_ATTRIBUTES`: ad alanı, bağlama noktası, bölge, VLAN) yalnızca bağımlı kurallarda kullanılır, küresel dizine girmez.
- `ingest.identity_key` tür kısıtı `attr:` ile başlayan, ServiceNow alan adı biçimindeki türleri kabul eder (göç 0015).
- Yeni kurallar:
  - **Bağımsız:** bulut hizmet hesabı (hesap kimliği), küme (küme kimliği; ad tek başına yetmez), IP ağı (alt ağ), konteyner (çalışma zamanı kimliği), servis/iş uygulaması/iş yeteneği/iş süreci/bilgi nesnesi (korelasyon kimliği, sonra ad).
  - **Bağımlı:** Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu. Bunlar U7'ye kadar `insufficient` döner.
- Doğrulayıcı üç hatayı reddeder:
  - sınıfta tanımlı olmayan sınıf anahtarı,
  - üst bağlamı olmayan bağımlı kural,
  - referans olmayan `parent_reference`.

### 6. Sahiplik ve yönetişim kök alanları (U4.3)

- `cmdb_ci` kök alanlarına `cost_center` eklendi; `cmn_cost_center` tablosuna referanstır. Sahiplik onayı için `attested`, `attested_by` ve `attested_date` eklendi; ServiceNow adlarıyla, `verify: true`.
- Kök alan fiziksel kolon olduğu için göç gerekti (0016). `CORE_COLUMNS` ve DDL testi artık 0001'deki tanımı ve sonraki göçlerdeki `ADD COLUMN` satırlarını sırayla birlikte okur.
- Referans tablosu `ref.cmn_cost_center` ServiceNow'dan aynı sys_id'lerle yansıtılır; okuma yetkisi `evren_readonly`'de, yazma `evren_app_sync`'te.
- Uzlaştırma kuralları:
  - `cost_center` yönetişim alanıdır (Manual 10, ITSM 50).
  - Onay alanlarını **yalnızca Manual** yazar: ServiceNow'daki onay bilgisi Evren'e taşınmaz, Evren onay akışı tek kaynaktır.
  - Elle düzenleme formunda onay alanları yoktur; onaylar U6'da gelecek onay akışından geçer (F1-17).

### 7. Veri Modeli sayfası (U4.4)

- Görünüm tek bir çekirdek fonksiyondan üretilir (`evren_core.model_view`):
  - API modunda `GET /api/v1/schema/model` canlı metadata'yı sunar; yayınlanmış değişiklik dosyaları dahildir.
  - Demo modunda `tools/build_model.py` aynı görünümü `apps/web/content/model.json` olarak üretir; CI `--check` ile güncelliğini denetler.
  - Bir test, API çıktısının statik dosyayla aynı olduğunu doğrular.
- CI sayıları yalnızca kullanıcının okuyabildiği sınıflar için verilir; kısıtlı bir sınıfın varlığı sayı üzerinden sızmaz.
- Kalıtılan alanlar ve bir sınıfa düşen ilişki önerileri istemcide ata zincirinden hesaplanır (`RelationRules.suggestions` ile aynı kural). Böylece dosya küçük kalır (≈120 KB).
- Sayfanın içeriği:
  - sınıf ağacı: Türkçe arama, "yalnızca doğrulanacaklar" süzgeci, aramada bağlam için üst sınıflar;
  - ayrıntı paneli: kendi ve kalıtılan alanlar, kimlik kuralı ve bağlamı, izinli ilişkiler, SQL görünüm adı, CI sayısı;
  - değer listeleri: bağımlı değerler ve eski alan karşılıkları;
  - ilişki kuralları ve doğrulanacaklar.
- Şema tasarımcısı panelden "Bu sınıfa alan ekle" ile değişiklik setini o sınıf seçili açar. Yayın akışı değişmez: taslak → onay → yayın, hazırlayan onaylayamaz.

### 8. Herkese açık belgelerle doğrulama (F0-07, 26.11.2026)

Proje sahibinin yönlendirmesiyle adlar ServiceNow örneği beklenmeden herkese açık belgelerden doğrulandı.

- **Kaynaklar:**
  - ServiceNow belgeleri, KB makaleleri ve CSDM 5 yayınları ("resmi");
  - ServiceNow Community ("topluluk");
  - üçüncü taraf sözlük dökümleri ve entegrasyon belgeleri ("üçüncü taraf").
- **Kanıt defteri:** `metadata/verification.yaml`. Her öğe için sonuç, belgedeki değer, not ve kaynak tutulur: 181 kayıt, 95 kaynak.
  - Doğrulayıcı defterle `verify` işaretlerinin tutarlılığını zorlar.
  - Belgeyle sonuçlanan öğede işaret kalmaz; kısmi veya bulunamayan öğede işaret durur.
  - İşaretli her öğenin defterde kaydı olmalıdır.
- **Sonuç:** 74 öğe doğrulandı, 18 öğe belgeye göre düzeltildi, 5 öğe kaldırıldı, 2 öğe Evren tanımı olarak işaretlendi. 55 öğe kısmen doğrulandı, 27 öğe bulunamadı; doğrulanacak öğe 130'dan 82'ye indi.
- **Başlıca düzeltmeler:**
  - **vCenter sınıf ağacı:** vCenter uygulama sınıfından türer. Veri merkezi mantıksal veri merkezinden, küme host kümesinden, datastore genel datastore sınıfından türer.
  - **Misafir sunucu ilişkileri:** VM örneğine `Instantiates::Instantiated by`, ESX'e `Virtualized by::Virtualizes`. vCenter eşlemesi değişti.
  - **VM ve bulut kimliği:** VM örneği vCenter UUID + nesne kimliğiyle, bulut bölgesi hesap bağlamında nesne kimliğiyle tanınır.
  - **Depolama ve kabin:** kabin ile depolama birimi doğrudan CI'dan türer; dosya sistemi depolama biriminin altındadır.
  - **Kubernetes:** iş yükü ara sınıfı eklendi; küme adla tanınır.
  - **CSDM 5 yaşam döngüsü:** belgede olmayan dört durum kaldırıldı. "Missing" ayrı aşama oldu; "End of Operation" ve "End of Support" eklendi.
  - **Yetenek hiyerarşisi:** ilişkiyle değil üst alanla kurulur.
  - **Asıl onay alanı:** `attestation_status` (göç 0017).
- **Sınır:** servicenow.com sayfaları çalışma ortamında doğrudan açılamadı ve arama kotası doldu. Kanıtlar arama özetlerine dayanır; yalnızca üçüncü taraf kaynakla doğrulananlar raporda ayrıca görünür. Kısmi ve bulunamayan öğeler ile kuruma özgü özelleştirmeler canlı kabulde salt okunur sözlük dökümüyle kapanır.

## Sonuçlar

- U5 bağlayıcıları (vCenter kümeleri, NNMi alt ağları, Defender uç noktaları, Kubernetes, bulut) yazacakları sınıfı bulur.
- Etki analizi ve hizmet haritası (U7) ters veya anlamsız ilişkiyle bozulmaz. İlişki kalitesi ölçülebilir olur.
- Demo üreticisindeki bir hata ortaya çıktı ve düzeltildi: ters yönlü ilişkiler eklenirken eklenen kayıt yeniden çevriliyordu. Bu yüzden sunucu aynı VM ile hem ebeveyn hem çocuk görünüyordu.
- `verify: true` öğe sayısı arttı (113). Bu bilinçli bir karardır: ad ServiceNow'dan doğrulanmadan kesin sayılmaz.

## Bilinçli sınırlar

- Bağımlı yeni sınıfların (Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu) üst bağlamda çözümlenmesi U7'dedir. O zamana kadar güvenli varsayılanla `insufficient` dönerler.
- IP ağı kuralı alt ağı kurum genelinde tekil sayar. VRF ile çakışan özel adres aralıkları kullanan kurumda kural bağımlıya (yönlendirme alanı bağlamı) çevrilmelidir.
- İlişki kuralları ServiceNow'un önerdiği listenin bir alt kümesidir. Depolama topolojisi (SAN/LUN) kuralları depolama bağlayıcısıyla gelir.
- CSDM 5 aşama/durum adları ve dinamik CI grubu, bilgi nesnesi gibi sınıflar kurum örneğinden doğrulanmalıdır.
