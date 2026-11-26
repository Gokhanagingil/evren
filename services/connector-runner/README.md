# services/connector-runner — Bağlayıcı çalıştırıcı servisi (U5.3b, ADR-0021)

Arayüzden ve API'den gelen **"şimdi çalıştır"** isteklerini ve `data_sources.yaml` zamanlamasını yürütür. API bağlayıcıyı çalıştırmaz ve kaynak sırlarını görmez. API yalnızca `ingest.connector_run_request`'e istek yazar; bu servis isteği alır ve bağlayıcıyı (`tools/connector_run.py`, ADR-0020) **ayrı alt süreç** olarak çalıştırır.

```bash
EVREN_CONNECTORS_FILE=/etc/evren/connectors.yaml EVREN_GATEWAY_URL=https://gecit:8089 \
EVREN_DATABASE_URL_FILE=/run/secrets/connector_dsn python -m evren_connector_runner
python -m evren_connector_runner --check   # yapılandırmayı doğrula (veritabanına bağlanmaz)
python -m evren_connector_runner --once    # bir tur: zamanlama + bekleyen istekler, bitmelerini bekle, çık
# Konteynerde: evren connector-runner [--check | --once]
```

- **Örnek yapılandırması** (`EVREN_CONNECTORS_FILE`, YAML): her örnek bir kaynak istemcisinin bir kurulumdur (ör. `zabbix-dc1`). Kaynak adresi, kullanıcı adı ve sır **dosya yolları** yalnızca burada durur. `password`, `token`, `secret` gibi anahtarlar reddedilir; sır değeri dosyaya yazılmaz. Örnek: `deploy/connectors.dev.yaml` (geliştirme; simülatörlü).
- **Bildirim:** Servis örneklerinin sırsız özetini `ingest.connector_instance`'a yazar: ad, istemci, kaynak, zamanlama, zaman dilimi, sonraki çalışma ve çalıştırıcı kimliği. Arayüz "Şimdi çalıştır"ı yalnızca canlı çalıştırıcının bildirdiği örnek için gösterir.
- **Yürütme:**
  - Her çalışma ayrı süreçtir: süre sınırı (`max_runtime_minutes`) ve iptal (SIGTERM, 10 sn sonra SIGKILL) gerçek olur.
  - Alt sürecin ortamı en az hâliyle kurulur: geçiş listesi (PATH, vekil, CA) ve örneğin `EVREN_SOURCE_*`/`EVREN_GATEWAY_*` yolları. Veritabanı adresi aktarılmaz.
  - `run_id` servis tarafından verilir. Geçitteki çalışma ile istek aynı kimliği taşır.
  - İstekte yalnızca sayılar saklanır. Sonuç notu yer tutucuyla temizlenir.
- **Zamanlama:** 5 alanlı cron (`evren_core/schedule.py`), `EVREN_SCHEDULE_TZ` (varsayılan `Europe/Istanbul`).
  - Kaçırılan zamanlama telafi edilmez: 60 dakikaya kadar geriye bakılır, yalnızca son dakika açılır.
  - Zamanlanmış dakika birden çok çalıştırıcıda bir kez açılır. Etkin istek varken yeni istek açılmaz.
  - `schedule_mode: delta` ile `since`, son başarılı çalışmanın başlangıcından 10 dakika öncesidir.
- **Kurtarma:**
  - Nabzı 3 tarama aralığı görülmeyen çalıştırıcının yürüyen isteği `failed` olur.
  - 24 saati geçen bekleyen istek `rejected` olur.
  - Emekli kaynağın ve devre dışı örneğin isteği reddedilir.
- **Yetki (göç 0019):** `evren_app_connector` rolü CMDB'yi okuyamaz ve yazamaz. Kaynak kaydına ve kimlik tablosuna erişemez. İsteğin yalnızca durum ve sonuç sütunlarını günceller. Giriş rolü `evren_connector_svc`'dir.
- **Ortam:** `EVREN_DATABASE_URL_FILE`, `EVREN_CONNECTORS_FILE`, `EVREN_GATEWAY_URL` (https; düz http yalnızca `EVREN_GATEWAY_ALLOW_HTTP=1`), `EVREN_GATEWAY_CA_FILE`, `EVREN_CONNECTOR_MAX_PARALLEL` (2), `EVREN_SCHEDULE_TZ`, `EVREN_RUNNER_NAME`, `EVREN_WORKER_POLL_SECONDS` (15), `EVREN_WORKER_HEALTH_FILE`, `EVREN_CONNECTOR_ALLOW_SIMULATE` (yalnızca geliştirme).
- **Testler:**
  - `services/connector-runner/tests`: yapılandırma doğrulaması ve gerçek PostgreSQL ile gerçek alt süreç. Uçtan uca istek → `connector_run.py --simulate` → sahte geçit akışı, süre aşımı, iptal, zamanlama ve kurtarma sınanır.
  - Gerçek geçit, Kafka ve uzlaştırıcıyla: `tools/dev_stack.py smoke`.
- **Bilinen sınır:** Çalıştırıcı çökerse yürüyen alt süreç yetim kalabilir ve çalışmasını bitirebilir. İstek yine `failed` sayılır; geçitteki çalışmayı uzlaştırıcı kapatır.
