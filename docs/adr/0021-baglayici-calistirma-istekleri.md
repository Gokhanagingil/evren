# ADR-0021: Bağlayıcı çalıştırma istekleri, ayrı çalıştırıcı servisi, zamanlama ve arayüzden kimlik verme

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U5 kapsamı), mimari

## Bağlam

ADR-0020 ile altı kaynağın istemcisi, eşleme ve geçide gönderim hazır. Ancak bağlayıcı yalnızca komut satırından çalışıyor (`tools/connector_run.py`, `evren connector-run`). U5'in kalan maddeleri:

1. Arayüzden **"şimdi çalıştır"**.
2. `data_sources.yaml` içindeki `schedule` alanına göre **zamanlanmış çalışma**.
3. Geçit belirtecinin (bağlayıcı kimliği) **arayüzden verilmesi ve iptali**. Bugün bu iş yalnızca yönetici veritabanı bağlantısıyla yapılıyor (`evren connector issue`).

Üç kısıt var:

- **API kaynak sırlarını görmemeli.** API internete en yakın servis. vCenter parolası, Defender istemci sırrı veya Zabbix belirteci API sürecine girerse API'deki bir açık bütün kaynakları açar.
- **Kural 2:** Bağlayıcı veri çeker ve `IngestRecord` üretir. CMDB'ye yazmaz. Bağlayıcıyı çalıştıran süreç de golden record'a yazamamalı.
- **Belirteç bir kez görünür.** Veritabanında yalnızca SHA-256 özeti durur (ADR-0017). Arayüzden verilen belirteç de günlüğe, tekrar güvenliği kaydına (`api.idempotency`) veya önbelleğe girmemeli.

## Karar

### 1. Çalıştırma isteği tablosu: `ingest.connector_run_request` (göç 0019)

API çalıştırmaz, **istek yazar**. Çalıştırıcı servisi isteği alır ve yürütür. Desen, ayırma ve elle düzenleme istekleriyle aynıdır (göç 0007, 0008): API yazar, ayrı servis uygular.

- Sütunlar: bağlayıcı örneği (`instance`), veri kaynağı, mod (`snapshot`/`delta`), `since`, köken (`manual`/`schedule`), zamanlanan dakika (`scheduled_for`), isteyen, not; durum, çalıştırıcı, başlangıç/bitiş, `run_id` (geçitteki `ingest.source_run` ile aynı), çıkış kodu, özet (`summary`: yalnızca sayılar), sonuç notu, iptal isteği.
- Durumlar: `pending` → `running` → `succeeded` | `failed` | `cancelled`; bekleyen istek `cancelled` veya `rejected` olabilir. Geçişler tetikleyiciyle zorlanır. Sonuçlanmış istek değiştirilemez.
- **Bir örnek için aynı anda tek etkin istek** (kısmi benzersiz dizin). İkinci "şimdi çalıştır" 409 döner. Zamanlayıcı etkin istek varken yeni istek açmaz.
- **Zamanlanmış istek tekrarlanmaz:** `(instance, scheduled_for)` benzersizdir. Birden çok çalıştırıcı örneği aynı dakikayı iki kez açamaz.
- Yetki sütun bazındadır. API yalnızca istek sütunlarını ekler; köken sütununa yetkisi yoktur, varsayılan `manual` olur. İptal için yalnızca `cancel_requested_*` sütunlarını günceller. Çalıştırıcı yalnızca durum ve sonuç sütunlarını günceller, isteğin içeriğini değiştiremez.
- Yeni istek ve iptal isteği çalıştırıcıyı `LISTEN evren_connector` ile hemen uyandırır. Bildirim yük taşımaz; iş her zaman tablodan okunur.

### 2. Bağlayıcı örneği: çalıştırıcı bildirir, API okur (`ingest.connector_instance`)

Bağlayıcı örneği, bir kaynak istemcisinin bir kurulumdaki karşılığıdır (ör. `zabbix-dc1`, `zabbix-dc2`). Yapılandırması (kaynak adresi, sır dosya yolları, geçit belirteci yolu) **yalnızca çalıştırıcıda** durur: `EVREN_CONNECTORS_FILE` (YAML).

Çalıştırıcı açılışta ve her nabızda örneklerini veritabanına bildirir. Bildirilenler: ad, istemci, veri kaynağı, etkin zamanlama, zaman dilimi, etkin mi, çalıştırıcı kimliği, son görülme. **Adres, kullanıcı adı ve dosya yolu bildirilmez.** Arayüz "şimdi çalıştır" düğmesini yalnızca son 3 tarama aralığı içinde görülen örnekler için gösterir. API de yalnızca bildirilmiş örnek için istek kabul eder.

### 3. Ayrı çalıştırıcı servisi: `evren connector-runner` (`services/connector-runner`)

- **Yeni grup rolü `evren_app_connector`:** CMDB'yi okuyamaz ve yazamaz. Yetkileri: istek tablosunun durum ve sonuç sütunları, zamanlanmış istek ekleme, kendi örnek kayıtları, kendi nabzı (`worker_heartbeat.kind = 'connector'`) ve güvenlik olayı ekleme. Kaynak verisi veritabanına yalnızca **geçit üzerinden** gider (kural 1 ve 2 değişmez).
- **Her çalışma ayrı süreçtir.** Çalıştırıcı, ADR-0020'deki komut satırını (`tools/connector_run.py`) alt süreç olarak başlatır. Seçilme nedenleri:
  1. **Süre sınırı** gerçek olur (`max_runtime_minutes`, varsayılan 120; aşılırsa süreç sonlandırılır).
  2. **İptal** gerçek olur (SIGTERM, 10 saniye sonra SIGKILL).
  3. Bozuk bir kaynak yanıtı ya da bellek taşması çalıştırıcıyı düşürmez.
  4. Elle çalıştırma ve zamanlanmış çalıştırma **aynı kod yolunu** kullanır.
- **Alt sürecin ortamı en az hâliyle kurulur.** Çalıştırıcının kendi ortamı (veritabanı adresi dosyası dâhil) aktarılmaz. Yalnızca o örneğin `EVREN_SOURCE_*` ve `EVREN_GATEWAY_*` değişkenleri verilir. Bunlar sırrın kendisi değil, **dosya yoludur** (ADR-0020 §3).
- `run_id` çalıştırıcıda üretilir ve alt sürece `--run-id` ile verilir. Geçitte açılan çalışma ile istek aynı kimliği taşır; arayüz isteği çalışma sonucuna bağlar.
- Sonuç: alt sürecin son satırı JSON rapordur (ADR-0020 §7). İstekte yalnızca sayılar saklanır (çekilen, eşlenen, gönderilen, atlanan, hata, sınıf dağılımı). Standart hata çıktısının ilk satırı en çok 300 karakterle sonuç notuna yazılır. Bu iletiler sır içermeyecek biçimde üretilir (ADR-0020 §3); yine de çalıştırıcı, örneğin dosya yollarını ve kaynak adresini notta yer tutucuyla değiştirir.
- Çıkış kodu 0 ise `succeeded`, diğer durumlarda `failed` olur. Süre aşımı ve iptal ayrıca belirtilir.
- **Eşzamanlılık:** çalıştırıcı başına en çok `EVREN_CONNECTOR_MAX_PARALLEL` (varsayılan 2) çalışma yürür. İstek `FOR UPDATE SKIP LOCKED` ile alınır; birden çok çalıştırıcı örneği güvenle çalışır. Çalıştırıcı yalnızca kendi yapılandırmasındaki örnekleri alır. Ağ bölgesine göre ayrı çalıştırıcı kurulabilir (ör. DMZ'deki vCenter için ayrı makine).
- **Kurtarma:**
  - Nabzı 3 tarama aralığından uzun süredir görülmeyen çalıştırıcının `running` istekleri başka bir çalıştırıcı tarafından `failed` ("çalıştırıcı yanıt vermiyor") yapılır. Geçitteki yarım çalışmayı uzlaştırıcı zaten tamamlanmamış kapatır (ADR-0017).
  - 24 saatten uzun bekleyen istek `rejected` ("süresi doldu") olur.
  - Emekli veri kaynağının isteği `rejected` olur.

### 4. Zamanlama

- **Kaynak:** `data_sources.yaml` → `schedule` (5 alanlı cron). Örnek yapılandırmasında `schedule` ile ezilebilir veya `schedule: null` ile kapatılabilir. Metadata doğrulayıcısı cron ifadesini denetler (`load_metadata.py --check`).
- **Ayrıştırıcı standart kütüphanededir** (`evren_core/schedule.py`); ek bağımlılık yok. Desteklenenler: `*`, sayı, aralık `a-b`, adım `*/n` ve `a-b/n`, liste `a,b`. Ay ve gün adları desteklenmez. Gün ve haftanın günü birlikte kısıtlıysa Vixie cron gibi **VEYA** uygulanır. Haftanın günü 0 ve 7 pazardır.
- **Zaman dilimi:** `EVREN_SCHEDULE_TZ`, varsayılan `Europe/Istanbul` (standart kütüphane `zoneinfo`; imajda `tzdata` var). Bulunamazsa çalıştırıcı açılmaz (yapılandırma hatası).
- **Kaçırılan dakika telafi edilmez.** Çalıştırıcı her turda son turdan bu yana geçen dakikaları en çok 60 dakika geriye kadar tarar. Daha uzun kesinti sonrası yalnızca bir sonraki zamanlama çalışır ve atlanan zamanlamalar sayılıp günlüğe yazılır. Gerekçe: kaçırılan 4 vCenter çalışmasını art arda yapmak bilgi getirmez, yalnızca yük getirir; bir tam liste yeterlidir.
- **Mod:** zamanlanmış çalışma varsayılan olarak `snapshot`'tır. Örnek yapılandırmasında `schedule_mode: delta` verilirse `since`, aynı örneğin son başarılı isteğinin başlangıcından 10 dakika öncesidir (saat kayması ve sınırdaki kayıtlar için örtüşme). Başarılı istek yoksa çalışma `snapshot` olur. Eskime ve "kaynakta yok" tespiti yalnızca tam liste ile yapılır (ADR-0017); bu yüzden delta zamanlanan örnek için ayrıca günlük tam liste zamanlaması önerilir.

### 5. Arayüzden kimlik verme ve iptal (`connector_admin`)

- **Belirteç API'de üretilir, veritabanına yalnızca özeti gider.** API rolü `ingest.connector_credential` tablosuna doğrudan yazamaz ve tablonun özet sütununu okuyamaz. Kimlik iki `SECURITY DEFINER` işlevle verilir ve iptal edilir: `ingest.issue_connector_credential(...)` ve `ingest.revoke_connector_credential(...)`. İşlevler şunları denetler:
  - ad biçimi;
  - veri kaynağının varlığı ve emekli olmaması;
  - geçerlilik (1–730 gün) ve hız sınırı;
  - özetin biçimi.

  İşlevler denetim kaydını (`audit.security_event`) aynı işlemde yazar. Sabit `search_path` ve tam nitelikli adlar kullanılır (göç 0007 deseni).
- **Belirteç yanıtta bir kez görünür.** Yanıt `Cache-Control: no-store` başlığını taşır. Tekrar güvenliği kaydına belirteçsiz yanıt yazılır; aynı `idempotency_key` ile tekrar gelen istek belirteci **döndürmez**, "belirteç yalnızca ilk yanıtta gösterildi" der. Arayüz belirteci bir iletişim kutusunda gösterir: kopyala düğmesi ve "bu pencere kapanınca belirteç bir daha gösterilmez" uyarısı. Kapanınca alan temizlenir.
- **İptal** ad ile yapılır; iptal edilen kimlik geçitte hemen reddedilir (ADR-0017). Verme ve iptal için ayrı onay adımı yoktur. Kimlik veri kaynağına bağlıdır ve veren kişi denetim izinde görünür. Ayrılık ilkesi gerekirse ileride `iam.sod_constraint` ile eklenir.
- `evren connector issue|revoke` komutu yönetici bağlantısıyla çalışmaya devam eder (kurulum ve kurtarma).

### 6. API uçları

| Uç | İzin | Açıklama |
|---|---|---|
| `GET /api/v1/sources` | `cmdb_ci/read` | Kaynak başına `connectors` (bildirilen örnekler, sonraki zamanlama) ve `requests` (son 20 istek) alanları yalnızca `data_source/connector_read` ile döner |
| `POST /api/v1/sources/{ad}/run-requests` | `data_source/connector_admin` | Örnek, mod, `since`, not, `idempotency_key`. 202 döner. Etkin istek varsa 409; örnek bildirilmemiş veya kaynak emekliyse 422 |
| `POST /api/v1/run-requests/{id}/cancel` | `data_source/connector_admin` | Bekleyen isteği hemen `cancelled` yapar; yürüyen isteğe iptal isteği yazar, çalıştırıcı süreci sonlandırır |
| `POST /api/v1/sources/{ad}/credentials` | `data_source/connector_admin` | Ad, geçerlilik günü, dakikalık sınır, `idempotency_key`. 201 döner; belirteç bir kez |
| `POST /api/v1/credentials/{ad}/revoke` | `data_source/connector_admin` | Kimliği iptal eder |

Tüm yazma uçları CSRF başlığını, aynı köken denetimini ve güvenlik olayı kaydını kullanır (mevcut desen).

## Değerlendirilen seçenekler

- **API'nin bağlayıcıyı kendisinin çalıştırması:** En kısa yol, ama kaynak sırları API sürecine girer. Reddedildi.
- **Zamanlamayı Kubernetes CronJob veya systemd zamanlayıcısına bırakmak:** Platforma bağlılık getirir ve "şimdi çalıştır" ile zamanlanmış çalışma ayrı yollardan geçer. Arayüz, sonraki çalışmayı ve kaçırılan zamanlamayı göremez. Reddedildi. Kurum isterse `--once` ile dış zamanlayıcıdan tek tur çalıştırmak mümkündür.
- **Çalıştırmayı süreç içinde (iş parçacığı) yapmak:** Python'da iş parçacığı dışarıdan durdurulamaz; süre sınırı ve iptal ancak her sayfada denetimle mümkün olurdu, bozuk yanıt çalıştırıcıyı düşürebilirdi. Reddedildi.
- **Zamanlama için `croniter` veya APScheduler:** Ek bağımlılık zinciri getirir. Gereken alt küme küçük ve testle tam kapsanabilir. Reddedildi.
- **Belirteci veritabanında üretmek (`pgcrypto`):** Düz belirteç veritabanı oturumunda ve günlük ayarına bağlı olarak sunucu günlüğünde görünebilirdi. API'de üretip yalnızca özeti göndermek bunu kapatır. Reddedildi.
- **Örnek yapılandırmasını veritabanında tutmak:** Kaynak adresi ve sır yolu API'nin okuyabileceği yere taşınır, sır kasası entegrasyonu dosya tabanlı kalır. Reddedildi; yapılandırma çalıştırıcıda, görünürlük için yalnızca sırsız özet veritabanında.

## Sonuçlar

- **Olumlu:**
  - Kaynak sırları yalnızca çalıştırıcı makinesinde durur.
  - API'deki bir açık kaynaklara erişim vermez; en kötü durumda istek yazar ve kimlik verir. Bunlar da `connector_admin` izni ve denetim izi gerektirir.
  - Elle ve zamanlanmış çalıştırma aynı yoldan geçer ve aynı geçmişte görünür.
  - Ağ bölgesine göre birden çok çalıştırıcı kurulabilir.
- **Olumsuz:** Yeni bir servis, rol ve iki tablo eklenir. Çalıştırıcı başına ayrı yapılandırma dosyası bakımı gerekir.
- **Riskler:**
  - Çalıştırıcı makinesi bütün kaynak sırlarını taşır, bu yüzden en yüksek değerli hedeftir. Önlemler: CMDB yetkisi yok; dışarıya yalnızca kaynaklara ve geçide çıkış; sırlar kasadan dosya olarak bağlanır.
  - Zaman dilimi yanlış ayarlanırsa çalışmalar kayar. Arayüz zaman dilimini ve sonraki çalışma zamanını gösterir.

## Güvenlik ve uyum etkisi

- Tehdit modeli (`docs/06-guvenlik.md`) güncellendi:
  - çalıştırıcı servisi;
  - istek tablosu üzerinden kötüye kullanım (hız ve tek etkin istek sınırı);
  - belirtecin yanıt dışında görünmemesi;
  - alt süreç ortamının sızmaması.
- İzin matrisi (`docs/07-yetkilendirme.md`): `connector_admin` artık uçlara bağlı.
- OWASP ASVS 2: V2.10 (servis kimlikleri), V3 (oturum ve CSRF), V7 (günlükte sır yok), V8.3 (hassas veri önbelleğe girmez), V13 (API).

## Doğrulama

- **Birim:**
  - cron ayrıştırıcı: alanlar, aralık, adım, liste, VEYA kuralı, geçersiz ifadeler;
  - sonraki zamanlama ve yaz saati olmayan dilim;
  - yapılandırma dosyası: sır değil yol, bilinmeyen anahtar reddi, https zorunluluğu.
- **PostgreSQL:**
  - API rolü istek ekler ama durum yazamaz, kökeni değiştiremez ve kimlik tablosuna yazamaz;
  - çalıştırıcı rolü CMDB'yi okuyamaz;
  - geçiş tetikleyicisi geçersiz geçişi reddeder;
  - tek etkin istek ve zamanlanmış istek tekrarı engellenir;
  - kimlik işlevleri emekli kaynağa ve bozuk özete kimlik vermez;
  - denetim kaydı yazılır.
- **Çalıştırıcı:** simülatörle uçtan uca istek, alt süreç, geçit ve sonuç; süre aşımı; iptal; çökmüş çalıştırıcının isteğinin kurtarılması; zamanlayıcının dakikayı bir kez açması.
- **API:**
  - izinsiz kullanıcı uçları kullanamaz ve örnekleri göremez;
  - belirteç yanıtta bir kez görünür, tekrarda dönmez, veritabanında yalnızca özeti durur;
  - `Cache-Control: no-store` başlığı var.
- **Tarayıcı:** Veri sorumlusu "Şimdi çalıştır" ve "Kimlik ver" akışlarını klavyeyle tamamlar; belirteç iletişim kutusu kapanınca belirteç sayfada kalmaz.
- **Yığın duman testi:** çalıştırıcı servisi simülatörle, zamanlanmış ve elle istekleri geçide gönderir.
