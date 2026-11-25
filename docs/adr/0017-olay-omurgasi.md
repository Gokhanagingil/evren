# ADR-0017: Olay omurgası — alım geçidi, Kafka, uzlaştırıcı tüketicisi, outbox yayını

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

ADR-0002 ara katmanı Kafka + uzlaştırıcı olarak tanımladı; ADR-0005 Kafka istemcisini (confluent-kafka) ayrı değişikliğe bıraktı. U1 ile Kafka yığında ayağa kalktı ama veri akmıyordu: bağlayıcıların gönderebileceği bir giriş noktası yoktu, uzlaştırıcı yalnızca insan isteklerini uyguluyordu, golden record değişiklikleri outbox'ta birikiyordu. Ayrıca uzlaştırıcı bir çalışmanın sayaçlarını bellekte tutuyordu; kayıtlar birden çok bölümden ve tüketiciden, yeniden teslimle gelince bu tutarsız olur.

## Karar

### 1. Bağımlılık: confluent-kafka 2.16.1

| Paket | Nerede | Gerekçe |
| --- | --- | --- |
| confluent-kafka | alım geçidi, alım tüketicisi, outbox yayıncısı | librdkafka tabanlı resmi istemci: idempotent üretici, tüketici grupları, SASL/SCRAM, TLS ve mTLS. librdkafka tekerleğe gömülü gelir; geçişli Python bağımlılığı yok. Apache 2.0. Hash ile kilitli, 14 gün bekleme kuralına uygun (`requirements/runtime.txt`), pip-audit temiz. |

Değerlendirilen seçenekler: aiokafka (asyncio, saf Python; SASL/TLS olgun ama idempotent üretici ve işlemlerde sınırlı) ve kafka-python (bakım geçmişi düzensiz). Kafka'ya dokunan kod tek adaptörde toplanır (`evren_core/kafka_client.py`); istemci değişirse yalnızca bu modül değişir.

### 2. Konu ve mesaj sözleşmesi (`evren_core/messaging.py`)

| Konu | Anahtar | İçerik | Yazan → okuyan |
| --- | --- | --- | --- |
| `evren.ingest.record.v1` (6 bölüm, 7 gün) | `<veri_kaynağı>:<source_key>` | `IngestEnvelope` (CloudEvents) | geçit → alım tüketicisi |
| `evren.ingest.run.v1` | veri kaynağı | `RunEnvelope` (bilgilendirme) | geçit → izleme |
| `evren.cmdb.ci.changed.v1` | `sys_id` | `cmdb.ci.*` olayları (oluşturma, değişiklik) | outbox → tüketen uygulamalar |
| `evren.cmdb.activity.v1` | `sys_id` | insan istekleri (elle düzenleme, ayırma …) | outbox → izleme |
| `evren.dlq.v1` | özgün anahtar | özgün mesaj + neden başlıkları | tüketici → operasyon |

Anahtar, aynı nesnenin kayıtlarını aynı bölüme düşürür; böylece sıra korunur. Olay kimliği `outbox-<id>`'dir; tüketen uygulamalar tekrarları bununla ayıklar. Hassasiyeti `confidential` veya `restricted` olan alanların değerleri olaya konmaz: yalnızca alan adı ve `redacted: true` gider.

### 3. Alım geçidi (`services/ingest-gateway`)

- **Kimlik:** Her bağlayıcı kimliği (`ingest.connector_credential`) tek bir veri kaynağına bağlıdır. Süresi ve dakikalık kayıt sınırı vardır, iptal edilebilir.
  - Belirteç `evr_` + 32 rastgele bayttır. Veritabanında yalnızca SHA-256 özeti ve son 4 karakteri tutulur.
  - Belirteç verilirken bir kez gösterilir (`tools/connector_credential.py`; arayüzden yönetim U4'te).
- **Doğrulama zinciri:**
  1. Belirteç denetlenir.
  2. Kaynak yetkisi denetlenir: kimlik başka kaynağa yazamaz, emekli kaynak yazamaz. Ret denetim izine yazılır.
  3. Zarf şeması denetlenir; bilinmeyen alan reddedilir ve hata gövdesi girdiyi yansıtmaz.
  4. Çalışmanın açık olduğu denetlenir.
  5. Gelecek tarihli gözlem (10 dakikadan ileri) reddedilir.
  6. Boyut sınırları uygulanır: gövdede `Content-Length` zorunludur, gövde 8 MiB, toplu gönderim 500 kayıt, tek kayıt 900 KB ile sınırlıdır.
  7. Kimlik başına hız sınırı uygulanır (429 + `Retry-After`).
- **Toplu gönderim ya hep ya hiç:** Bir kayıt geçersizse hiçbiri alınmaz; hata, kaydın sırası ve alanıyla döner. 202 yanıtı ancak Kafka tüm mesajları onayladıktan sonra verilir. Kafka'ya ulaşılamazsa 503 döner ve hiçbir kayıt sayılmaz; bağlayıcı aynı toplu gönderimi yeniden dener, tekrar zararsızdır.
- **Ağ:** Geçit varsayılan olarak yalnızca loopback'e bağlanır.
  - Ağa açılmak için TLS sertifikası gerekir (`EVREN_GATEWAY_TLS_*`). İstemci CA'sı verilirse istemci sertifikası zorunlu olur (mTLS).
  - Düz HTTP yalnızca açık `EVREN_GATEWAY_INSECURE_HTTP=1` ile mümkündür (geliştirme konteyneri).
  - Belgeler ve OpenAPI uç noktaları kapalıdır.
- **En az yetki (göç 0012):** Geçidin rolü kaynak kaydını okuyamaz ve yazamaz. Çalışmayı yalnızca açılış sütunlarıyla açar, yalnızca sayaç ve kapanış sütunlarını günceller. Kimlik bilgisinde yalnızca son kullanım zamanını yazar. Uzlaştırıcı ve API kimlik bilgisini göremez; denetçi özet hariç görür.

### 4. Çalışma yaşam döngüsü: veritabanı koordine eder, sayaçlar kalıcıdır

- Çalışmayı geçit açar (`opened_by`). Kafka'ya teslim edilen kayıtları sayar (`accepted_count`). Kapanış isteğinde bildirilen sayı (`reported_count`) teslim edilenden fazla olamaz (409).
- Uzlaştırıcı çalışanı her turda `finalize_ready_runs` çalıştırır. Çalışma üç yoldan kapanır:
  - Bildirilen sayıda kayıt işlendiyse bildirilen tamamlanma durumuyla kapanır.
  - Kapanış istendikten sonra 15 dakikada kayıtlar tamamlanmadıysa **eksik** kapanır (`completed=false`). Bu durumda devre kesici açılır ve yıkıcı işlem yapılmaz.
  - Kapanış hiç istenmediyse 24 saat sonra **terk edilmiş** sayılır ve eksik kapanır.
- Sayaçlar bellekte değil kaynak kayıtlarındadır: `last_run` (bu çalışmada görüldü), `first_run` (ilk kez bu çalışmada görüldü), `changed_run` (içeriği bu çalışmada değişti). Kaynak kaydı anahtar başına tek satır olduğundan Kafka'nın yeniden teslimi iki kez sayılamaz. Birden çok tüketici örneği ve çalışan güvenle çalışır (`FOR UPDATE SKIP LOCKED`).
- **Devre kesici çalışma türünü bilir.** Delta çalışmasında hacim düşüşü, sıfır kayıt ve değişim oranı kuralları uygulanmaz; yalnızca tamamlanma ve hata oranı denetlenir. Taban çizgisi yalnızca tam (snapshot) çalışmalardan hesaplanır. Bu ayrım olmadan tek kayıtlı bir delta çalışması "hacim düştü" diye yanlışlıkla karantinaya giriyordu; duman testi bunu yakaladı.

### 5. Alım tüketicisi (`evren_reconciler/consumer.py`, `evren ingest`)

- En az bir kez okunur, etkisi bir kezdir:
  - Ofset yalnızca kaydın veritabanı işlemi tamamlanınca saklanır (`enable.auto.offset.store=false`).
  - Yeniden gelen kayıt içerik özeti aynı olduğu için "değişmedi" yolundan geçer.
- **Zehirli mesaj kuyruğu kilitlemez:** Şemaya uymayan, çalışması olmayan (geçidi atlatma girişimi) veya başka kaynağın çalışmasına yazan kayıt değiştirilmeden `evren.dlq.v1`'e gider; nedeni ve kaynak bölüm/ofset başlıklara yazılır. Beklenmeyen uygulama hatası da hata kimliğiyle DLQ'ya gider; ayrıntı günlüktedir.
- **Geçici hata kayıp yaratmaz:** Veritabanı kopması, kilitlenme veya DLQ'ya yazılamaması durumunda mesaja geri sarılır ve üstel beklemeyle yeniden denenir.
- Tüketici kendi giriş rolüyle bağlanır (`evren_ingest_svc` → `evren_app_reconciler`) ve nabzını `kind='ingest'` olarak yazar. Arayüz Genel Bakış'ta durumunu gösterir.

### 6. Outbox yayını

- Golden record değişikliği ve olayı aynı işlemde yazılır.
- Çalışan outbox satırlarını `FOR UPDATE SKIP LOCKED` ile alır, Kafka'ya yayınlar ve onay gelince `published_at` yazar. Kafka'ya ulaşılamazsa işlem geri alınır ve olaylar beklemede kalır.
- Outbox'a yazım çalışanı `NOTIFY` ile uyandırır, yani yayın gecikmesi tarama aralığına bağlı değildir.

### 7. Kafka güvenliği

- İstemci varsayılanı `SASL_SSL`'dir (SCRAM-SHA-512). Parola yalnızca dosyadan okunur ve `repr`'da görünmez. TLS ana bilgisayar adı doğrulaması açıktır; isteğe bağlı istemci sertifikası desteklenir.
- `PLAINTEXT` yalnızca açık `EVREN_KAFKA_ALLOW_PLAINTEXT=1` ile çalışır (geliştirme yığını).
- Üretimde her servis ayrı Kafka kimliğiyle bağlanır ve konu ACL'leriyle sınırlanır:

| Servis | İzin |
| --- | --- |
| Geçit | `ingest.*` yazma |
| Tüketici | `ingest.record` okuma, `dlq` yazma |
| Çalışan | `cmdb.*` yazma |

## Değerlendirilen seçenekler

- **Geçit kayıtları doğrudan veritabanına yazar (Kafka'sız):** Daha basit olurdu. Ancak alım hızı uzlaştırma hızına bağlanır, kaynak ile işleyici ayrılmaz, yeniden oynatma olmaz. ADR-0002 Kafka'yı seçti.
- **Yalnızca mTLS ile kimlik:** Sertifika ile veri kaynağı arasındaki bağı yönetmek PKI bağımlılığı getirir. Belirteç + TLS bugün çalışır, mTLS üstüne katman olarak eklenebilir; ikisi birlikte kullanılabilir.
- **Kafka REST Proxy / Connect:** Ek JVM bileşeni getirir ve kaynak yetkisi, zarf doğrulaması, çalışma yönetimi yine Evren'de gerekir.
- **Outbox için Debezium (CDC):** Ek bileşen ve mantıksal çoğaltma yetkisi gerektirir. Sorgu tabanlı yayıncı bugünkü hacim için yeterli; gerekirse sonra değiştirilebilir.
- **Kafka işlemleriyle tam bir kez teslim:** Veritabanı ile Kafka arasında dağıtık işlem gerekmez; içerik özeti ve kalıcı sayaçlar aynı etkiyi daha basit sağlar.

## Sonuçlar

- `python tools/dev_stack.py up --demo && python tools/dev_stack.py smoke` uçtan uca zinciri sınar:

  bağlayıcı kimliği → geçit (401/403 olumsuz denetimler) → Kafka → tüketici → CI → çalışma "tamam" kapanışı → outbox → Kafka CI olayı → kimlik iptali

  CI "Yığın" işi bunu her değişiklikte çalıştırır.
- **Bilinen sınırlar:**
  - Hız sınırı süreç içidir; birden çok geçit örneğinde sınır örnek başına uygulanır.
  - Aynı `source_key` bir çalışmada iki kez gönderilirse işlenen sayı bildirilenden az kalır ve çalışma süre sonunda eksik kapanır. Bu güvenli taraftır, ama bağlayıcı sözleşmesinde açıkça yazılıdır.
  - Çalışma olayları yalnızca bilgilendirme amaçlıdır.
  - Geliştirme yığınında Kafka ACL'leri ve SASL yoktur.
  - Bağlayıcı kimlikleri için arayüz U4'tedir.
  - Gerçek kaynak bağlayıcıları (vCenter, Zabbix …) U4'te bu geçide gönderecek.
