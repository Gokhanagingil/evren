# ADR-0024: Ham yük arşivi — çalıştırıcı tarafında şifreli parça dosyaları, saklama süresi, arşivden yeniden işleme

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U5.4, F1-14), mimari, güvenlik

## Bağlam

Bağlayıcı kaynaktan ham nesneyi çeker, eşleme dosyasıyla `IngestRecord` üretir ve ham nesneyi atar. `IngestRecord.raw_ref` alanı ve çalıştırıcıdaki `archive` kancası baştan beri vardı (ADR-0020), ama arşiv yoktu. Bu yüzden üç ihtiyaç karşılanamıyordu:

1. **Sorun analizi:** "Kaynak bu değeri gerçekten böyle mi gönderdi?" sorusu, kaynak değiştikten sonra yanıtlanamıyordu.
2. **Yeniden işleme:** Eşleme düzeltilince kaynağa yeniden gitmek gerekiyordu. Kaynak yavaş, hız sınırlı (Defender dakikada 100 çağrı) veya bakımda olabilir.
3. **Kanıt:** Gölge mod ve ürün değişimi kararlarında karşılaştırmanın girdisi saklanmıyordu.

Ham yük bütün kaynak alanlarını taşır. Bu alanlar arasında eşlemenin bilerek almadığı kişisel veri ve kısıtlı güvenlik verisi de vardır (Defender maruziyeti). Bu yüzden arşiv **kısıtlı veri** sınıfındadır.

## Karar

1. **Yer: çalıştırıcı tarafında dosya sistemi** (`EVREN_ARCHIVE_DIR`). Ham yükü zaten gören tek bileşen bağlayıcı sürecidir. Arşiv aynı makinede kalırsa kaynak verisi yeni bir ağ yolundan geçmez; API, uzlaştırıcı ve veritabanı arşivi görmez. Dizin kurumun şifreli ve yedekli bir birimine (SAN, NFS, nesne deposu bağlaması) bağlanabilir. S3 uyumlu doğrudan yazım ileride ayrı bir yazıcıyla eklenir; biçim aynı kalır.

2. **Biçim:** her çalışma ayrı bir dizindir: `<kaynak>/<YYYY-MM-DD>/<run_id>/`.
   - **Parça dosyaları** (`segment-00001.enc` …): en çok 5000 ham nesne, satır başına bir JSON (NDJSON). Önce gzip ile sıkıştırılır, sonra şifrelenir.
   - **Manifest** (`manifest.json`): sürüm, kaynak, `run_id`, mod, başlangıç ve bitiş zamanı, parça adları, kayıt sayıları, şifreli dosyaların SHA-256 özetleri, anahtar kimliği (anahtar özetinin ilk 12 hanesi) ve çalışmanın tamamlanma durumu. **Kaynak verisi içermez.** En son yazılır; manifesti olmayan dizin yarım arşivdir.
   - Dosyalar atomik yazılır (geçici dosya + yeniden adlandırma); izinler dizinde 0700, dosyada 0600.
   - **`raw_ref`:** `archive:<kaynak>/<tarih>/<run_id>/<parça no>/<satır no>`. Göreli bir başvurudur, makine yolu içermez. Kaynak kaydında (`ingest.source_record.raw_ref`) saklanır.

3. **Şifreleme: zorunludur.**
   - Fernet kullanılır (AES-128-CBC + HMAC-SHA256). Bu, API oturumlarında zaten kullanılan `cryptography` paketindedir; **yeni bağımlılık yoktur**.
   - Anahtar dosyadan okunur (`EVREN_ARCHIVE_KEY_FILE`). Dosyada birden çok satır olabilir; ilk satır şifreler, hepsi çözer. Böylece anahtar döndürmede eski arşivler okunmaya devam eder.
   - Anahtar yoksa arşiv açılmaz (yapılandırma hatası). Düz metin arşiv seçeneği yoktur.

4. **Bütünlük:** okuyucu her parçanın özetini manifestle karşılaştırır, sonra şifreyi çözer. Uymayan özet, yanlış anahtar ve bozuk dosya açık hatayla durur. Hata iletisinde içerik yoktur.

5. **Saklama:**
   - Süre `EVREN_ARCHIVE_RETENTION_DAYS` ile belirlenir (varsayılan 30 gün; kişisel ve kısıtlı veri için kısa tutulur).
   - Çalıştırıcı servisi günde bir kez süresi dolan çalışma dizinlerini siler. Elle de silinebilir: `evren connector-archive prune`.
   - Silme yalnızca arşiv kökünün altında, `<kaynak>/<tarih>/<run_id>` biçimine uyan dizinlerde yapılır.
   - Kaynak kaydındaki `raw_ref` kalır; arşiv silinmişse yeniden işleme "arşiv yok" der.

6. **Arşivden yeniden işleme:** `connector_run.py --from-archive <çalışma dizini>`.
   - Arşivdeki ham nesneler **bugünkü** eşleme dosyalarıyla yeniden eşlenir ve alım geçidine **yeni bir çalışma** olarak gönderilir; kural 1 ve 2 değişmez.
   - Yeni çalışma asıl çalışmanın modunu kullanır. Asıl çalışma tamamlanmamışsa yeni çalışma `delta` olur; böylece eksik arşiv "kaynakta yok" tespitine yol açmaz.
   - Kayıtlar arşivdeki asıl `raw_ref`'i taşır.
   - Kaynağa hiç bağlanılmaz.

7. **Erişim:**
   - Arşivi yalnızca çalıştırıcı makinesi ve oradaki operatör görür.
   - Arayüzde ve API'de ham yük gösterilmez. Gösterilmesi gerekirse ayrı karar ve kısıtlı yetki gerekir.

## Değerlendirilen seçenekler

- **PostgreSQL'de ham yük (`jsonb`/`bytea`):** Yedekleme ve erişim kolaylaşır. Ama golden record veritabanı büyür ve kısıtlı veri API'nin bağlandığı veritabanına girer. Reddedildi.
- **S3/MinIO istemcisiyle doğrudan nesne deposu:** Yeni bağımlılık (boto3) veya imzalı istek kodu gerekir. Hedef ortam kararı (U10) beklenirken erkendir. Dosya sistemi bağlaması aynı biçimle sonradan taşınabilir. Ertelendi.
- **Arşivi geçitte veya Kafka'da tutmak (konu saklama süresi):** Kafka kayıtları eşlenmiş kayıtlardır, ham yük değildir. Ham yükü geçide taşımak kısıtlı veriyi yeni bir yoldan geçirir. Reddedildi.
- **Şifresiz arşiv + disk şifrelemesi:** Disk şifrelemesi makine çalışırken koruma sağlamaz. Uygulama düzeyinde şifreleme, yedekte ve taşımada da korur. Reddedildi; disk şifrelemesi ek önlem olarak önerilir.

## Sonuçlar

- **Olumlu:**
  - Eşleme düzeltilince kaynağa gitmeden yeniden işleme yapılabilir.
  - Sorun analizi kaynağın gönderdiği ham değerle yapılabilir.
  - Kanıt saklanır; saklama süresi otomatik uygulanır.
- **Olumsuz:**
  - Disk alanı gerekir; sıkıştırma ile çalışma başına genelde birkaç MB tutar.
  - Anahtar kaybı arşivi okunamaz yapar. Anahtar kurum sır kasasında yedeklenmelidir.
- **Riskler:**
  - Arşiv dizini yanlışlıkla geniş erişimli bir birime bağlanırsa şifreleme yine korur, ama manifest meta verisi (kaynak, zaman, sayılar) görünür.
  - Saklama süresinin kurum politikasıyla (KVKK, iç denetim) uyumu kurumda onaylanmalıdır (U10).

## Güvenlik ve uyum etkisi

- Tehdit modeline şu satırlar eklenir:
  - arşivin okunması (şifreleme, izinler);
  - kurcalanması (özet denetimi);
  - yol geçişi (dizin adı doğrulaması);
  - saklama süresi;
  - yeniden işlemenin kural 1 ve 2'yi atlamaması.
- KVKK: veri en aza indirme ilkesi saklama süresiyle uygulanır. Kişisel veri arşivden yalnızca süre dolunca silinir; tek kişinin silinmesi arşivde desteklenmez. Bu sınır yazılıdır.
- OWASP ASVS 2: V6.2 (onaylı algoritma), V6.4 (anahtar yönetimi), V8.3 (hassas veri saklama), V12.3 (dosya yolu).

## Doğrulama

- **Birim:**
  - yazma ve okuma (parça sınırı, satır başvurusu);
  - manifestte veri olmaması;
  - kurcalanmış parça ve yanlış anahtarın açık hatayla durması (içerik iletiye yazılmaz);
  - anahtar döndürme;
  - yol geçişinin reddi;
  - saklama süresiyle silme (yalnızca biçime uyan dizinler);
  - dosya izinleri.
- **Uçtan uca:** simülatörle arşivli çalışma, ardından `--from-archive` ile kaynaksız yeniden işleme. Aynı kayıtlar üretilir ve `raw_ref` korunur.
- **Yığın duman testi:** çalıştırıcı servisi arşivli çalışır; arşiv listesinde çalışma ve kayıt sayısı görünür.
