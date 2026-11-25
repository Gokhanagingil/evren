# ADR-0016: Tek konteyner imajı, izlenen göçler ve compose geliştirme yığını

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

U1 (uçtan uca iş akışı, `roadmap.json` → `track`) altyapı yığınının kurulmasını ister: PostgreSQL, Kafka ve OpenSearch ile Evren servislerinin birlikte çalıştığı, tek komutla açılan ve CI'da gerçekten ayağa kalktığı sınanan bir ortam. O ana kadar servisler yalnızca geliştiricinin makinesinde ayrı ayrı, göçler ise `psql` döngüsüyle uygulanıyordu: hangi göçün hangi ortamda uygulandığı izlenmiyor, servisler sahip rolüyle (`evren_owner`) bağlanabiliyordu. Hedef çalışma ortamı (kurum Kubernetes'i, VM'ler veya başka bir platform) henüz belli değil; proje sahibi bu bilgiyi en sonda paylaşacak.

## Karar

1. **Tek imaj, birden çok servis.** `deploy/Dockerfile` tek bir Evren imajı üretir. Giriş noktası `deploy/evren.py` servisi komutla seçer: `api`, `worker`, `migrate`, `demo`, `healthcheck`. U2'de alım geçidi, U3'te dizinleyici aynı imaja eklenir. Kod tek sürümle dağıtılır, servisler arasında sürüm kayması olmaz.
2. **İmaj sıkılaştırması.** Bağımlılıklar `requirements/runtime.txt`'ten `pip --require-hashes --no-deps` ile kurulur. Temel görüntü özetiyle (digest) sabittir. Debian güvenlik güncellemeleri derleme anında uygulanır; depoya erişilemezse derleme durur (`apt-get update --error-on=any`), çünkü apt aksi hâlde sessizce eski listeyle devam eder. `OS_UPDATES=0` yalnızca ağsız geliştirme derlemesi içindir. Kullanıcı root değildir (uid 10001). Kaynak dosyalar root sahiplidir, uygulama bunları değiştiremez. Compose'ta kök dosya sistemi salt okunurdur, yalnızca `/tmp` tmpfs'tir; `cap_drop: ALL` ve `no-new-privileges` uygulanır. Testler, belgeler ve sırlar imaja girmez (`.dockerignore`).
3. **İzlenen göçler.** `tools/migrate.py`, `meta.schema_migration` defterine sürüm ve SHA-256 yazar. Her göç bir kez uygulanır. Uygulanmış bir göçün içeriği değişmişse kurulum durur (sürüklenme): göç dosyaları değiştirilmez, yeni göç yazılır. Veritabanında depoda olmayan bir sürüm varsa (eski imaj) kurulum yine durur. `psql` ile kurulmuş mevcut veritabanları `--baseline` ile bir kez deftere alınır. Metadata tohumu her çalıştırmada yeniden uygulanır (idempotent).
4. **Servis giriş rolleri.** Her servis kendi LOGIN rolüyle bağlanır (`evren_api_svc` → `evren_app_api`, `evren_worker_svc` → `evren_app_reconciler` …). Roller `NOINHERIT`'tir, yalnızca kendi grup rolünü `SET ROLE` ile üstlenir ve 50 bağlantıyla sınırlıdır. Parolalar dosyadan okunur ve en az 24 karakterdir. Sahip rolü yalnızca göç işinde kullanılır.
5. **Sırlar yalnızca dosyadan.** Servisler `*_FILE` ortam değişkenleriyle sır dosyası yolunu alır (`EVREN_DATABASE_URL_FILE`, `EVREN_SESSION_KEY_FILE`). Geliştirmede bunlar compose `secrets` ile `/run/secrets/` altına bağlanır; dosyaları `tools/dev_secrets.py` rastgele üretir (`deploy/secrets/`, git'e girmez). Üretimde aynı yollar kurum sır kasasından doldurulur. Sır ortam değişkenine, komut satırına, imaja veya günlüğe yazılmaz.
6. **Geliştirme yığını.** `deploy/compose.dev.yaml` şu servisleri içerir: PostgreSQL 16, Kafka 4 (KRaft, otomatik konu açma kapalı; konular `deploy/kafka/topics.txt`), OpenSearch 2 (tek düğüm), `migrate`, `api`, `worker` ve isteğe bağlı `demo`. Dış görüntüler özetle sabittir. Portlar yalnızca 127.0.0.1'e yayınlanır. `tools/dev_stack.py` sırayı yönetir: sırlar → imaj → göç → (demo) → Kafka konuları → servisler. Tek seferlik işler açıkça ve sırayla çalışır; biri başarısız olursa yığın yarım açılmaz.
7. **Konteyner içi dev oturumu.** API'nin dev kimlik modu normalde yalnızca loopback'e bağlanır. Konteynerde 0.0.0.0 gerekir. Bu yalnızca açık `EVREN_DEV_CONTAINER=1` bayrağıyla ve dev modunda kabul edilir. Bu modda yalnızca özel ağlardan ve loopback'ten gelen istekler kabul edilir, diğerlerine 403 döner. Entra modunda bayrak yok sayılır.
8. **Kurumsal TLS vekil sunucusu.** İmaj derlemesi isteğe bağlı `build_ca` derleme sırrını kabul eder (`EVREN_BUILD_CA_FILE`). Sır yalnızca bağımlılık kurulum adımına bağlanır, imaj katmanına ve geçmişine yazılmaz.
9. **CI'da gerçek yığın.** "Yığın" işi geliştiricinin çalıştırdığı komutların aynısını çalıştırır: imajı derler, demo verisiyle yığını açar ve göç aracını ikinci kez çalıştırır (bekleyen göç kalmamalı). Uçtan uca duman testi API'den elle düzenleme gönderir, konteynerdeki uzlaştırıcının bunu golden record'a uyguladığını doğrular, ardından Kafka konularını ve OpenSearch sağlığını denetler. Sıkılaştırma da denetlenir: uid 10001, salt okunur kök, `cap_drop ALL`, portların yalnızca 127.0.0.1'e yayınlanması. İmaj Trivy ile taranır: düzeltmesi yayınlanmış YÜKSEK/KRİTİK zafiyet veya sır varsa iş durur. İmaj SBOM'u (CycloneDX) üretilir.

## Değerlendirilen seçenekler

- **Servis başına ayrı imaj:** Her imaj daha küçük olurdu. Ancak sürüm eşgüdümü ve tarama yükü artar. Evren'in servisleri aynı çekirdek kütüphaneyi paylaşıyor; tek imaj (≈280 MB) yeterince küçük.
- **Göç aracı olarak Alembic veya Flyway:** Yeni bağımlılık (Flyway için JVM) gerektirir. Göçler zaten düz SQL ve sıralı. Gerekli olan sürüm ve özet defteri ile sürüklenme denetimi; bu 150 satırlık bir araçla sağlanıyor.
- **Sırları ortam değişkeniyle vermek:** Yaygın ama `docker inspect`, süreç ortamı ve çökme dökümlerinde görünür. Kurum standardı dosya veya sır kasası istiyor.
- **Kubernetes manifestleri veya Helm:** Hedef ortam belli değil (U10). Compose geliştirme ve CI için yeterli. Üretim dağıtım biçimi hedef ortam kararıyla birlikte yazılacak; imaj, giriş noktası, sağlık denetimleri ve sır dosyası sözleşmesi o biçimden bağımsız.

## Sonuçlar

- `docker compose` olan her makinede `python tools/dev_stack.py up --demo` ile tüm yığın açılır. Arayüz http://127.0.0.1:8088 adresinde API modunda çalışır.
- Kafka ve OpenSearch geliştirmede şifresiz ve düz metindir (yalnızca 127.0.0.1). Üretimde Kafka SASL/TLS ve konu ACL'leri, OpenSearch güvenlik eklentisi ve TLS zorunludur (docs/12, docs/06).
- Kafka ve OpenSearch bu adımda yalnızca ayağa kalkar. Evren'in onları kullanması U2 (alım geçidi, Kafka tüketicisi) ve U3'te (dizinleyici, arama) gelir.
- İlk taramada temel görüntüde düzeltmesi yayınlanmış bir YÜKSEK bulgu çıktı (libzstd1). Derleme anı güvenlik güncellemesi bu yüzden zorunlu tutuldu.
- İmaj imzalama (cosign) ve imaj kayıt defteri hedef ortamla birlikte gelir (U10).
- Görüntü güncellemesi bilinçli bir değişikliktir: `python tools/dev_stack.py digests --pull` yeni özeti gösterir; özet tek değişiklik olarak güncellenir ve incelenir.
