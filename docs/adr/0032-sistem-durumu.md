# ADR-0032: Sistem durumu servis nabızlarından; Kafka'nın durumu onu kullanan servislerden okunur

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (U8.1), mimari

## Bağlam

Platform birden çok servisten oluşur: API, uzlaştırıcı çalışanı, alım tüketicisi, dizinleyici, bağlayıcı çalıştırıcı ve alım geçidi. Arkalarında PostgreSQL, Kafka ve OpenSearch vardır. Bugüne kadar yalnızca uzlaştırıcının çalışıp çalışmadığı Genel Bakış'ta görünüyordu.

Şu sorular yanıtsızdı:

- "Kafka'ya erişilebiliyor mu?"
- "Dizin geride mi?"
- "Olaylar yayımlanıyor mu?"
- "Hangi göç uygulanmış?"
- "Bir kaynağın devre kesicisi açık mı?"

Bunlara cevap verebilmek için operatörün her bileşene ayrı ayrı bakması gerekiyordu.

## Karar

1. **Tek uç nokta, tek sayfa.** `GET /api/v1/system/status` her bileşen için şunları döndürür: durum (`ok` çalışıyor, `warn` dikkat, `bad` sorun var, `off` bu kurulumda yok), tek satırlık özet, ayrıntı listesi ve ne yapılacağını söyleyen Türkçe not. Arayüzde "Yönetim → Sistem Durumu" sayfası, sayfa açıkken 30 saniyede bir yenilenir.

2. **Servisler nabızlarına ayrıntı yazar** (göç 0029: `ingest.worker_heartbeat.details`):
   - Uzlaştırıcı çalışanı olay yayıncısının açık olup olmadığını bildirir.
   - Alım tüketicisi ve dizinleyici Kafka tüketici gecikmesini (yüksek su işareti − konum) ve Kafka'ya ulaşıp ulaşamadığını bildirir.
   - Alım geçidi ilk kez nabız yazar (yeni tür `gateway`, ayar `EVREN_GATEWAY_HEARTBEAT_SECONDS`, varsayılan 30 sn): kabul edilen kayıt, Kafka teslim hatası, Kafka bağlantısı.
   - Nabız yazılamazsa servis çalışmayı sürdürür. Kafka'ya ulaşılamazsa nabız hata türünü yazar, ayrıntı sızdırmaz.

3. **API Kafka'ya bağlanmaz.** Kafka'nın durumu, onu kullanan canlı servislerin bildirdiğidir:
   - Bildiren yoksa `off` olur (Kafka yapılandırılmamış ya da servisler çalışmıyor).
   - Biri ulaşamıyorsa `bad` olur.

   Böylece API'ye Kafka kimliği verilmez, ağ erişimi de açılmaz. Kafka "ayakta" olsa bile servisin ona ulaşamaması, asıl önemli olan durumdur.

4. **Diğer denetimler:**
   - **Veritabanı:** sürüm, boyut, bağlantı/max_connections (%80'de dikkat).
   - **Göç defteri** (`meta.schema_migration`, imajdaki göç dosyalarıyla karşılaştırılır): boş defter dikkat, bekleyen göç dikkat, uygulandıktan sonra değişmiş göç sorun.
   - **OpenSearch:** küme durumu; dizindeki ve veritabanındaki CI sayısı farkı %1'i ve 10'u aşarsa dikkat. Yapılandırılmamışsa `off` olur; arama PostgreSQL yedeğiyle çalışır.
   - **Olay yayını:** yayımlanmamış en eski olay 5 dakikada dikkat, 1 saatte sorun. Yayıncı yoksa `off` olur ve bekleyen olay sayısı yazılır.
   - **İstek kuyrukları:** uygulanamayan karar varsa dikkat. Bekleyen istek varken uzlaştırıcı çalışmıyorsa da dikkat.
   - **Kaynak çalışmaları:** son çalışmanın devre kesici durumu; bir günden uzun açık kalmış çalışma.

5. **Servis durumu:**
   - Son bir gün içinde, nabız aralığının iki katı (en az 60 sn) içinde görülen örnek canlıdır.
   - Düzgün kapanmış ve yeniden başlamamış servis "durduruldu" (dikkat) olur.
   - Nabzı kesilmiş ama kapanmamış servis "yanıt vermiyor" (sorun) olur.
   - Hiç çalışmamış servis `off` olur.

   Genel durum, `off` dışındaki en kötü durumdur.

6. **Yetki.** Yanıt iç ayrıntı taşır (sürümler, bağlantı sayıları, kuyruklar), sır ya da bağlantı bilgisi taşımaz.
   - Yalnızca `platform` kaynağında `read` izni olan görür. Bunun için yeni bir "Platform operatörü" rolü (`evren_operator`) açıldı; Veri sorumlusu da izin aldı. Denetçi ve break-glass yönetici `*` ile görür.
   - API rolü olay tablosunun yalnızca `id`, `created_at` ve `published_at` sütunlarını okur; olay içeriği (`payload`) kapalıdır.
   - Alım geçidi rolü yalnızca nabız tablosuna yazar.

## Sonuçlar

- Operatör tek ekrandan hangi parçanın neden sorunlu olduğunu ve ne yapacağını görür.
- Yığın duman testi gerçek servislerle sınar: veritabanı, göçler, Kafka, beş servis ve olay yayını `ok` olmalı; OpenSearch `ok` ya da `warn` (tek düğümde sarı) olabilir.
- Yeni rolün Entra uygulama kaydında karşılığı tanımlanmalıdır (U10).
- Durum anlıktır: metrik geçmişi, eğilim ve uyarı (e-posta, olay) yoktur. Bunlar OpenTelemetry ile F1-15'te gelecek.
- Nabız tablosu her yeniden başlatmada yeni satır alır. Sayfa yalnızca son bir günü ve her türün son örneğini gösterir. Eski satırların temizliği gözlemlenebilirlik işiyle birlikte ele alınacak (F1-15).

## Alternatifler

- **API'nin Kafka ve OpenSearch'e doğrudan sağlık sorgusu yapması:** API'ye Kafka kimliği ve ağ erişimi gerekirdi. Ayrıca "Kafka ayakta ama alım tüketicisi ulaşamıyor" durumunu göremezdi. Kafka için reddedildi. OpenSearch için API'nin zaten bir istemcisi var (arama), o kullanıldı.
- **Prometheus/OpenTelemetry ile başlamak:** yeni bağımlılık ve ayrı bir bileşen gerekir, hedef ortam kararını bekler. Anlık durum sayfası bağımlılıksız yapılabiliyordu; metrikler F1-15'te ADR ile eklenecek.
- **Sistem durumunu bütün okuyuculara açmak:** iç ayrıntı (sürüm, bağlantı, kuyruk) gereksiz yere açılırdı. Reddedildi; Genel Bakış'taki uzlaştırıcı göstergesi herkese açık kalır.
