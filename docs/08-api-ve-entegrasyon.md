# 08 · API ve entegrasyon

## Üç arayüz, tek kural seti

| Arayüz | Kim kullanır | Nerede |
| --- | --- | --- |
| Yerel REST `/api/v1` | Evren arayüzü, GRC Platformu (tedarik), otomasyonlar | `services/api` |
| ServiceNow uyumlu `/api/now/table/{table}` (okuma), `/api/now/identifyreconcile` (alım) | SN Table API'ye yazılmış eski betikler, SN IRE biçiminde veri üreten araçlar | Table API `services/api` (ADR-0044); IRE alımı `services/ingest-gateway` (ADR-0045) |
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
- **İstek kimliği:** her yanıtta `X-Request-Id` (32 onaltılık). Aynı istekte yazılan güvenlik olaylarına işlenir; destek ve denetimde olayları bağlar (ADR-0039).
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

### Elle CI oluşturma (ADR-0026)

- `GET /api/v1/meta/creatable-classes`: kişinin CI açabileceği sınıflar (`create` izni, kök hariç). `GET /api/v1/me` → `can.create_ci`.
- `GET /api/v1/meta/classes/{sınıf}/form`: elle yazılabilen alanlar (elle düzenlemeyle aynı politika), `required: ["name"]`, `identity` (tanımlama kuralındaki öznitelikler; keşfin CI'ı sonradan tanıması için).
- `POST /api/v1/ci` (CSRF başlığı, `idempotency_key`): `{"sys_class_name": "cmdb_ci_linux_server", "attributes": {"name": "…", "serial_number": "…"}, "note": "…", "force_new": false}` → `202 {"id", "status": "pending"}`. `force_new` gerekçe (`note`) ister. Hatalar: `422 ad_zorunlu`, `422 gecersiz_alan`, `422 kok_sinif`, `422 sinif_yok`, `403 yetki_yok`.
- `GET /api/v1/ci-requests/{id}`: `status` = `pending | created | duplicate | needs_decision | rejected`, `result_ci` (açılan veya var olan CI), `candidates` (belirsiz eşleşmede; ad, sınıf, olasılık), `result_note`.
- Uzlaştırıcı isteği kimlik çözümlemesinden geçirir: kesin eşleşmede CI açılmaz (zorlanamaz), belirsiz eşleşmede veya kimlik anahtarı olmadan aynı ailede aynı adda karar istenir; açılan CI'ın kimlik anahtarları dizine girer.

### Elle ilişki (ADR-0026 §5)

- `GET /api/v1/ci/{sys_id}/relation-options`: bu CI için izinli ilişkiler (ilişki kuralları, sınıf kalıtımıyla; `direction`, `type`, Türkçe fiil, karşı sınıf) ve `can_add`/`can_remove`.
- `POST /api/v1/ci/{sys_id}/relations` (CSRF, `idempotency_key`): `{"action": "add" | "remove", "type": "Runs on::Runs", "direction": "parent" | "child", "target": "<sys_id>", "note": "…"}` → `202`. Hatalar: `422 iliski_kurali` (ters yön veya kural yok; ileti hangisi olduğunu söyler), `422 ayni_ci`, `422 iliski_tipi_yok`, `409 iliski_var`, `409 elle_iliski_degil` (keşfin ilişkisi elle kaldırılamaz), `409 bekleyen_istek`, `403`.
- `GET /api/v1/relation-requests/{id}`: `pending | applied | rejected` ve uzlaştırıcının notu.
- CI ayrıntısında her ilişki `source` (yazan kaynak) ve `manual` (elle girilmiş mi; iki uçtaki Manual kayıtlardan) taşır; `pending_relations`, `can_relate`, `can_unrelate`.

### CSV ile toplu içe aktarma (ADR-0026 §6)

- `POST /api/v1/imports/preview` (CSRF, `create` izni): `{"sys_class_name": "cmdb_ci_linux_server", "csv": "…", "file_name": "sunucular.csv"}` → `200`. Hiçbir şey yazmaz.
  - Yanıt: `delimiter` (virgül, noktalı virgül, sekme), `columns` (başlık → alan; `ok | class | unknown | duplicate`), `total`, `valid_count`, `invalid_count`, `rows` (hatalılar önce, en çok 500: `row_no`, `class`, `class_label`, `name`, `valid`, `errors`).
  - Başlık alan adı veya Türkçe etiket olabilir; `sys_class_name` sütunu satırın sınıfıdır. Seçim değeri Türkçe etiketiyle, referans adla, evet/hayır Türkçe yazılabilir.
- `POST /api/v1/imports` (CSRF, `idempotency_key` zorunlu, isteğe bağlı `note`): dosyayı yeniden doğrular, geçerli satırları toplu iş altında oluşturma isteği yapar → `202 {"id", "row_count", "skipped_count"}`. Aynı anahtar aynı işi döndürür.
- `GET /api/v1/imports/{id}` (isteğe bağlı `status` süzgeci): `counts` (`pending | created | duplicate | needs_decision | rejected`), `done`, satır sonuçları (`row_no`, `name`, `status`, `result_ci`, `result_ci_name`, `result_note`).
- Hatalar: `413 dosya_cok_buyuk` (2 MB), `413 istek_cok_buyuk` (gövde 5 MB), `422 bos_dosya`, `422 csv_bozuk`, `422 cok_satir` (5000), `422 satir_yok`, `422 ad_sutunu_yok`, `422 gecerli_satir_yok`, `422 sinif_yok` (varsayılan sınıf), `422 idempotency_key_yok`, `403 yetki_yok`.
- Var olan CI güncellenmez: kimliği var olan CI'a denk gelen satır `duplicate` olur ve o CI'ı gösterir. Aynı dosyada iki satır aynı CI'sa ilki açılır, ikincisi `duplicate` olur (satırlar sırayla işlenir).

### Sahiplik onayı (ADR-0027)

- `GET /api/v1/attestation/summary`: kapsamdaki CI sayıları duruma göre (`not_reviewed`, `valid`, `due_soon`, `expired`, `rejected`), sahipsiz sayısı, onay oranı, kişinin kendi CI'ları (`mine`), politikalar (`where_label` Türkçe), `user.mapped`, `can_attest`.
- `GET /api/v1/attestation/items?view=mine|invalid|rejected|orphan&q=&limit=&offset=`: aciliyet sırasıyla CI'lar (durum, geçerlilik sonu, son onay, sorumlu, bekleyen istek). Okuma izni olmayan sınıfın CI'ı listelenmez.
- `POST /api/v1/attestations` (CSRF, `idempotency_key`, `attest` izni): `{"items": ["<sys_id>", …] (1–200), "decision": "attested" | "rejected", "reason": "not_owner" | "not_exists" | "data_wrong", "note": "…"}` → `202 {"accepted": [{"ci", "request"}], "skipped": [{"ci", "reason"}]}`. Ret neden ve gerekçe ister. Uygun olmayan CI atlanır (kapsam dışı, sorumlu değil, bekleyen istek, bulunamadı); hiçbiri uygun değilse `409 onaylanacak_ci_yok` (ayrıntıda nedenler). Oturum `ref.sys_user`'a eşlenemezse `403 kullanici_kaydi_yok`.
- `GET /api/v1/attestation-requests?ids=a,b,…`: isteklerin durumu (`pending | applied | rejected`) ve uzlaştırıcının notu.
- CI ayrıntısında `attestation`: durum, politika, son onay (kişi adı, tarih), geçerlilik sonu, sorumlular, son istekler, açık ret anomalisi, `can_attest`, `blocker` ve `blocker_code`.

### Etki analizi (ADR-0030)

- `GET /api/v1/ci/{sys_id}/impact?depth=6` (1–10): `upstream` (bu CI düşerse etkilenenler) ve `downstream` (bağımlılıkları). Her biri `nodes` (sys_id, ad, sınıf, düzey, ilişki tipi, `phrase` (bağımlı ucun Türkçe fiili), `via`/`via_name` (aracı CI), `service`, `production`, `retired`), `services`, `hidden` (okuma izni olmayan CI sayısı; adları sızmaz), `truncated` (500 düğüm sınırı). `summary`: etkilenen hizmet ve üretimdeki hizmet sayısı.
- Yön `metadata/relationship_types.yaml` → `impact` (`parent` | `child` | `none`); yürüyüş döngüsüzdür.

### Sistem durumu (ADR-0032)

- `GET /api/v1/system/status` yalnızca `platform` okuma izniyle çağrılabilir (Platform operatörü, Veri sorumlusu, Denetçi); izni olmayan 403 alır. `/api/v1/me` → `can.system`.
- Yanıt:
  - `overall` ve `overall_label`: `off` dışındaki en kötü durum;
  - `counts`;
  - `components`: her biri `id`, `label`, `state` (`ok` | `warn` | `bad` | `off`), `state_label`, `summary`, `items` (`label`/`value`) ve `note`.
- Bileşenler:
  - `api`, `database`, `migrations`, `kafka`, `opensearch`;
  - servisler: `svc_requests`, `svc_ingest`, `svc_indexer`, `svc_connector`, `svc_gateway`;
  - `outbox`, `queues`, `sources`.
- Kafka'nın durumu onu kullanan servislerin nabzından okunur; API Kafka'ya bağlanmaz.

### Yönetim paneli ve teknik belgeler (ADR-0033)

- `GET /api/v1/admin/attention`: kişinin karar vermesi gereken bekleyen işler; yalnızca izni olduğu işler sayılır. Her öğe `id`, `label`, `count`, `href` (arayüz yolu), `hint` ve `tone` (`bad` | `warn` | `info`) taşır; önem ve sayı sırasıyla döner. Öğeler:
  - `queue` (eşleştirme kuyruğu), `schema_review` (onay bekleyen, hazırlayan kendisi değil), `schema_publish`, `schema_draft` (kendi taslağı);
  - `staleness` (emekliye ayırma adayı), `unauthorized` (yetkisiz değişiklik), `credentials` (14 gün içinde süresi dolacak bağlayıcı kimliği), `breaker` (devre kesicisi açık kaynak);
  - `attestation` (kişinin onayını bekleyen CI), `system` (sistem durumu `warn` veya `bad`).
- `/api/v1/me` → `can`: arayüz görev kartlarını bununla açar veya kilitler (`create_ci`, `relate`, `review_match`, `retire`, `schema_draft`, `schema_approve`, `connector_read`, `connector_admin`, `anomaly_review`, `iam_admin`, `audit_read`, `system` …). Asıl yetki denetimi her uç noktadadır.
- `GET /api/v1/docs`: teknik belgeler (`docs`) ve ADR'ler (`adrs`), her biri `name` ve `title`.
- `GET /api/v1/docs/{ad}`: `{"name", "title", "markdown", "base"}`. Ad `06-guvenlik`, `adr/0033-…` ya da `adr/README` biçimindedir; geçersiz ad `400 gecersiz_belge`, olmayan belge `404 bulunamadi`. Oturum gerekir.
- Kullanım kılavuzu API'den gelmez: `apps/web/content/guide.json` statik içeriktir (`docs/kilavuz` → `tools/build_guide.py`).

### Yönetişim görevleri (ADR-0035)

- `GET /api/v1/tasks?view=mine|all|unassigned|attention&state=open|closed|all&q=`: `mine` herkese (kişiye ve grubuna atanan); diğerleri Konfigürasyon yöneticisi ve Denetçi. Öğe: `number`, `kind`, `title`, `ci`, `assignee` (kişi, grup, dayanak), `due_at`, `overdue`, `channel`, `delivery_status`, `external_number`, `external_closed`, `state`, `outcome`; `counts`.
- `GET /api/v1/tasks/{sys_id}`: ayrıntı, `events`, `can_complete`, `can_cancel`, `outcomes`. Görme yetkisi yoksa 404.
- `POST /api/v1/tasks` (`task_admin`, `idempotency_key`): `{title, description?, ci_sys_id?, assignee: {mode: auto|user|group, sys_id?}, due_days (1–365), channel?}` → 201 `{sys_id, number, basis, channel, message}`. `auto` CI ister (sahip → yöneten → destek grubu; yoksa `unassigned`).
- `POST /api/v1/tasks/{sys_id}/complete`: `{outcome: confirmed|corrected|not_applicable|rejected, note}` (sorumlu, grubunun üyesi veya Konfigürasyon yöneticisi; `confirmed` dışında açıklama ≥ 10 karakter). `POST /api/v1/tasks/{sys_id}/cancel`: `{reason}` (yalnızca Konfigürasyon yöneticisi).
- `GET /api/v1/task-channels`: kanallar (tür, yaşam döngüsü, varsayılan, ayar, açık ve teslim edilemeyen sayısı) ve görev çalışanının durumu. `PATCH /api/v1/task-channels/{name}`: `{is_default?, lifecycle?, settings?: {table, close_state, cancel_state, closed_states, default_group}, reason}`; ayar doğrulanır, denetim izine yazılır.
- ServiceNow tarafı (görev çalışanı, Table API): `POST /api/now/table/<tablo>` yalnızca standart alanlarla, `GET …?sysparm_query=correlation_id=<görev>` (tekillik), `GET …/<sys_id>` (eşitleme), `PATCH …/<sys_id>` (`state`, `close_notes`).

### Sihirbazlar (ADR-0038)

- `GET /api/v1/integrations` (`connector_admin`): bağlayıcı kataloğu (rol, kimlik türü ve alanları, seçenekler, adres örneği, varsayılan zamanlama, veri kaynağının yaşam döngüsü, mevcut örnekler) ve yarım kalan kurulumlar (kimliği verilmiş, çalıştırıcıda görünmeyen).
- `POST /api/v1/integrations/plan` (`connector_admin`): `{name, connector, url?, user?, token_file?, password_file?, tenant_id?, client_id?, client_secret_file?, scope?, ca_file?, gateway_token_file, schedule_choice: default|custom|manual, schedule?, schedule_mode, page_size?, api_release?, lookback_days?, max_runtime_minutes}` → `{yaml, secrets: [{key, path, what}], schedule, next_runs, warnings}`. Çalıştırıcının kurallarıyla doğrulanır (`yapilandirma_gecersiz` 422); sır değeri alanı yoktur; **saklanmaz**.
- `GET /api/v1/integrations/{name}` (`connector_admin`): `steps` (`data_source`, `credential`, `instance` — bildirildi mi, çalıştırıcı canlı mı —, `run`), `complete`, `started`.
- `POST /api/v1/schema/change-sets/validate` (`schema_draft`): `{changes}` → `{ok, errors, changes (açıklamalar), impact}`; kaydetmez.

### Denetim izi (ADR-0039)

Okuma `audit/audit_read`, doğrulama ve inceleme `audit/audit_review` ister (ikisi de Denetçi). Süzgeçler: `since`, `until` (en çok 366 gün; varsayılan son 7 gün), `source` (`security`/`ci`/`task`), `category`, `action`, `actor`, `outcome` (`allow`/`deny`), `object`, `request_id`, `ip`, `q`.

- `GET /api/v1/audit/catalog`: akışlar, kategoriler, bilinen olay türleri (Türkçe ad ve kategori).
- `GET /api/v1/audit/events?…&cursor=&limit=` → `{items, next, total (ilk sayfada; 10.000 üstü "10.000+"), since, until}`. Olay: `key`, `source`, `id`, `at`, `actor`, `action`, `action_label`, `category`, `object`, `object_link`, `outcome`, `client_ip`, `request_id`, `detail`, `masked`.
- `GET /api/v1/audit/events/{source}/{id}`: olay + `seal` (dönem, son doğrulama) + `related` (aynı istekteki olaylar).
- `GET /api/v1/audit/summary?since&until`: akış ve kategori başına sayı, ret sayısı, en çok ret alan ve en etkin kişiler.
- `GET /api/v1/audit/export?…&format=csv|ndjson`: en çok 50.000 kayıt (`X-Evren-Truncated`); CSV `;` ayraçlı, UTF-8 BOM'lu, formül korumalı. Aktarmanın kendisi `audit_export` olayıdır.
- `GET /api/v1/audit/integrity`: akış başına son mühür, gecikme, son kendiliğinden/elle doğrulama, son 30 günün sorunlu doğrulamaları. `GET /api/v1/audit/seals?stream&limit`: son mühürler ve son doğrulama sonucu.
- `POST /api/v1/audit/seals/verify` `{since, until, stream?}` (en çok 92 gün) → `{checked, failed, ok, message, problems}`; her mühür için `audit.seal_check` kanıtı ve `audit_verify` olayı.
- `GET /api/v1/audit/reviews` → `{items, due, every_days, suggested}`. `POST /api/v1/audit/reviews` `{period_start, period_end, outcome: no_findings|findings, note (10–4000), idempotency_key}` → `201`; mühürler önce doğrulanır, mühür sorunu varken `no_findings` → `422 muhur_sorunu`.

### Bildirimler, platform uyarıları ve metrikler (ADR-0040)

- `GET /api/v1/notifications?unread_only&before&limit` → `{items, next, unread, critical_unread}`: bana, grubuma ve rollerime (son 90 gün). `GET /api/v1/notifications/count`. `POST /api/v1/notifications/read` `{ids}`, `POST /api/v1/notifications/read-all` (yalnızca görünen bildirimler).
- `GET /api/v1/notifications/routes` (`platform` okuma): yönlendirmeler, kanal durumu (Teams'in yalnızca hedef adları), roller, gruplar, teslim sorunları. `PUT /api/v1/notifications/routes` `{audience_kind: role|group, audience, channel: email|teams, target, min_severity, active}` ve `POST /api/v1/notifications/test` `{audience_kind, audience}` (`ops_admin`; denetim olayı).
- `GET /api/v1/ops/alerts?view=active|history` (`platform` okuma): uyarılar, öneri, onay, kural kataloğu. `POST /api/v1/ops/alerts/{sys_id}/ack` `{note (10–1000)}` (`ops_admin`; yalnızca ateşlenmiş ve bir kez; `409 onaylanamaz`).
- `GET /metrics` (Prometheus metin biçimi 0.0.4): yalnızca `EVREN_METRICS_TOKEN_FILE` tanımlıysa, `Authorization: Bearer`; yoksa 404, yanlışsa 401.

### Evren MCP sunucusu ve öneriler (ADR-0043)

- `POST /mcp`: MCP Streamable HTTP, JSON-RPC 2.0. Yöntemler `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`. Entra'da `Authorization: Bearer` (erişim belirteci) zorunludur; çerez kabul edilmez. Araçlar ve sınırlar `docs/09-mcp-sozlesmesi.md`'dedir.
- `POST /api/v1/proposals` `{kind: ci_update|match_decision, target, payload, rationale, model, prompt_version, input_summary, idempotency_key}` (kurum MCP uygulamasının REST adaptörü; `cmdb_ci` okuma). Aynı anahtar aynı öneriyi döner (`200 replayed`).
- `GET /api/v1/proposals?status=pending|decided`: öneriler, özet, gerekçe, model ve `can_decide`.
- `POST /api/v1/proposals/{id}/decide` `{decision: accept|reject, note (10–2000)}`. Kabul, kararı verenin yetkisiyle uygulanır. Kendi öneriniz için `409 kendi_oneriniz`, karar verilmiş öneri için `409 karar_verilmis` döner. Uygulanamazsa `409 uygulanamadi` döner ve öneri "uygulanamadı" olarak kaydedilir.

### ServiceNow replikası (ADR-0042)

- `GET /api/v1/replica` (`platform` okuma veya `replica_admin`): mod, tur sınırı, çalışanın canlılığı ve bağlantısı, durum sayıları (eşit, bekliyor, çelişki, hata, dışarıda), gölge sonuçları, ilişkiler, bekletilen/onaylı tur, son 10 çalışma, kapsam (sınıf, CI sayısı), `shadow_done`, `can_admin`.
- `GET /api/v1/replica/items?view=attention|conflict|error|shadow|excluded|in_sync&limit&offset`: CI, durum, gölge sonucu, ServiceNow'un bulduğu sys_id, neden, gönderilmeyen alanlar.
- `PUT /api/v1/replica/settings` `{mode: off|shadow|active, max_changes (1–100000), note (10–1000)}` (`replica_admin`; etkin moda geçiş tamamlanmış gölge turu ister, yoksa `409 golge_gerekli`).
- `PUT /api/v1/replica/scope/{sınıf}` `{enabled, note}` (`replica_admin`; listede olmayan sınıf `404`).
- `POST /api/v1/replica/runs/{id}/approve` `{note}` (`replica_admin`; yalnızca bekletilen tur, bir kez; `409 onaylanamaz`).
- `POST /api/v1/replica/items/{sys_id}/resolve` `{action: accept|exclude|retry, note}` (`replica_admin`; `accept` yalnızca ServiceNow'un bir kayıt bulduğu çelişkide ve kayıt başka bir Evren CI'ına eşli değilse — aksi `409 baska_ci_eslesik`).
- Hepsi denetim izine yazılır; değişiklikten sonra sn-sync `NOTIFY evren_replica` ile uyandırılır. ServiceNow'a API yazmaz.

### Kalite kuralları, bulgular ve CMDB sağlığı (ADR-0037)

Okuma: CI okuma izni (`cmdb_ci` / `read`); okunamayan sınıftaki CI'ın bulgusu listelenmez. Yazma: Konfigürasyon yöneticisi (`governance` / `task_admin`). Yazma uç noktaları CSRF başlığı ister.

- `GET /api/v1/quality/health`: son ölçüm (`overall`, `grade`, `dimensions` → `score`, `grade`, `metrics`: `label`, `good`, `total`, `pct`, kural ölçümünde `rule`, `open`, `excepted`), `measured_at`, `stale` (2 saatten eski), son 30 günün günlük `trend`'i, `findings` (açık, istisna, ayıklama). Ölçüm yoksa `measured: false`.
- `GET /api/v1/quality/rules`: kurallar (tür, kapsam, boyut, önem, sıklık, açık/istisna/ayıklama sayısı, son değerlendirme, durum) ve kataloglar: `kinds`, `dimensions`, `severities`, `intervals`, `roles`, `relationship_types`, `templates`, `classes`.
- `GET /api/v1/quality/rules/{sys_id}`: ayrıntı, son 20 değerlendirme (`status`: done, failed, inconclusive), izinli geçişler. `GET /api/v1/quality/fields?class=`: sınıfın sözlük alanları.
- `POST /api/v1/quality/rules/preview`: `{check_kind, params, scope_class, scope_query?, rule?}` → `{valid, in_scope, matched, by_basis, people, groups, sample (why ile), inconclusive, partial}`; geçersiz kural 200 `{valid: false, problem}`.
- `POST /api/v1/quality/rules` (`idempotency_key`): `{name, description?, check_kind, params, scope_class, scope_query?, dimension, severity, open_task, task_title, task_instructions?, due_days (1–180), channel?, exception_days (1–730), max_findings (1–50 000), interval_hours (1|6|24|168), activate}` → 201. Hatalar: `kural_gecersiz`, `kanal_yok` (422), `ad_var` (409).
- `PATCH /api/v1/quality/rules/{sys_id}`: düzenleme (yalnızca taslak/duraklatılmış) ve/veya `{state, reason (≥ 10)}`. `POST /api/v1/quality/rules/{sys_id}/run`: `{reason}` → 202.
- `GET /api/v1/quality/findings?rule=&ci=&state=current|open|excepted|resolved|all&triage=&dimension=&q=`: bulgular (CI, kural, neden `text`, son görev, istisna) ve sayılar. `GET /api/v1/quality/findings/{sys_id}`: ayrıntı ve bütün görevleri, `can_except`, `can_reopen`.
- `POST /api/v1/quality/findings/{sys_id}/exception`: `{reason (≥ 10), days (1–730)}`; açık görevi iptal edilir. `POST /api/v1/quality/findings/{sys_id}/reopen`: `{reason}`; istisnayı kaldırır veya ayıklamayı bitirir.
- Kural görevinde (`kind: finding`) `GET /api/v1/tasks/{sys_id}` → `finding` (kural, neden, istisna süresi) ve kararlar: Düzeltildi, Geçerli değil (süreli istisna), Reddedildi (sorumlusu ben değilim). `GET /api/v1/ci/{sys_id}` → `findings`.

### Zamanlanmış kampanyalar (ADR-0036)

Okuma: Konfigürasyon yöneticisi (`task_admin`) veya Denetçi (`audit_read`); yazma yalnızca Konfigürasyon yöneticisi. Yazma uç noktaları CSRF başlığı ister.

- `POST /api/v1/campaigns/preview`: `{scope_class, scope_query?, campaign?}` → `{valid, in_scope, by_basis: {owned_by, managed_by, support_group, unassigned}, people, groups, already_open, sample}`. Geçersiz koşul 200 `{valid: false, problem}`.
- `GET /api/v1/campaigns`: kampanyalar (tür, kapsam, sıklık, `next_run_at`, açık/süresi geçen/kapanan görev sayısı, durum), `kinds`, `frequencies`, `classes` (kapsam seçimi için sınıf ağacı), `can_admin`.
- `GET /api/v1/campaigns/{sys_id}`: ayrıntı, son 20 çalışma (`trigger`, `status`, `in_scope`, `created`, `already_open`, `unassigned`, `note`), izinli geçişler (`transitions`).
- `POST /api/v1/campaigns` (`idempotency_key`): `{name, kind: attestation|access_review|checklist, title, instructions?, scope_class, scope_query?, frequency: monthly|quarterly|semiannual|annual | schedule (cron), due_days (1–180), channel?, max_tasks (1–20 000), activate}` → 201 `{sys_id, state, next_run_at, message}`. Hatalar: `kampanya_gecersiz`, `siklik_gerekli`, `kanal_yok` (422), `ad_var` (409).
- `PATCH /api/v1/campaigns/{sys_id}`: düzenleme (yalnızca taslak veya duraklatılmış; aksi 409 `duzenlenemez`) ve/veya `{state: active|paused|retired, reason (≥ 10)}`; izinsiz geçiş 409 `gecis_yok`.
- `POST /api/v1/campaigns/{sys_id}/run`: `{reason (≥ 10)}` → 202 `{run}`; yalnızca etkin kampanya (409 `etkin_degil`), bekleyen çalışma varken 409 `calisiyor`. Görev çalışanı bir sonraki turunda yürütür.
- `GET /api/v1/tasks?campaign=<sys_id>`: kampanyanın görevleri.

### Kullanıcılar ve roller (ADR-0034)

Okuma: Yetki yöneticisi (`iam_admin`) veya Denetçi (`audit_read`); yazma yalnızca Yetki yöneticisi. Yazma uç noktaları CSRF başlığı ister.

- `GET /api/v1/iam/roles`: rol–yetki matrisi. Her rol için `elevated`, `machine`, kalıtılan roller (`contains`), kalıtılanlar dahil izinler (`permissions`: `resource`, `operation`, Türkçe adları, `from`), birleşemediği roller (`sod`), atama sayıları (`holders`); `policy` (süre sınırları, en kısa gerekçe, kişi kimliği türü).
- `GET /api/v1/iam/people?q=&role=&status=active|inactive|all&expiring=true&limit=&offset=`: kişiler ve rolleri (doğrudan ve grup yoluyla; süre, veren, gerekçe), `can_sign_in` (Entra kimliği var mı).
- `GET /api/v1/iam/people/{sys_id}`: kişi, grupları, yetki geçmişi; `is_me`.
- `POST /api/v1/iam/people` (`idempotency_key`): `{user_name, name, email?, entra_oid?}` → 201. Kişi Evren kaynaklı açılır; aynı kullanıcı adı veya Entra kimliği 409.
- `PATCH /api/v1/iam/people/{sys_id}`: `{name?, email?, entra_oid?, clear_entra_oid?, active?, reason?}`. ServiceNow/Entra kaynaklı kişide yalnızca Entra kimliği (422 `kaynak_kisi`). Entra kimliği değişikliği ve pasifleştirme gerekçe ister; eski kimliğe verilmiş doğrudan roller geri alınır (`revoked`). Kendi kaydı 403.
- `GET /api/v1/iam/groups?q=` ve `PATCH /api/v1/iam/groups/{sys_id}` (`{entra_group_id, reason}`): grup ve Entra grup kimliği; rolü olan grubun kimliği değiştirilemez (409).
- `POST /api/v1/iam/grants` (`idempotency_key`): `{principal_type: user|group, target: sys_id, role, expires_at?, justification}` → 201 (`renewed`: aynı rol yenilendi). Hatalar: `gerekce_gerekli`, `makine_rolu`, `sure_gerekli`, `sure_uzun`, `bitis_gecmiste`, `entra_kimligi_yok`, `grup_kimligi_yok`, `kisi_pasif`, `kendine_rol` (403), `gorevler_ayriligi` (409).
- `POST /api/v1/iam/grants/revoke`: `{principal_type, target, role, reason}`; kendi rolü 403, olmayan atama 404.
- `GET /api/v1/iam/events?limit=`: rol verme, yenileme, geri alma ve kişi değişiklikleri (yapan, kime, rol, süre, gerekçe).
- `/api/v1/me` → `can.iam_read`, `can.task_admin`. Veritabanı rolleri her istekte okunur; verilen veya geri alınan rol bir sonraki istekte geçerlidir.

### İlişki haritası ve zaman içinde sorgu (ADR-0031)

- `GET /api/v1/ci/{sys_id}/as-of?at=<ISO 8601, saat dilimiyle>`: CI'ın o andaki hâli. Yanıtta şunlar bulunur:
  - `existed`, `created_on`, o andaki `class`;
  - `attributes` (`then`, `now`, `changed`) ve `changed_count`;
  - `relations` (`then`, `added_since`, `removed_since`; her biri `phrase`, `direction`, `other`, `other_name`);
  - `hidden_relations`.

  Saat dilimi yoksa 422 `saat_dilimi_yok`; gelecek an 422 `gelecek_zaman`; o andan bu yana 20 000'den fazla değişiklik varsa 422 `cok_eski_an`.
- `GET /api/v1/ci/{sys_id}/timeline?limit=200&before=<imleç>`: değişiklikler yeniden eskiye, anlara toplanmış. Aynı çalışmanın (`run_id`) veya aynı elle isteğin değişiklikleri tek andır. Her an şunları taşır: `at`, `source`, `actor`, `reason`/`reason_label`, `changes` (`label`, `old`, `new`) ve `relations` (`action`: added/removed, `phrase`, `other_name`, `no_effect`, `ended`). `next_cursor` bir anı ikiye bölmez.
- `GET /api/v1/ci/{sys_id}/graph?depth=2&at=` (derinlik 1–3, en çok 120 düğüm): CI'ın çevresi.
  - `nodes`: `level` (bağımlılar negatif/üstte, bağımlılıklar pozitif/altta, etkisiz bağlantılar aynı düzeyde), `depth`, `center`, `service`, `production`, `retired`.
  - `edges`: `parent_phrase`, `child_phrase`, `impact`, `dependent`, `sources` (bildiren kaynaklar).
  - Okunamayan sınıftaki CI haritaya girmez ve üzerinden genişlenmez; yalnızca `hidden` sayısı döner. `at` verilirse ilişkiler o ana geri sarılır, adlar bugünkü hâliyledir.
- CI ayrıntısındaki `relations[].sources`: ilişkiyi bugün bildiren kaynaklar. `source` ilk bildireni gösterir.

### Bağımlı CI (ADR-0029)

- Alım zarfında isteğe bağlı `parent: {"source_key": "…", "data_source": null}`: bağımlı sınıfın kaydı ana kaydını bildirir (ör. NNMi arayüzü → düğüm). Eşleme dosyasında `parent: {source_key: {from: …}}`. Bağımlı olmayan sınıfta yok sayılmaz ama kullanılmaz.
- CI ayrıntısında `dependents` (bu CI'a bağlı CI'lar ve yerel anahtarları) ve `depends_on` (bağımlı CI'ın ana CI'ı); CI'a başvuran alanlar adla gösterilir.

### Eskime (ADR-0028)

- `GET /api/v1/staleness/summary`: öneri sayıları (`candidate`, `stale`, `retired`, `dismissed`, `recovered`, `reappeared`), kurallar (sınıf ve süreler), gözlemci kaynakların son çalışma sağlığı (`ok`, `unhealthy`, `none`) ve `can_decide`.
- `GET /api/v1/staleness/cases?view=candidate|stale|retired|dismissed&q=`: öneriler (son görülme, gün, kaynak başına gözlem, karar). Okuma izni olmayan sınıfın CI'ı listelenmez.
- `POST /api/v1/staleness/{id}/decision` (CSRF, `idempotency_key`, `retire` izni): `{"action": "retire" | "dismiss", "note": "…"}` → `202`. Hatalar: `409 aday_degil` (eskimiş aşamada emekliye ayrılamaz), `409 oneri_kapali`, `409 bekleyen_istek`, `422` (gerekçe boş), `403`, `404`.
- `GET /api/v1/retirement-requests/{id}`: `pending | applied | rejected` ve uzlaştırıcının notu (ör. "CI yeniden görüldü", "devre kesici").
- CI ayrıntısında `staleness`: son öneri, kural, `can_decide`, `can_retire`.

### Değişiklik denetimi (ADR-0025)

- `GET /api/v1/ci/{sys_id}` yanıtındaki `change_control`:
  - `observations`: gözlenen değişiklikler (öznitelik, eski → yeni, kaynak, değişiklik aralığı, sonuç `pending | authorized | unauthorized | suppressed`, kapsayan değişiklik numarası, Türkçe açıklama, `late`). Alan hassasiyetine göre süzülür.
  - `changes`: CI'a `cmdb_ci` veya `task_ci` ile bağlı değişiklik kayıtları (Evren'de doğan CI için replika sys_id'si de aranır); durum, onay ve tür Türkçe etiketli.
  - `anomalies`, `open_unauthorized`, `can_review`.
- `GET /api/v1/ci?scope=yetkisiz-degisiklik`: açık veya incelenen yetkisiz değişikliği olan CI'lar. `GET /api/v1/summary` → `unauthorized_changes`.
- `POST /api/v1/anomalies/{sys_id}/review` (CSRF başlığı, `anomaly/anomaly_review`): gövde `{"status": "acknowledged" | "false_positive" | "open", "note": "…"}`; yanlış alarmda gerekçe zorunlu. `resolved` istenemez (sistemin işi); uygun olmayan geçiş 409. Aynı karar tekrar gönderilirse `changed: false` döner (doğal olarak tekrar güvenli). Karar `audit.security_event` `anomaly_review`.

## ServiceNow uyumlu Table API (ADR-0044)

ServiceNow CMDB'sini Table API ile okuyan betik ve entegrasyonlar yalnızca adres ve kimlik değiştirerek Evren'i okur. Yeni entegrasyonlar `/api/v1`'i kullanmalıdır.

- **Uç noktalar:** `GET /api/now/table/{tablo}` ve `GET /api/now/table/{tablo}/{sys_id}`. `/api/now/v2/table/…` de aynıdır. Yazma yöntemleri 405 döner.
- **Tablolar:** CMDB sınıfları (`cmdb_ci` soyağacı; üst tablo alt sınıfların kayıtlarını da döner), `cmdb_rel_ci`, `cmdb_rel_type` ve referans tabloları (`sys_user`, `sys_user_group`, `cmn_location`, `core_company`, `cmn_department`, `cmn_cost_center`, `cmdb_model`).
- **Kimlik:** Entra erişim belirteci (`Authorization: Bearer`, MCP ile aynı doğrulama) veya oturum. Temel kimlik doğrulama yoktur.
- **Yetki:**
  - Tablo okuma izni gerekir. İlişki tabloları için `cmdb_rel_ci`, referans tabloları için `cmdb_ci` izni.
  - Reddedilen alt sınıf üst tabloda da görünmez. İlişki, iki ucu okunabiliyorsa görünür.
  - Hassasiyet sınırını aşan alan yanıtta yoktur ve süzgeçte kullanılamaz. Kişi e-postası yalnızca gizli düzeyde görünür.

Parametreler:

- `sysparm_query` — encoded query:
  - Mantık: `^`, `^OR`, `^NQ`, `ORDERBY`, `ORDERBYDESC`.
  - Operatörler: `=`, `!=`, `LIKE`, `NOT LIKE`, `STARTSWITH`, `ENDSWITH`, `IN`, `NOT IN`, `ISEMPTY`, `ISNOTEMPTY`, `>`, `>=`, `<`, `<=`, `BETWEEN`, `ANYTHING`, `INSTANCEOF`. Metin karşılaştırması büyük/küçük harf duyarsızdır.
- `sysparm_fields`: bilinmeyen veya görülmeyen alan atlanır.
- `sysparm_limit` (varsayılan ve en çok 10000, ServiceNow'daki gibi), `sysparm_offset`. Sıralama `sys_id` ile tamamlanır; sayfalar kararlıdır.
- `sysparm_exclude_reference_link`.
- `sysparm_display_value`: yalnızca `false`.

Yanıt:

- **Gövde:** `{"result": [...]}`. Sonuçsuz sorgu 200 ve boş dizi döner.
- **Değerler:** Metindir; boş değer `""`, mantıksal değer `"true"`/`"false"`, zaman UTC `YYYY-MM-DD hh:mm:ss`. Referans `{"link", "value"}` biçimindedir.
- **Başlıklar:** `X-Total-Count` ve `Link` (`first`, `prev`, `next`, `last`).
- **Hata:** `{"error": {"message", "detail"}, "status": "failure"}`. Tekil kayıt yoksa 404 `No Record found` döner.

Bilinçli farklar (güvenlik):

- `javascript:` değerleri reddedilir; sunucu tarafında betik çalışmaz.
- Dot-walk (`support_group.name`) desteklenmez ve 400 döner.
- Koşul sayısı 200, IN listesi 1000 ile sınırlıdır.
- `sysparm_display_value=true|all`, `sysparm_view` ve `sysparm_no_count` yoktur.

Örnek:

```
GET /api/now/table/cmdb_ci_server?sysparm_query=sys_class_nameINSTANCEOFcmdb_ci_linux_server^operational_status=1^ORDERBYname&sysparm_fields=sys_id,name,ip_address,owned_by&sysparm_limit=100
Authorization: Bearer <Entra erişim belirteci>
```

## IRE uyumlu alım ucu (ADR-0045)

ServiceNow CMDB'ye bugün IRE API ile yazan araçlar aynı yükü Evren'in **alım geçidine** gönderir. Yük golden record'a yazılmaz. Her öğe, aracın veri kaynağı adına bir kaynak kaydı olur ve uzlaştırıcıdan geçer.

| İstek | Sonuç |
| --- | --- |
| `POST /api/now/identifyreconcile?sysparm_data_source=<kaynak>` | Yük tek bir delta çalışması olur, öğeler Kafka'ya gider. 200 ve IRE biçiminde yanıt: öğeler `QUEUED`, `sysId` `Unknown`; `result.evren` alanında `run_id`, `status_path` (`/ingest/v1/runs/{id}`) ve uyarılar |
| `POST /api/now/identifyreconcile/query?sysparm_data_source=<kaynak>` | Yalnızca doğrular (`VALIDATED`); hiçbir şey yazılmaz |

- **Kimlik:** bağlayıcı belirteci (`Authorization: Bearer evr_…`). `sysparm_data_source` belirtecin kaynağıyla aynı olmalıdır.
- **Zorunlu alan:** `sys_object_source_info.source_native_key`.
- **Ya hep ya hiç:** bir öğe hatalıysa hiçbiri alınmaz. Hata ServiceNow biçiminde döner; ayrıntı sorunun yerini söyler, girdiyi yansıtmaz.
- **Bağımlı sınıf:** Uygulama gibi bağımlı sınıfların ana CI'ı, kuraldaki ilişkiyle (`Runs on::Runs`) eşleşen öğedir.
- **İşlenmeyen veya atılan parçalar:**
  - `related`, `referenceItems` ve `settings` işlenmez.
  - Sözlükte olmayan alanlar ve `sys_*` alanları uyarıyla atılır.
  - Referans alanı sys_id bekler.

Dönüşüm tablosu ve bilinçli farklar: ADR-0045.

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

Bağlayıcı `Authorization: Bearer evr_…` ile gelir. Belirteci `tools/connector_credential.py issue` verir; arayüzden yönetim U5'te gelecek. Belirteç tek bir veri kaynağına bağlıdır.

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
- Platform uyarısının ateşlenmesi, çözülmesi ve onayı `evren.cmdb.activity.v1` konusundadır: `type` = `evren.ops.alert.fired|resolved|acked` (kural, konu, önem, başlık, ayrıntı, değer).
- Denetim izi `evren.audit.v1` konusundadır (1 yıl saklama, ADR-0039): `type` = `evren.audit.security_event` (kim, ne, nesne, izin/ret, kaynak IP, istek kimliği, ayrıntı), `evren.audit.seal` (akış, dönem, kayıt sayısı, özet, önceki zincir, zincir), `evren.audit.seal_check` (mühür, doğrulayan, sonuç, sorun). Kurum SIEM'i bu konuyu tüketir; mühür zincir değerleri veritabanı dışında tanık olur.
- Webhook (F2-06): `X-Evren-Signature: sha256=<HMAC>` + zaman damgası (tekrar saldırısına karşı 5 dakika penceresi).

## GRC Platformu (tedarik) entegrasyonu (F2-08)

Örnek otomatik kontroller (kurum MCP uygulaması veya GRC Platformu zamanlayıcısı çalıştırır):

| Kontrol | Evren sorgusu | Sonuç |
| --- | --- | --- |
| CMDB'de kaydı olmayan sunucu | Defender/Zabbix'te olup golden record'a bağlanmamış kaynak kayıtları | fail + kanıt listesi |
| İzlenmeyen üretim sunucusu | `used_for=Production` ve Zabbix bağı yok | fail/pass |
| Korumasız uç nokta | Sunucu sınıfı ve Defender bağı yok | fail/pass |
| Yetkisiz değişiklik (CMDB-05, ADR-0025) | açık veya incelenen `insight.anomaly kind=unauthorized_change`; değişiklik kaynağı rolü sağlıklı değilse `inconclusive` | fail + kanıt listesi (CI) |
| Sahiplik onayı geçersiz (CMDB-06, ADR-0027) | kapsamdaki CI'ın onay durumu `not_reviewed`, `expired` veya `rejected` (`metadata/attestation.yaml`) | fail + kanıt listesi (CI, durum) |
| Sahipsiz kritik CI | `busines_criticality` yüksek ve `owned_by` boş | fail/pass |

Kaynak çalışması devre kesicide `WARN` veya `OPEN` ise `collection_status=partial` gönderilir ve GRC Platformu sonucu `inconclusive` olur.
