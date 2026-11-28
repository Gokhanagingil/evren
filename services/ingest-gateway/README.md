# services/ingest-gateway — Alım geçidi (U2, ADR-0017)

Bağlayıcıların tek giriş noktasıdır. Çalışır durumda; sözleşme `docs/08-api-ve-entegrasyon.md` → "Alım geçidi".

```bash
# Yerel (loopback, Kafka gerekir)
EVREN_DATABASE_URL_FILE=… EVREN_KAFKA_BOOTSTRAP=127.0.0.1:59092 EVREN_KAFKA_SECURITY_PROTOCOL=PLAINTEXT \
  EVREN_KAFKA_ALLOW_PLAINTEXT=1 python deploy/evren.py gateway
# Konteyner yığınında: python tools/dev_stack.py up  →  http://127.0.0.1:8089/ingest/v1
python tools/connector_credential.py issue --name zabbix-dc1 --data-source Zabbix --by <kişi>
```

## Doğrulama zinciri

1. TLS; istemci CA'sı verilirse istemci sertifikası da zorunlu (mTLS). Ağa TLS olmadan açılmaz.
2. Bağlayıcı belirteci (`Authorization: Bearer evr_…`). Yalnızca SHA-256 özeti saklanır; süre, iptal ve son kullanım tutulur.
3. Kaynak yetkisi: belirteç yalnızca kendi `data_source` değeri adına yazar. Emekli kaynak yazamaz. Ret denetim izine yazılır.
4. JSON şema doğrulaması (`IngestEnvelope`, `RunEnvelope`). Bilinmeyen alan reddedilir; hata gövdesi girdiyi yansıtmaz.
5. Çalışma durumu, gelecek tarihli gözlem, boyut sınırları (`Content-Length` zorunlu) ve kimlik başına hız sınırı.

## Doğrulamadan sonra

- Kayıtlar `evren.ingest.record.v1` konusuna `<kaynak>:<source_key>` anahtarıyla yazılır. 202 yanıtı yalnızca Kafka tüm kayıtları onayladıktan sonra döner.
- Çalışmayı geçit açar ve kapanışını ister (`ingest.source_run`: `opened_by`, `accepted_count`, `reported_*`, `close_requested_at`). Kapatan uzlaştırıcı çalışanıdır.
- Çalışma olayları `evren.ingest.run.v1` konusuna yazılır (bilgilendirme amaçlı).

## IRE uyumlu alım (ADR-0045)

ServiceNow'a IRE ile yazan araçlar aynı yükü buraya gönderir: `POST /api/now/identifyreconcile?sysparm_data_source=<kaynak>`. Kimlik bağlayıcı belirtecidir.

- Yük tek bir delta çalışması olur: aç → kayıtları Kafka'ya → kapanış iste.
- Yanıt IRE biçimindedir; öğeler `QUEUED`, `sysId` `Unknown`.
- `/api/now/identifyreconcile/query` yalnızca doğrular.
- Bu yoldaki hatalar ServiceNow biçimindedir.
- Dönüşüm: `evren_core.ire_inbound`.

## Neyi yapmaz

Eşleştirme, uzlaştırma veya CMDB yazımı yapmaz. Kaynak kaydını okuyamaz, yazamaz (göç 0012). Çalışmayı kapatamaz.

## Hata sözleşmesi

`400` şema veya kayıt reddi (alan konumu) · `401` kimlik · `403` kaynak yetkisi veya emekli kaynak · `404` çalışma yok veya başka kaynağın · `409` kapalı çalışma veya sayı uyumsuzluğu · `411`/`413` gövde · `429` hız (`Retry-After`) · `503` Kafka (yeniden dene).

## Ortam değişkenleri

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `EVREN_DATABASE_URL_FILE` | — | Bağlantı (giriş rolü `evren_gateway_svc` → `evren_app_ingest`) |
| `EVREN_GATEWAY_HOST` / `_PORT` | `127.0.0.1` / `8089` | Loopback dışı için TLS veya `EVREN_GATEWAY_INSECURE_HTTP=1` (yalnızca geliştirme) |
| `EVREN_GATEWAY_TLS_CERT_FILE`, `_KEY_FILE`, `_CLIENT_CA_FILE` | — | TLS ve mTLS |
| `EVREN_GATEWAY_MAX_BODY_BYTES`, `_MAX_BATCH`, `_PUBLISH_TIMEOUT` | 8 MiB, 500, 30 sn | Sınırlar |
| `EVREN_KAFKA_*` | — | `evren_core.messaging.KafkaSettings` (varsayılan SASL_SSL) |

## Testler

`EVREN_TEST_PG=1 python -m pytest services/ingest-gateway/tests`: 20 test, gerçek PostgreSQL ve taklit Kafka. Gerçek aracıyla uçtan uca akış `python tools/dev_stack.py smoke` ile sınanır.
