# ADR-0020: Bağlayıcı çalıştırıcı — standart kütüphaneyle HTTP, değiştirilebilir taşıma, kaynak simülatörü, ek bağımlılık yok

- Durum: Kabul edildi
- Tarih: 26.11.2026

## Bağlam

U2 (ADR-0017) ile alım geçidi, Kafka ve uzlaştırıcı tüketicisi çalışıyor. Ancak kaynaktan veri çekip geçide gönderen bir program yoktu. Eşleme motoru (`FieldMapping`) ve eşleme dosyaları hazırdı, fakat yalnızca testlerde sabit JSON ile kullanılıyordu. Geçide kayıt gönderen tek şey yığın duman testindeki elle yazılmış istekti.

Bağlayıcının üç zor işi var:

1. **Eksiksiz çekme:** Sayfalama yarıda kalırsa çalışma "tamamlandı" sayılmamalı, yoksa devre kesici eskime ve emekliye ayırmaya izin verir (kural 4).
2. **Kimlik bilgisi:** Kaynak ve geçit sırları hiçbir zaman komut satırında, günlükte veya hata iletisinde görünmemeli.
3. **Kurum dışında sınanabilirlik:** Kaynaklara (vCenter, BMC, NNMi, Zabbix, Defender, ServiceNow) geliştirme ortamından erişim yok. İstemci kodu gerçek ağ olmadan sınanabilmeli; gerçek kabul ise kurum ortamında yapılmalı (U10).

## Karar

### 1. Katmanlar (`evren_core/connectors/`)

| Modül | Görev |
| --- | --- |
| `http.py` | JSON HTTP istemcisi (`HttpClient`), `Transport` sözleşmesi, `UrllibTransport`, `ReplayTransport`, `RecordingTransport`, `read_secret` |
| `gateway.py` | Alım geçidi istemcisi (`GatewayClient`): çalışma aç → toplu gönder → kapanış iste; `DryRunGateway` |
| `runtime.py` | `ConnectorRunner`: kaynak istemcisi → eşleme → geçit; `RunReport` |
| `sources/` | Kaynak istemcileri ve kayıt (`SOURCES`); ilk istemci Zabbix (`ZabbixClient`, `ZabbixSimulator`) |

Çalıştırıcı CMDB'ye yazmaz, eşleştirme yapmaz, CI silmez (kural 2). Veritabanı erişimi yoktur; tek çıkışı geçittir.

### 2. Ek bağımlılık yok

HTTP katmanı standart kütüphaneyle (`urllib.request`, `ssl`) yazıldı. ADR-0018'deki OpenSearch istemcisiyle aynı gerekçe geçerli: `requests` veya `httpx`, urllib3, certifi, idna, h11 ve anyio zincirini getirir. Bağlayıcının kullandığı yüzey (JSON gönder/al, başlık, zaman aşımı, TLS) küçüktür. Tedarik zinciri riskini artırmaya değmez.

### 3. Güvenli varsayılanlar

- **TLS:** `https` zorunludur; sertifika zinciri ve ana bilgisayar adı doğrulanır. Kurum CA paketi verilebilir (`EVREN_SOURCE_CA_FILE`, `EVREN_GATEWAY_CA_FILE`). Düz `http` yalnızca geçit için ve açık `EVREN_GATEWAY_ALLOW_HTTP=1` ile çalışır; bu yalnızca geliştirme yığını içindir.
- **Sırlar yalnızca dosyadan:** `EVREN_SOURCE_TOKEN_FILE`, `EVREN_SOURCE_PASSWORD_FILE`, `EVREN_CONNECTOR_TOKEN_FILE`. Adreste kullanıcı bilgisi reddedilir. Belirteç standart girdiden de verilebilir (`/dev/stdin`); yığın duman testi böyle çalışır.
- **Sızıntı yok:**
  - `HttpClient.__repr__` kimlik doğrulamanın yalnızca var olup olmadığını gösterir.
  - Hata iletileri sır, başlık, sorgu dizesi ve sunucu yanıt gövdesi içermez; yalnızca yöntem, yol ve durum kodu yazılır.
  - Zabbix API hatasında yalnızca hata kodu iletilir.
  - Eşleme hatasında kayıt içeriği değil yalnızca hata türü rapora girer. Beklenmeyen hata günlüğe tür adıyla yazılır; ayrıntı yalnızca `DEBUG` düzeyindedir.
- **Boyut sınırı:** tek yanıt en fazla 64 MB.

### 4. Yeniden deneme

- Bağlantı hatası, 429 ve 5xx geri çekilmeli olarak yeniden denenir (varsayılan 4 kez: 1, 2, 4, 8 sn).
- `Retry-After` başlığına uyulur; üst sınır 120 sn.
- 4xx (429 dışı) kalıcı hatadır ve yeniden denenmez. Geçidin 400, 403, 409 ve 413 yanıtları `GatewayRejected` olur.
- Yeniden denemede aynı gövde gönderilir. Olay kimlikleri aynı kalır.
- **Teslim en az bir kezdir:** geçit Kafka'ya yazdıktan sonra yanıt kaybolursa toplu gönderim yinelenebilir. Bu, ADR-0017'deki en az bir kez sözleşmesiyle uyumludur. Uzlaştırıcı aynı `(data_source, source_key)` kaydını idempotent işler. Geçidin kabul sayacı fazla sayabilir; kapanışta bildirilen sayı çalıştırıcının saydığıdır ve geçit yalnızca "kabul edilenden fazla bildirme"yi reddeder.

### 5. Tamamlanma kuralı

`completed` yalnızca şu durumda doğrudur: kaynak istemcisi sayfalamayı sonuna kadar hatasız bitirdi, hiçbir toplu gönderim reddedilmedi ve hiçbir eşleme hatası olmadı. Aksi hâlde çalışma yine kapatılır, ama `completed=false`, hata sayısı ve kısa bir gerekçeyle. Böylece:

- Devre kesici yıkıcı işleri durdurur.
- Kısmi veri yine uzlaştırılır; kaybolmaz.
- Çalışmanın neden tamamlanmadığı geçitteki kayıtta ve çalıştırıcı raporunda görünür.

Kapanış isteği de başarısız olursa geçitteki çalışma açık kalır. Uzlaştırıcı çalışanı onu eskiyen çalışma olarak kapatır (ADR-0017).

### 6. Değiştirilebilir taşıma, simülatör, kayıt/oynatma

`Transport` tek yöntemli bir sözleşmedir (`send`). Böylece aynı istemci kodu şu taşımalarla çalışır:

| Taşıma | Kullanım |
| --- | --- |
| `UrllibTransport` | Gerçek kaynak ve geçit |
| `ZabbixSimulator` | Zabbix `host.get` alt kümesini örnek veriyle yanıtlar (`connectors/zabbix/samples/hosts.json`, **sentetik**). Birim testleri, geçitle uçtan uca test ve yığın duman testi |
| `RecordingTransport` | Kurum ortamında gerçek kaynağa karşı bir kez çalıştırılır; yanıtları istek parmak iziyle kaydeder (sır kaydedilmez) |
| `ReplayTransport` | Kaydedilmiş yanıtları oynatır. Kayıtta olmayan istek hata verir; istemci beklenmedik bir istek yaparsa test kırmızıya döner |
| Test istemcisi | Geçidin FastAPI uygulamasına ağ olmadan gider (`services/ingest-gateway/tests/test_connector_e2e.py`) |

Simülatörle ve kayıtla yapılan sınamalar gerçek kaynak kabulü yerine geçmez. Gerçek yanıt biçiminin doğrulanması U10'dadır (eşleme dosyalarındaki `verify: true` işaretleri).

### 7. Çalıştırma biçimi

- Komut satırı: `python tools/connector_run.py --connector zabbix ...` veya konteynerde `evren connector-run ...`.
- Geliştirme yığınında ayrı bir `connector` servisi vardır (`profiles: [tools]`). Veritabanı sırrı yoktur; yalnızca geçide gider.
- `--dry-run` geçide göndermez; `--out` ile kayıtlar NDJSON dosyasına yazılır. Bu, eşleme sonucunu gözden geçirmek içindir; dosya kaynak verisi içerir ve kurum dışına çıkarılmaz.
- Çıktı tek satır JSON rapordur: çekilen, eşlenen, atlanan, gönderilen, hata, sınıf dağılımı.
- Çıkış kodu: 0 tamamlandı, 1 tamamlanmadı, 2 yapılandırma hatası.
- Zamanlama (`data_sources.yaml` `schedule`) ve arayüzden "şimdi çalıştır" U5.3'tedir.

## Sonuçlar

- Yeni kaynak eklemek için üç iş gerekir: `sources/<kaynak>.py` (istemci ve mümkünse simülatör), `SOURCES` kaydı ve eşleme dosyası. Çalıştırıcı, geçit istemcisi ve güvenlik kuralları ortaktır.
- Delta: Zabbix'te güvenilir "değişti" alanı yoktur; her çalışma tam listedir. Delta destekleyen kaynaklar (vCenter, ServiceNow, Defender) U5.2'de `since` parametresini kullanacak.
- Ham yük arşivi (`raw_ref`, F1-14) için çalıştırıcıda kanca var (`archive`); arşiv deposu henüz yok.
- Bilinen sınır: yeniden deneme süreç içindedir. Çalıştırıcı süreci ölürse çalışma yarıda kalır ve uzlaştırıcı onu eskiyen çalışma olarak tamamlanmamış kapatır. Kaldığı yerden devam yoktur; sonraki çalışma tam listeyi yeniden gönderir.

## Alternatifler

- **`httpx` veya `requests`:** Daha kısa kod, bağlantı havuzu ve HTTP/2 sağlar. Ancak ek bağımlılık zinciri getirir ve bu yüzeyde gerekmez. Reddedildi.
- **Bağlayıcıyı geçidin içine gömmek:** Kaynak sırları ile geçidin veritabanı erişimi aynı süreçte toplanırdı. Kural 2'yi zayıflatır. Reddedildi.
- **Yalnızca kaydedilmiş yanıtlarla test:** Sayfa boyutu gibi davranışları sınamak zordur. Simülatör ve kayıt birlikte kullanıldı.
