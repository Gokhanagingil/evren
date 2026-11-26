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
