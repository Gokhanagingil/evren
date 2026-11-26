# ADR-0020: Bağlayıcı çalıştırıcı — standart kütüphaneyle HTTP, değiştirilebilir taşıma, kaynak simülatörü, ek bağımlılık yok; vCenter için VI/JSON

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

### 8. vCenter: VI/JSON API (U5.2)

- **Seçim:** Eşleme dosyaları vSphere yönetilen nesne modelinin özellik adlarını kullanır (`config.instanceUuid`, `runtime.host`, `guest.net`). vSphere 8.0 U1 ile gelen VI/JSON API aynı modeli JSON/HTTP ile sunar. Bu yüzden ne SOAP/XML ayrıştırma ne de pyVmomi bağımlılığı gerekir. vSphere Automation REST API (`/api/vcenter/vm`) daha az alan verir (ör. `runtime.host`, `guest.net` yok), VM listesinde üst sınır vardır ve alan adları farklıdır; reddedildi. vCenter 7.x (genel desteği bitti) desteklenmez.
- **Protokol** (VMware'in VI/JSON 8.0.1.0 OpenAPI tanımıyla doğrulandı):
  - `GET /sdk/vim25/{sürüm}/ServiceInstance/ServiceInstance/content`.
  - `POST …/SessionManager/{moId}/Login` (`userName`, `password`). Oturum kimliği `vmware-api-session-id` başlığıyla döner ve sonraki isteklerde aynı başlıkla gönderilir.
  - `ViewManager.CreateContainerView`, ardından `PropertyCollector.RetrievePropertiesEx` (`maxObjects`) ve `ContinueRetrievePropertiesEx` (`token`). Sonra `DestroyView` ve `Logout`; ikisi her durumda çağrılır.
  - İlkel değerler `{"_typeName", "_value"}` kutusundadır; nesne başvurusu `{type, value}` biçimindedir. İstemci bunları açar ve noktalı özellik adlarını iç içe belgeye çevirir.
- **Nitelikli anahtar:** `moId` yalnızca tek vCenter içinde tekildir (`host-10` her vCenter'da vardır). ESXi kaynak anahtarı ve VM'lerin host ilişki hedefi bu yüzden `<vCenter örnek kimliği>/<moId>` biçimindedir (`_key`, `_ref.host`). Aksi hâlde iki vCenter'ın host'ları tek CI'da birleşirdi.
- **Eksik özellik:** Yetki veya erişim yüzünden okunamayan özellik (`missingSet`) belgeye `_missing` olarak işlenir. Kimliği okunamayan VM eşleme hatası olur ve çalışma tamamlanmamış kapanır. Sorun sessizce yutulmaz.
- **Eşleme koşulları:** Eşleme motoruna `require` eklendi: koşulların hepsi sağlanmalıdır. `include_when` için ise en az biri yeter. Her vCenter eşlemesi kendi nesne türüne bağlıdır (`_type`). VM örneği şablonları dışarıda bırakır (`config.template` = `false`). Mantıksal değerler `true`/`false` metniyle eşlenir.

### 9. Kayıt dosyalarında sır yok

İlk sürümde istek parmak izi gövdenin SHA-256 özetiydi. Oturum açma gövdesi parolayı içerdiği için kayıt dosyasına parolanın tuzsuz özeti yazılıyordu; bu özet çevrimdışı tahmin edilebilir. Bu, vCenter testinde bulundu ve düzeltildi:

- Parmak izinde kimlik alanları (`password`, `userName` …) özetten önce maskelenir. Oynatma da bu sayede gerçek kimlik bilgisi istemez.
- Gizli yanıt başlıkları (oturum kimliği, `set-cookie`) yalnızca yer tutucuyla kaydedilir.
- İstek başlıkları hiç kaydedilmez.

İkisi de testlidir.

### 10. ServiceNow: Table API, anahtar sıralı sayfalama, sys_id korunur (U5.2)

- **Çekme:** `GET /api/now/table/{cmdb_ci_server|cmdb_ci_netgear}` ile yapılır. Parametreler:
  - `sysparm_exclude_reference_link=true` ve `sysparm_display_value=false`: referanslar `sys_id` olarak gelir.
  - `sysparm_fields`: yalnızca eşlemenin alanları.
  - `sysparm_no_count=true`: toplam sayım yapılmaz.

  Üst tablo sorgusu alt sınıf kayıtlarını da döndürür; sınıf `sys_class_name`'den gelir. Kimlik doğrulama, entegrasyon kullanıcısıyla Basic'tir (parola dosyadan).
- **Sayfalama:** offset yerine anahtar sırası kullanılır: `sys_id>SON^ORDERBYsys_id`.
  - ServiceNow'un "Break up a large export" belgesi aynı deseni kullanır. Offset her sayfada önceki satırları yeniden okur; değişen tabloda kayıt atlatabilir veya yineleyebilir.
  - ACL bazı satırları gizlerse sayfa istenenden kısa gelir. Bu yüzden istemci kısa sayfada durmaz, boş sayfaya kadar devam eder.
  - Gelen `sys_id`'nin sırası ve biçimi denetlenir.
- **Delta:** `sys_updated_on>=<UTC>` ile yapılır. ServiceNow sorgudaki tarihi kullanıcının saat dilimiyle yorumladığı için entegrasyon kullanıcısının saat dilimi UTC olmalıdır.
- **sys_id korunur (uzlaştırıcıda):** Veri kaynağında `sys_id_key: true` vardır (`metadata/data_sources.yaml`). Bağı olmayan ServiceNow kaydı kimlik çözümlemesine girmez:
  - `sync.sn_ci_map`'te o SN sys_id'si varsa kayıt, Evren'de doğup IRE ile replikaya giden CI'dır; Evren CI'ına bağlanır.
  - Yoksa CI aynı sys_id ile açılır (ilk yükleme) veya o sys_id'li CI'a bağlanır.
  - Gölge veya devredilen kaynak bu yola girmez (CI açamaz).

  Önceden bu mantık yalnızca demo yükleyicide vardı. Olay omurgasından gelen ServiceNow kaydı yeni sys_id'li CI açardı ve ServiceNow task'larındaki CI referansları kopardı.
- **Henüz yok:** referans verileri (`sys_user_group`, `sys_user`, `cmn_location`, `core_company`, `cmn_cost_center`) yükleme yolu. CI'daki referans alanları yumuşak referanstır (yabancı anahtar yok), yani CI'lar bu veriler olmadan da alınır; adlar görünmez. F0-08'in parçası olarak sıradadır.

### 11. BMC Helix Discovery: data/search, sabit sonuç kümesi, sayı denetimi (U5.2)

- **Uç:** `GET /api/v{sürüm}/data/search?query=…&format=object&limit=…`. Kimlik doğrulama `Authorization: Bearer` ile yapılır; belirteç Discovery kullanıcısınındır ve dosyadan okunur. Sürüm kuruluma göre değişir (on-prem 25.2 örneklerinde 1.12, SaaS'ta 1.13; `--api-release`).
- **Sayfalama:** ilk yanıt `count`, `offset`, `next_offset` ve `results_id` döndürür. Sonraki sayfalar `results_id` + `offset` ile istenir.
  - Sunucunun verdiği `next` adresi izlenmez; adres istemcide kurulur, böylece belirteç başka bir adrese gitmez.
  - Sonuç kümesi `results_id` ile sabittir. Toplanan sayı `count`'a eşit değilse çalışma tamamlanmamış sayılır.
- **Kapsam:** eşleme masaüstlerini dışarıda bırakır (`include_when: host_type`). Hypervisor düğümü ESXi sınıfına gider. Sorgudaki ilişki yolu (MAC adresleri) ve alan adları `verify: true` işaretlidir; kurum örneğinde doğrulanır (F1-01).

### 12. Defender: Entra istemci kimliği, nextLink güvenliği, belirteç sırrı kayda girmez (U5.2)

- **Kimlik doğrulama:** Entra istemci kimliği akışı (client credentials). `POST https://login.microsoftonline.com/{kiracı}/oauth2/v2.0/token` form gövdesiyle istenir; istemci sırrı dosyadan okunur, kapsam `https://api.securitycenter.microsoft.com/.default`. Uygulama iznine yalnızca okuma verilir (`Machine.Read.All`; kurum kaydında doğrulanacak). Belirteç bellekte tutulur. API 401 dönerse bir kez yenilenir, çünkü uzun çalışmada süresi dolabilir.
- **Uç ve sayfalama:** `GET https://api.security.microsoft.com/api/machines?$top=…` çağrılır; sonraki sayfa `@odata.nextLink`'tedir (Microsoft Learn: List machines). Bağlantı yalnızca API ile aynı şema ve ana bilgisayardaysa izlenir. Yolu ve sorgusu alınır, istek istemcinin temel adresine yapılır; başka adrese giden bağlantı çalışmayı tamamlanmamış yapar.
- **Kısıtlı veri:** risk ve maruziyet alanları CI özniteliği olmaz (eşleme bunları almaz; testli). Yalnızca `cmdb.ci_security_posture`'a gider; o yol henüz yok (F1-02).
- **Kayıt güvenliği:** form kodlu gövdelerdeki kimlik alanları (`client_secret`, `client_id`) parmak izinde maskelenir. Yanıttaki `access_token` / `refresh_token` / `id_token` kayda yer tutucuyla yazılır (testli).

### 13. NNMi: WS-I SOAP ve güvenli XML ayrıştırma, ek bağımlılık yok (U5.2)

- **Arayüz:** NNMi'nin belgelenmiş düğüm arayüzü WS-I SOAP'tır (`/NodeBeanService/NodeBean`, `getNodes`). Kimlik doğrulama Basic ile yapılır; "Web Services Client" rolü yeter. Genel bir REST düğüm API'si belgede bulunamadı.
- **Sayfalama:** süzgeç `id GT SON` koşulu ile `maxObjects` kısıtıdır; boş sayfada biter. Süzgeç biçimi NNMi Developer Toolkit'teki "WS-I Filter Definition"a dayanır, ancak `maxObjects` ve sıralama belgeden doğrulanamadı (kurum sürümünde doğrulanacak, F1-03).
  - Gelen kimlikler artan sırada değilse çalışma durur. Sıralama güvencesi yoksa anahtar sırası kayıt atlatabilir; sessiz eksik veri yerine görünür hata seçildi.
- **XML ayrıştırma:** `evren_core/connectors/safe_xml.py` standart kütüphane expat üzerinedir.
  - Reddedilenler: `<!DOCTYPE>`, varlık tanımı ve dış varlık başvurusu. Böylece varlık genişletme (billion laughs) ve XXE kapanır.
  - Derinlik 64 ile, boyut HTTP katmanında 64 MB ile sınırlıdır.
  - `defusedxml` aynı korumayı sağlar ama yeni bağımlılık olurdu; kullanılan yüzey küçük olduğu için eklenmedi. `xml.etree` doğrudan kullanılmaz.
- **Kayıt/oynatma:** XML yanıtları ham saklanır (`.xml`). Kimlik doğrulama başlıktadır; gövde sır içermez.
- **SOAP Fault:** metni kurum bilgisi içerebilir, iletiye yazılmaz.

### 14. Arayüzde bağlayıcı görünürlüğü (U5.3, göç 0018)

- **Çalışma ayrıntısı** (Veri Kaynakları → kaynak): her çalışmanın modu (tam liste/değişenler), işlenen ve değişen sayısı gösterilir. Olay omurgasıyla gelen çalışmada ayrıca geçidin kabul ettiği ve bağlayıcının bildirdiği sayı, "bağlayıcı tamamlanmadı" işareti ve bağlayıcının gerekçesi (`notes`; sır içermez, §3) görünür.
- **Bağlayıcı kimlikleri:** yeni `data_source/connector_read` izni (Veri sorumlusu, Denetçi) ile görünür. Gösterilenler ad, durum (etkin/süresi doldu/iptal), son kullanım, geçerlilik sonu, veren, hız sınırı ve belirtecin son dört karakteri. Çalışmayı açan kimliğin adı da yalnızca bu izinle döner.
- **Sırsız görünüm:** API rolü `ingest.connector_credential` tablosunu yine okuyamaz. Yalnızca `ingest.connector_credential_status` görünümünü okur (`security_barrier`); görünümde belirteç özeti yoktur (testli).
- **Sonraki adım (U5.3b):** kimlik verme/iptal ve "şimdi çalıştır" `connector_admin` izniyle yapılacak (izin 0018'de tanımlandı, uç henüz yok). Zamanlama da bu adımdadır. Belirteç yalnızca verildiği anda bir kez gösterilecek; çalıştırma isteği kaynak sırlarına erişen ayrı bir çalıştırıcı servisinde yürütülecek (API kaynak sırlarını görmez).

## Sonuçlar

- Yeni kaynak eklemek için üç iş gerekir: `sources/<kaynak>.py` (istemci ve mümkünse simülatör), `SOURCES` kaydı ve eşleme dosyası. Çalıştırıcı, geçit istemcisi ve güvenlik kuralları ortaktır.
- Delta: Zabbix ve vCenter'da her çalışma tam listedir. vCenter'da değişiklik akışı (`WaitForUpdatesEx`) ileride delta için kullanılabilir. ServiceNow ve Defender U5.2'de `since` parametresini kullanacak.
- Ham yük arşivi (`raw_ref`, F1-14) için çalıştırıcıda kanca var (`archive`); arşiv deposu henüz yok.
- Bilinen sınır: yeniden deneme süreç içindedir. Çalıştırıcı süreci ölürse çalışma yarıda kalır ve uzlaştırıcı onu eskiyen çalışma olarak tamamlanmamış kapatır. Kaldığı yerden devam yoktur; sonraki çalışma tam listeyi yeniden gönderir.

## Alternatifler

- **`httpx` veya `requests`:** Daha kısa kod, bağlantı havuzu ve HTTP/2 sağlar. Ancak ek bağımlılık zinciri getirir ve bu yüzeyde gerekmez. Reddedildi.
- **Bağlayıcıyı geçidin içine gömmek:** Kaynak sırları ile geçidin veritabanı erişimi aynı süreçte toplanırdı. Kural 2'yi zayıflatır. Reddedildi.
- **Yalnızca kaydedilmiş yanıtlarla test:** Sayfa boyutu gibi davranışları sınamak zordur. Simülatör ve kayıt birlikte kullanıldı.
