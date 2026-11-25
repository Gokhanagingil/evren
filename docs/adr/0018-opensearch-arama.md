# ADR-0018: CI araması OpenSearch'te — olaydan dizinleme, yetki sorguda, PostgreSQL yedeği, ek bağımlılık yok

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

ADR-0002 arama ve analiz için OpenSearch'ü seçti. U1 ile OpenSearch yığında ayağa kalktı; U2 ile CI değişiklikleri Kafka'ya olay olarak yayınlanıyor. Arayüzdeki küresel arama (Ctrl+K) ve CI seçici o güne kadar PostgreSQL'de `LIKE` ile çalışıyordu. Bu yöntem önekle aramayı ve yazım hatasını tanımıyordu. Türkçe büyük/küçük harfte de hata veriyordu: Python'da `"İ".lower()` "i̇" (birleşik nokta) üretir.

## Karar

### 1. Ek bağımlılık yok: standart kütüphaneyle ince REST istemcisi

`evren_core/opensearch_client.py` (çekirdekte OpenSearch'e dokunan tek modül) yalnızca kullanılan uçları sarar: küme sağlığı, dizin oluşturma/silme, takma ad, toplu yazım (`_bulk`) ve arama. Resmi `opensearch-py` paketi urllib3, requests, certifi ve dateutil zincirini getirir; bu kadar küçük bir yüzey için tedarik zinciri riskini artırmaya değmez.

- **Güvenli varsayılanlar:**
  - HTTPS ve sertifika zinciri ile ana bilgisayar adı doğrulaması kullanılır (`EVREN_OPENSEARCH_CA_FILE`).
  - Düz HTTP yalnızca açık `EVREN_OPENSEARCH_ALLOW_HTTP=1` ile çalışır (yerel geliştirme).
  - Kimlik bilgisi adreste reddedilir. Parola yalnızca dosyadan okunur ve `repr`'da görünmez.
  - Hata iletileri sunucu yanıt gövdesini yansıtmaz.
  - Dizin adı düzenli ifadeyle doğrulanır; yol enjeksiyonu yapılamaz.

### 2. Dizin tasarımı (`evren_core/search.py`, saf ve testli)

- **Parçalama ve katlama:**
  - Ad, FQDN, IP, seri, MAC, varlık etiketi ve işletim sistemi harf/rakam dışı karakterlerden parçalanır.
  - Türkçe küçük harf ve ASCII katlama birlikte uygulanır: "İST-DB-01", "ist-db-01" ve "ıst db" aynı belgeyi bulur.
- **Alt alanlar:**
  - `prefix` (kenar n-gram) önekle arama içindir.
  - `raw` (normalleştirilmiş tam değer) tam eşleşme içindir.
  - `compact` (ayırıcısız) "istdb01" ve "redhat" gibi yazımlar içindir.
- **Sorgu:**
  - Ağırlık sırası: sys_id ve tam kimlik değerleri, ayırıcısız tam değer, ayırıcısız önek, parça önekleri (hepsi gerekli), bulanık eşleşme (hepsi gerekli, `AUTO`).
  - IP biçimindeki sorgu yalnızca IP önekiyle aranır; benzer IP'ler gürültü üretmez.
  - Kullanıcının girdisi sorgu sözdizimine girmez (`query_string` kullanılmaz). Girdi JSON değeri olarak iletildiği için enjeksiyon yoktur.
- **Sürümlü dizin:**
  - Dizin adı `evren-cmdb-ci-v<MAPPING_VERSION>-<zaman>` biçimindedir ve `evren-cmdb-ci` takma adı arkasında durur.
  - Eşleme değişince sürüm artar ve dizinleyici açılışta yeniden dizinler: yeni dizin dolar, takma ad atomik taşınır, eski dizin silinir. Kesinti olmaz.
- **Katı eşleme (`dynamic: strict`):** Hassasiyeti `confidential` veya `restricted` olan alanlar dizine hiç girmez. Aynı kural olay akışında da geçerlidir (`hidden_fields`, tek kaynak).

### 3. Dizinleyici (`services/indexer`, `evren indexer`)

- `evren.cmdb.ci.changed.v1` konusunu ayrı bir tüketici grubuyla (`evren-indexer`) okur.
- Olayın içeriğini değil, CI'ın PostgreSQL'deki **güncel** hâlini dizinler. Olaylar sırasız veya tekrar gelse de dizin doğru kalır; en az bir kez teslim yeterlidir.
- Ofset yalnızca OpenSearch yazımı başarılı olunca saklanır. Hata durumunda bölümler toplu okumanın ilk mesajına geri sarılır ve üstel beklemeyle yeniden denenir.
- `--reindex` tam yeniden dizinleme yapar. Tarama sırasında değişen CI'lar takma ad taşındıktan sonra bir kez daha yazılır; böylece kayıp aralığı kapanır.
- Yeni grup rolü `evren_app_indexer` (göç 0013): okuyucu hakları, kaynak bağından yalnızca `ci_sys_id, data_source, active` ve kendi nabzı. İş verisini değiştiremez. Kaynak anahtarı, kaynak kaydı, kimlik bilgisi ve kısıtlı güvenlik tablosu ona kapalıdır (testli). Giriş rolü `evren_indexer_svc`'dir.

### 4. Arama API'si: yetki motora bırakılmaz

`GET /api/v1/search?q=&limit=`:

- CI okuma izni gerekir.
- Kullanıcının okuyabildiği sınıflar sorguya **süzgeç** olarak, görebildiği alanlar **arama alanı** olarak verilir: IP'yi göremeyen kullanıcı IP ile CI bulamaz.
- Yanıttaki her alan sınıf bazında hassasiyetle yeniden süzülür. Motorun süzgeci herhangi bir nedenle atlanırsa okunamayan sınıf yanıta yine girmez (testli).
- OpenSearch tanımlı değilse veya ulaşılamıyorsa PostgreSQL yedeği kullanılır. Yedek, `ascii_fold` ve SQL'de aynı Türkçe katlamayla çalışır; joker karakterler kaçırılır. Yanıt `engine` (`opensearch` | `postgresql`) ve `degraded` bilgisini taşır; arayüz yedeğe düşüşü grup başlığında söyler.
- Arayüz: Ctrl+K paleti bu ucu kullanır. Motorun bulduğu öğeler (yazım hatası, önek) istemci tarafında yeniden süzülmez.

## Değerlendirilen seçenekler

- **opensearch-py:** Olgun ama dört geçişli bağımlılık getirir. Kullanılan uç sayısı küçük.
- **Arama için yalnızca PostgreSQL (pg_trgm, tsvector):** pg_trgm benzerlik araması için iyidir. Ancak ADR-0002'deki trend ve anomali (Random Cut Forest) hedefi OpenSearch'tedir; ayrıca arama yükü kayıt sistemine binerdi. PostgreSQL yedek olarak kaldı.
- **Olay içeriğinden dizinleme:** Olay sırası ve tekrarı dizini bozabilirdi. Güncel hâli okumak basit ve doğrudur. Maliyeti, olay başına küçük bir okuma sorgusudur.
- **Yetkiyi OpenSearch güvenlik eklentisinde (belge/alan düzeyi) uygulamak:** Evren'in RBAC + hassasiyet modelini ikinci bir yerde tanımlamak gerekir. Tek kaynak API'deki yetkidir.

## Sonuçlar

- Duman testi zinciri sınar: yeni CI → olay → dizinleyici → OpenSearch → API. Türkçe büyük harf, yazım hatası ve ayırıcısız yazımla bulma da sınanır.
- `test_search_os.py` gerçek OpenSearch'e karşı 15 alaka ve yetki denetimi yapar; CI "Yığın" işinde çalışır.
- **Bilinen sınırlar:**
  - CI listesi sayfası (süzgeç ve imleçli sayfalama) hâlâ PostgreSQL'de; küresel arama OpenSearch'te.
  - Bir-iki karakterlik parçalarda yazım hatası toleransı yoktur ("0l" → "01" bulunmaz). Bu, kısa kimliklerde yanlış eşleşmeyi önlemek için bilinçli bir sınırdır.
  - İlişkiler ve sınıfa özgü diğer öznitelikler dizinde yok (CI 360°, F1-08).
  - Trend ve anomali (RCF) U8 ve sonrasındadır.
  - Geliştirme yığınında OpenSearch güvenlik eklentisi kapalıdır. Üretimde TLS ve kimlik zorunludur; dizinleyicinin OpenSearch kullanıcısı yalnızca `evren-cmdb-ci*` dizinlerine yazabilmeli, API'ninki yalnızca okuyabilmelidir.
