# 12 · Operasyon ve ölçek

## Hacim tahmini (40.000 sunucu + 100.000 ağ cihazı)

| Veri | Tahmin | Not |
| --- | --- | --- |
| Ana CI (sunucu + ağ cihazı + VM örneği) | ~180.000 | VM'ler ayrı CI |
| Ağ adaptörü + IP CI | 0,5–1 M | Sunucu adaptörleri ve IP'ler |
| Switch portu | 2–4 M | Başlangıçta CI değil (docs/02) |
| Yazılım kurulumu | 8–10 M | `cmdb_sam_sw_install`, hash bölümlü |
| İlişki | 10–20 M | |
| Günlük kaynak gözlemi | 5–10 M | Büyük kısmı değişmez → süzgeç |
| Günlük uzlaştırmaya giren | 50.000–500.000 | %1–5 |
| Denetim satırı | ~100 M/yıl | Aylık bölüm |

## Başlangıç kapasitesi (öneri)

Üretim öncesi yük testiyle doğrulanacak.

| Bileşen | Düğüm | Kaynak |
| --- | --- | --- |
| PostgreSQL | 1 birincil + 1 senkron yedek (+ okuma replikası) | 16–32 vCPU, 128 GB RAM, NVMe, ≥ 2 TB |
| Kafka | 3 broker (KRaft) | 8 vCPU, 32 GB, 1 TB |
| OpenSearch | 3 düğüm | 8 vCPU, 32 GB, 1 TB |
| Valkey | 1 + yedek | 2 vCPU, 8 GB |
| Uygulama servisleri | api ×2, reconciler ×2–4, gateway ×2, sn-sync ×1, mcp ×1 | Yatay ölçeklenir |

## Yüksek erişilebilirlik ve felaket kurtarma

- PostgreSQL: senkron yedek ve otomatik yük devretme (kurum standardı: Patroni veya eşdeğeri). Şifreli PITR yedekleri; aylık geri yükleme tatbikatı.
- Kafka: 3 replika, `min.insync.replicas=2`.
- OpenSearch kayıt sistemi değildir; kaybedilirse golden record'dan yeniden dizinlenir.
- RPO ve RTO iş etki analiziyle belirlenir (ISO 22301). Başlangıç önerisi: RPO 15 dk, RTO 4 saat. CMDB çökerse ServiceNow replika çalışmaya devam eder.

## Gözlemlenebilirlik

- **Metrikler (OpenTelemetry):**
  - Kaynak başına kayıt, değişim ve hata.
  - Devre kesici durumu.
  - Uzlaştırma gecikmesi ve kuyruk derinliği.
  - Eşleştirme kuyruğu yaşı.
  - sn-sync bekleyen ve hata sayısı.
  - API gecikmesi (p95).
- **Loglar:** yapılandırılmış JSON, korelasyon kimliği; sır ve kişisel veri yok.
- **Alarmlar:**
  - Devre kesici OPEN.
  - Eşleştirme kuyruğu 3 günden eski.
  - sn-sync hata oranı.
  - Denetim tetikleyicisi ihlal denemesi.
  - Break-glass kullanımı.
- **Uzlaştırıcı çalışanı (F0-10, ADR-0013):** `ingest.worker_heartbeat` son görülme, işlenen ve başarısız istek sayısı; `/api/v1/summary` → `worker` (durum `ok`/`stale`/`none`, bekleyen istekler). Alarm önerisi: bekleyen istek varken çalışan 2 tarama aralığından uzun süre görülmüyor; başarısız istek sayısı artıyor.
- Evren kendi altyapısını kendi CMDB'sinde CI olarak tutar.

## Dağıtım

- Hedef ortam açık soru: Kubernetes/OpenShift mi, VM üzerinde Docker mı? (Proje sahibi en son paylaşacak; U10.) İki durumda da:
  - imaj imzalı ve taranmış;
  - root olmayan kullanıcı, salt okunur kök dosya sistemi;
  - sırlar dosya olarak bağlanır;
  - ağ politikaları servis bazında tanımlanır.
- Migrasyonlar sürüm kontrollü ve geri alınabilir yazılır. Yıkıcı değişiklikler iki adımda yapılır: önce genişlet, sonra daralt.
- Ortamlar: geliştirme → test → ön üretim → üretim. Ön üretimde anonimleştirilmiş veri kullanılır.

### Bugün hazır olan (U1, ADR-0016)

Üretim dağıtım biçimi hedef ortamla birlikte yazılacak. Aşağıdaki sözleşme o biçimden bağımsızdır ve CI'da her değişiklikte sınanır.

| Parça | Ne | Nerede |
| --- | --- | --- |
| İmaj | Tek Evren imajı; komut servisi seçer: `api`, `gateway`, `worker`, `ingest`, `migrate`, `connector`, `demo`, `healthcheck api\|gateway\|worker\|ingest`. uid 10001, hash'li bağımlılıklar, temel görüntü özetle sabit | `deploy/Dockerfile`, `deploy/evren.py` |
| Alım geçidi | Bağlayıcıların tek giriş noktası (`/ingest/v1`). TLS (`EVREN_GATEWAY_TLS_CERT_FILE`/`_KEY_FILE`), mTLS için `EVREN_GATEWAY_TLS_CLIENT_CA_FILE`. Ağa TLS'siz açılmaz. Yatay ölçeklenir (hız sınırı örnek başına) | ADR-0017 |
| Alım tüketicisi | `evren ingest`: `evren.ingest.record.v1` tüketici grubu (`evren-reconciler`). En fazla bölüm sayısı (6) kadar örnek paralel çalışır | ADR-0017 |
| Çalışan | `evren worker`: insan istekleri, çalışma kapanışı, outbox → Kafka. Birden çok örnek güvenli (`SKIP LOCKED`) | ADR-0013, ADR-0017 |
| Göç | Bir kez çalışan iş. Göçler `meta.schema_migration` defterine (sürüm + SHA-256) yazılır; içeriği değişmiş göçte durur. Metadata tohumu ve servis giriş rolleri de bu işte | `tools/migrate.py` |
| Veritabanı bağlantısı | Servis başına giriş rolü (`evren_api_svc`, `evren_worker_svc` …), NOINHERIT, yalnızca kendi grup rolü | ADR-0016 madde 4 |
| Sırlar | `EVREN_DATABASE_URL_FILE`, `EVREN_SESSION_KEY_FILE` (+ Entra'da istemci sırrı dosyası); servis başına parola dosyası `<dizin>/<giriş rolü>` | kurum sır kasası → dosya |
| Sağlık | API: `/readyz` (veritabanı erişimi), `/healthz`. Çalışan: nabız dosyası (`EVREN_WORKER_HEALTH_FILE`) + `ingest.worker_heartbeat` | `deploy/evren.py healthcheck` |
| Kafka | KRaft; otomatik konu açma kapalı; konular, bölüm sayısı ve saklama süresi `deploy/kafka/topics.txt`'te. İstemci: `EVREN_KAFKA_BOOTSTRAP`, `EVREN_KAFKA_SECURITY_PROTOCOL` (varsayılan `SASL_SSL`), `EVREN_KAFKA_USERNAME` + `EVREN_KAFKA_PASSWORD_FILE`, `EVREN_KAFKA_CA_FILE`; `PLAINTEXT` yalnızca `EVREN_KAFKA_ALLOW_PLAINTEXT=1` ile | ADR-0017 |
| OpenSearch | Tek düğüm (geliştirme) | U3'te kullanılmaya başlar |

Geliştirme yığını (yalnızca 127.0.0.1):

```bash
python tools/dev_stack.py up --demo    # sırlar → imaj → göç → demo verisi → Kafka konuları → servisler
python tools/dev_stack.py smoke        # uçtan uca duman testi (CI "Yığın" işiyle aynı)
python tools/dev_stack.py status       # servis durumları; logs [servis] ile günlükler
python tools/dev_stack.py down         # durdur; --volumes veriyi de siler
```

| Adres | Servis |
| --- | --- |
| http://127.0.0.1:8088 | Evren arayüzü ve API (dev oturumu) |
| 127.0.0.1:55432 | PostgreSQL (`evren`; parola `deploy/secrets/pg_owner_password`) |
| http://127.0.0.1:8089/ingest/v1 | Alım geçidi (bağlayıcılar; belirteç: `docker compose -f deploy/compose.dev.yaml run --rm migrate connector issue …`) |
| 127.0.0.1:59092 | Kafka (dış dinleyici) |
| http://127.0.0.1:59200 | OpenSearch |

- **TLS denetimli kurumsal vekil sunucu arkasında derleme:** `EVREN_BUILD_CA_FILE=/yol/kurum-ca.pem python tools/dev_stack.py up`. CA yalnızca bağımlılık kurulum adımına derleme sırrı olarak bağlanır; imaja yazılmaz.
- **Debian güvenlik güncellemeleri** derlemede zorunludur; depoya erişilemezse derleme durur. Debian deposuna erişimi olmayan geliştirme makinesinde: `EVREN_BUILD_OS_UPDATES=0` (yalnızca geliştirme; CI'daki Trivy imaj kapısı bu imajı kabul etmeyebilir).
- **Görüntü güncelleme:** `python tools/dev_stack.py digests --pull` sabit özetle bugünkü özeti karşılaştırır. Yeni özet tek değişiklik olarak yazılır ve incelenir.
- **Psql ile kurulmuş mevcut veritabanı:** `python tools/migrate.py --baseline 0011_change_sets` (veritabanına son uygulanan göç) o sürüme kadarki göçleri bir kez deftere alır; sonrasında `python tools/migrate.py` yalnızca yeni göçleri uygular.
- **Geliştirme kolaylıkları üretimde yoktur:** dev oturumu (`EVREN_DEV_CONTAINER`), Kafka düz metin, OpenSearch güvenlik eklentisi kapalı. Üretimde Entra, Kafka SASL/TLS + konu ACL'leri, OpenSearch güvenlik eklentisi + TLS zorunludur.

## Yük testi (F0 sonu)

Sentetik 5 milyon kaynak kaydı ve 3 milyon CI ile ölçülecekler:

- İlk yükleme süresi.
- Günlük artımlı işleme süresi.
- Arama gecikmesi.
- Etki analizi gecikmesi (derinlik 4).

Sonuçlar `DURUM.md`'ye kaydedilir.
