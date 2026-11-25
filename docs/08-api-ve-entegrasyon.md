# 08 · API ve entegrasyon

## Üç arayüz, tek kural seti

| Arayüz | Kim kullanır | Nerede |
| --- | --- | --- |
| Yerel REST `/api/v1` | Evren arayüzü, GRC Platformu (tedarik), otomasyonlar | `services/api` |
| ServiceNow uyumlu `/api/now/table/{table}`, `/api/now/identifyreconcile` | SN Table API'ye yazılmış eski betikler, SN IRE biçiminde veri üreten araçlar | `services/api` (F2-05) |
| Alım geçidi `/ingest/v1` | Bağlayıcılar (vCenter, Zabbix, keşif …) | `services/ingest-gateway` (ADR-0017) |
| Olay akışı (CloudEvents + imzalı webhook) | Değişikliğe tepki veren uygulamalar | Kafka `evren.cmdb.ci.changed.v1`, webhook (F2-06) |
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
- **Oturum:** tarayıcı için Entra ID OIDC + PKCE ve şifreli `__Host-` çerezi; durum değiştiren isteklerde `X-Evren-Request: 1` başlığı (ADR-0010). Makine hesapları (GRC Platformu, otomasyon) için kapsamlı istemci kimliği F1'de eklenecek.
- **Uygulanan uç noktalar** ve durumları: `services/api/README.md`. Makine tarafından okunur sözleşme: `GET /api/v1/openapi.json`.

### Eşleştirme kararı

`POST /api/v1/match-queue/{id}/decision`:

```json
{"decision": "merge", "ci_sys_id": "<32 hex>", "note": "Seri doğrulandı", "idempotency_key": "ui-…"}
```

- `decision`: `merge` (aday zorunlu), `new_ci`, `defer`, `fix_at_source`.
- Yanıt: `{"decision", "label", "ci_sys_id", "applied": false, "replayed", "message"}`. Karar `match_candidate`'e ve `evren.match.decided.v1` olayı olarak outbox'a yazılır; kimlik bağını ve CI'ı uzlaştırıcı kurar (`applied` sonradan `true` olur).
- `409`: kayıt zaten karar almış, aday kuyrukta değil veya aynı anahtar farklı içerikle kullanılmış. `403`: Eşleştirme analisti rolü yok.

### Elle düzenleme

`PATCH /api/v1/ci/{sys_id}` (ADR-0012), yetki `cmdb_ci/write`:

```json
{"attributes": {"used_for": "Production", "support_group": "<sys_user_group sys_id>", "os": null},
 "note": "Sahiplik devri", "idempotency_key": "ui-…"}
```

- Alanlar sözlükten çözülür; `GET /api/v1/ci/{sys_id}` yanıtındaki `editable[]` düzenlenebilir alanları, tiplerini, seçimleri, referans tablosunu, `governed` (Manual en öncelikli mi) ve `authority` (rol öncelik sırası) bilgisini verir. `null` elle değeri kaldırır.
- Yanıt `202` + istek kimliği; olay `evren.ci.edit.requested.v1`. Uygulanınca `edits[].result` alan başına `applied` / `overridden` (+ kazanan kaynak) / `cleared`.
- `422 gecersiz_alan` (`details`: alan + Türkçe neden; geçersiz alan adı yansıtılmaz), `403 yetki_yok`, `403 alan_yetkisi_yok`.
- Referans seçenekleri: `GET /api/v1/ref/{tablo}?q=&limit=` (yalnızca `sys_user`, `sys_user_group`, `cmn_location`, `core_company`, `cmn_department`).

### Gölge mod raporu ve kanıt

- `GET /api/v1/reports/shadow` (ADR-0014): `status` (`conclusive`/`inconclusive`), `provenance` (`collection_status`, `depends_on`, `unhealthy_sources`, roller), `coverage` (toplamlar, sınıf × rol tablosu, keşfin görmediği ve CMDB'de olmayan CI listeleri), `duplicates` (gruplar: `verdict`, kanıt anahtarları, `advice`, CI'lar ve keşfin görüp görmediği), `drift` (öznitelik bazında gerçek/yazım farkı, örnekler), `shadow_sources`, `can_snapshot`.
- `POST /api/v1/reports/shadow/snapshots` (`note`, `idempotency_key`; yetki `shadow_report/create`) → `201` `{id, sha256, status, collection_status}`.
- `GET /api/v1/reports/shadow/snapshots` → liste; `GET /api/v1/reports/shadow/snapshots/{id}` → kayıttaki kararlı JSON (ek dosya), `X-Evren-Content-SHA256` başlığı. GRC Platformu indirilen baytların SHA-256'sını bu başlıkla ve listedeki özetle doğrular.

### Metadata değişiklik setleri

ADR-0015. `GET /api/v1/schema/catalog` (sınıflar ve kendi alanları, referans tabloları, roller, kaynaklar ve izinli geçişleri, yetenekler), `GET /api/v1/schema/change-sets`, `GET /api/v1/schema/change-sets/{id}`.

- `POST /api/v1/schema/change-sets` (`schema/schema_draft`; `name`, `description`, `changes[]` = `{op, table, element, payload}`, `idempotency_key`) → `201`; geçersizse `422 gecersiz_degisiklik` ve Türkçe nedenler.
- `POST …/{id}/submit` (yalnız hazırlayan), `…/approve` ve `…/reject` (`schema/schema_approve`, `{note}`), `…/publish` (`schema/schema_approve`). Hazırlayan onaylayamaz/reddedemez/yayınlayamaz: `403 hazirlayan_onaylayamaz`. Yanlış durum: `409 durum_uygun_degil`.
- `GET …/{id}/artifact` → `metadata/changes/` dosyası (YAML), `X-Evren-Content-SHA256`.

### Birleştirmeyi ayırma

`POST /api/v1/ci/{sys_id}/split` (ADR-0011):

```json
{"link_id": "<kimlik bağı sys_id>", "target_ci": null, "note": "Seri başka sunucuya ait", "idempotency_key": "ui-…"}
```

- `link_id`: `GET /api/v1/ci/{sys_id}` yanıtındaki `links[].id`. Her bağ `split: {allowed, pending, reason}` taşır; yanıtta `can_split` ve `splits` (istek geçmişi) bulunur.
- `target_ci` boşsa kayıt için yeni CI açılır; doluysa kayıt o CI'a taşınır (hedef sınıfta `merge` yetkisi ve sınıf uyumu gerekir).
- Yanıt `202`: `{"id", "status": "pending", …}`; olay `evren.ci.split.requested.v1`. Uygulandığında `evren.ci.split.v1`.
- `409 tek_kaynak` (elle giriş kaydı sayılmaz), `409 bekleyen_ayirma`, `422 elle_giris_bagi`, `422 ilk_yukleme_bagi`, `422 kaynak_ci_acamaz`, `422 bag_yok`, `422 hedef_yok`, `422 sinif_uyumsuz`, `403 yetki_yok`.

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

## Küresel arama (ADR-0018)

`GET /api/v1/search?q=<metin>&limit=<1-50>` → `{"engine": "opensearch" | "postgresql", "degraded": bool, "items": [{sys_id, name, class, class_label, ip_address, fqdn, os, sources}]}`

- **Eşleşme türleri:**
  - Ad, FQDN, IP, seri, MAC, varlık etiketi ve işletim sisteminde parça önekiyle (`ist-d`, `ist db`) eşleşir.
  - Ayırıcısız yazım bulunur (`istdb01`, `redhat`).
  - Yazım hatası toleransı vardır (`windws`, `ank-epr-01`).
  - Türkçe büyük/küçük harf duyarsızdır (`İST-DB-01`).
  - IP biçimindeki sorgu IP önekiyle aranır (`10.20.30` → `10.20.30.*`). Tam sys_id en üste gelir.
- **Yetki:** Yalnızca okuma izni olan sınıflar döner. Kullanıcının göremediği alanla aranamaz ve bu alan yanıtta `null` gelir. Yetki her zaman API'de uygulanır, arama motoruna bırakılmaz.
- **Yedek:** OpenSearch tanımlı değilse veya ulaşılamıyorsa PostgreSQL alt dize araması kullanılır; Türkçe katlama aynıdır. Yedeğe düşüldüğünde `degraded: true` döner ve arayüz bunu gösterir.

## Alım geçidi (bağlayıcı sözleşmesi, ADR-0017)

Bağlayıcı `Authorization: Bearer evr_…` ile gelir. Belirteci `tools/connector_credential.py issue` verir; arayüzden yönetim U4'te gelecek. Belirteç tek bir veri kaynağına bağlıdır.

| Adım | İstek | Yanıt |
| --- | --- | --- |
| 1. Çalışmayı aç | `POST /ingest/v1/runs` gövde: `evren.ingest.run.started.v1` (`run_id`, `mode`: `snapshot` \| `delta`) | 201; aynı `run_id` tekrar: 200 `replayed` |
| 2. Kayıtları gönder | `POST /ingest/v1/runs/{run_id}/records` gövde: `{"records": [evren.ingest.record.v1 …]}` (≤ 500) | 202 `accepted` — Kafka onayladıktan sonra |
| 3. Kapanış iste | `POST /ingest/v1/runs/{run_id}/complete` gövde: `evren.ingest.run.completed.v1` (`record_count`, `completed`, `error_count`) | 202 `closing`; aynı değerlerle tekrar: 200 |
| 4. Durumu izle | `GET /ingest/v1/runs/{run_id}` | `open` · `closing` · `finished` (+ `completed`, `breaker_state`) |

- Toplu gönderim **ya hep ya hiç** işlenir. Bir kayıt reddedilirse hiçbiri alınmaz; `details` reddedilen kaydın sırasını, alanını ve nedenini verir: `kaynak_uyumsuz`, `calisma_uyumsuz`, `gelecek_tarih`, `kayit_buyuk`.
- `record_count` = çalışmada gönderilen **farklı** nesne sayısı. Aynı `source_key` bir çalışmada bir kez gönderilmelidir. `record_count` Kafka'ya teslim edilen sayıdan büyükse 409 döner.
- Çalışma, uzlaştırıcı bildirilen kayıtları işleyince kapanır. 15 dakikada tamamlanmazsa eksik kapanır ve devre kesici yıkıcı işlemleri durdurur. Kapanış hiç istenmezse çalışma 24 saat sonra terk edilmiş sayılır.
- Delta çalışmasında görülmeyen kayıt eksik sayılmaz. Hacim ve değişim oranı yalnızca tam (snapshot) çalışmada denetlenir.
- Hata kodları:

  | Kod | Anlam |
  | --- | --- |
  | 400 | Şema hatası (alan konumu döner, girdi yansıtılmaz) veya kayıt reddi |
  | 401 | Belirteç yok, geçersiz, süresi dolmuş veya iptal edilmiş |
  | 403 | Belirteç başka kaynağa bağlı veya kaynak emekli |
  | 404 | Çalışma yok veya başka kaynağa ait |
  | 409 | Çalışma kapalı veya sayı uyumsuz |
  | 411/413 | Gövde uzunluğu bildirilmemiş veya sınırı aşıyor |
  | 429 | Hız sınırı (`Retry-After`) |
  | 503 | Kafka'ya ulaşılamıyor; aynı toplu gönderimi yeniden deneyin, tekrar zararsızdır |

## Olaylar

Kafka `evren.cmdb.ci.changed.v1` (anahtar: `sys_id`, CloudEvents; outbox'tan yayınlanır, ADR-0017):

- `id` = `outbox-<n>`: tekrarlar bununla ayıklanır (en az bir kez teslim).
- `type`: `evren.cmdb.ci.created.v1`, `evren.cmdb.ci.changed.v1`. `subject` = sys_id.
- `data`: sınıf, değişen öznitelikler (eski → yeni), kaynak, çalışma. Hassasiyeti `confidential`/`restricted` olan alanın değeri konmaz (`redacted: true`).
- İnsan istekleri (elle düzenleme, ayırma …) `evren.cmdb.activity.v1` konusundadır.
- Webhook (F2-06): `X-Evren-Signature: sha256=<HMAC>` + zaman damgası (tekrar saldırısına karşı 5 dakika penceresi).

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
