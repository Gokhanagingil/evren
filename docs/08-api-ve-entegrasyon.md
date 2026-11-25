# 08 · API ve entegrasyon

## Üç arayüz, tek kural seti

| Arayüz | Kim kullanır | Nerede |
| --- | --- | --- |
| Yerel REST `/api/v1` | Evren arayüzü, GRC Platformu (tedarik), otomasyonlar | `services/api` |
| ServiceNow uyumlu `/api/now/table/{table}`, `/api/now/identifyreconcile` | SN Table API'ye yazılmış eski betikler, SN IRE biçiminde veri üreten araçlar | `services/api` (F2-05) |
| Olay akışı (CloudEvents + imzalı webhook) | Değişikliğe tepki veren uygulamalar | Kafka `cmdb.ci.changed`, webhook |
| MCP | Kurumun kendi MCP uygulaması ve yapay zekâ ajanları | `services/mcp`, docs/09 |

Hepsi aynı yetki modelinden (docs/07) ve aynı golden record'dan geçer.

## Sözleşme ilkeleri (GRC Platformu ile ortak)

- **Tekrar güvenliği:** yazma isteklerinde `idempotency_key`.
  - Aynı anahtar + aynı içerik → `200` ve `replayed: true`.
  - Aynı anahtar + farklı içerik → `409`.
- **Kaynak bilgisi (`provenance`):** `source_system`, `query_reference`, `collection_status` (`complete`/`partial`/`error`).
  - GRC Platformu'na giden CMDB tabanlı kontrol sonuçları bu alanı taşır.
  - `partial` veya `error` verisi `inconclusive` dışında bir sonuç üretemez.
- **Zaman:** saat dilimli ISO 8601, UTC saklama.
- **Hata gövdesi:** `{"error": {"code", "message" (Türkçe), "details"}}`.
- **Sayfalama:** imleç + `limit` (en fazla 1000).

## ServiceNow uyumlu Table API

Desteklenen parametreler:

- `sysparm_query` — encoded query:
  - Mantık: `^`, `^OR`, `^NQ`, `ORDERBY`, `ORDERBYDESC`.
  - Operatörler: `=`, `!=`, `LIKE`, `NOT LIKE`, `STARTSWITH`, `ENDSWITH`, `IN`, `NOT IN`, `ISEMPTY`, `ISNOTEMPTY`, `>`, `>=`, `<`, `<=`, `BETWEEN`, `ANYTHING`, `INSTANCEOF`.
- `sysparm_fields`
- `sysparm_limit`, `sysparm_offset`
- `sysparm_display_value` (F2)

Bilinçli farklar (güvenlik):

- `javascript:` değerleri reddedilir; sunucu tarafında betik çalışmaz.
- Dot-walk (`support_group.name`) ilk sürümde desteklenmez ve açık hata döner.
- Koşul sayısı 200, IN listesi 1000 ile sınırlıdır.

Örnek:

```
GET /api/now/table/cmdb_ci_server?sysparm_query=sys_class_nameINSTANCEOFcmdb_ci_server^operational_status=1^ram>=16384^ORDERBYname&sysparm_fields=sys_id,name,ip_address,ram&sysparm_limit=100
```

## Olaylar

`cmdb.ci.changed.v1` (CloudEvents):

- `subject` = sys_id.
- `data`: sınıf, değişen öznitelikler (eski → yeni), kaynak, gerekçe, korelasyon (çalışma, Change numarası).
- Webhook imzası: `X-Evren-Signature: sha256=<HMAC>` + zaman damgası (tekrar saldırısına karşı 5 dakika penceresi).

## GRC Platformu (tedarik) entegrasyonu (F2-08)

Örnek otomatik kontroller (kurum MCP uygulaması veya GRC Platformu zamanlayıcısı çalıştırır):

| Kontrol | Evren sorgusu | Sonuç |
| --- | --- | --- |
| CMDB'de kaydı olmayan sunucu | Defender/Zabbix'te olup golden record'a bağlanmamış kaynak kayıtları | fail + kanıt listesi |
| İzlenmeyen üretim sunucusu | `used_for=Production` ve Zabbix bağı yok | fail/pass |
| Korumasız uç nokta | Sunucu sınıfı ve Defender bağı yok | fail/pass |
| Yetkisiz değişiklik | `insight.anomaly kind=unauthorized_change` (dönem) | fail + Change referansı |
| Sahipsiz kritik CI | `busines_criticality` yüksek ve `owned_by` boş | fail/pass |

Kaynak çalışması devre kesicide `WARN` veya `OPEN` ise `collection_status=partial` gönderilir ve GRC Platformu sonucu `inconclusive` olur.
