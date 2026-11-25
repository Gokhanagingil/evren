# services/api — Evren REST API ve arayüz sunucusu (F0-09)

## Sorumluluk

- `/api/v1/*` yerel REST API, OpenAPI 3.1 ile belgelenir.
- `/api/now/table/{table}` ve `/api/now/identifyreconcile`: ServiceNow uyumlu uç noktalar (F2-05). `evren_core.encoded_query` kullanılır.
- `apps/web` statik dosyalarını `evren_core.web.SECURITY_HEADERS` ile sunar. HSTS TLS sonlandırıcıda eklenir.
- Kimlik doğrulama: Entra ID OIDC + PKCE. GRC Platformu'ndaki `tedarik/vendor/entra_auth.py` deseni, `__Host-` önekli çerez.
- Makine hesapları: kapsamlı API anahtarı veya istemci sertifikası. Her anahtar tek bir `data_source` veya tek bir uygulamaya bağlıdır.

## Yazma ilkesi

API **golden record'a yazmaz.** CI düzenleme isteği şöyle işlenir:

1. Yetki kontrolü: RBAC + ABAC + alan hassasiyeti.
2. `ingest.source_record` içine `data_source='Manual'` kaydı yazılır.
3. Uzlaştırıcı kuralları uygular.
4. Yanıt `202 Accepted` + işlem kimliği döner. Arayüz sonucu olayla veya sorgulayarak gösterir.

Bu sayede elle girilen her değer de kaynak bilgisi, kural ve denetim izinden geçer.

## Sözleşme ilkeleri (GRC Platformu ile ortak)

- `idempotency_key`: aynı anahtar ve aynı içerik → aynı sonuç (`200`, `replayed: true`). Aynı anahtar ve farklı içerik → `409`.
- `provenance`: kaynak sistem, sorgu referansı ve `collection_status`. `partial` veya `error` verisi kesin sonuç sayılmaz.
- Hata gövdesi: `{"error": {"code": "...", "message": "<Türkçe>", "details": [...]}}`. İç ayrıntı sızdırılmaz.
- Sayfalama: `limit` (en fazla 1000) + imleç. Sorgu başına zaman aşımı uygulanır.

## İlk uç noktalar

- `GET /api/v1/ci?q=&class=&sysparm_query=` — arama ve listeleme.
- `GET /api/v1/ci/{sys_id}` — öznitelikler, kaynak bilgisi, kaynak kapsamı, ilişkiler.
- `GET /api/v1/ci/{sys_id}/history` — `audit.ci_change`.
- `PATCH /api/v1/ci/{sys_id}` — Manual kaynak kaydı (202).
- `GET /api/v1/match-queue`, `POST /api/v1/match-queue/{id}/decision` — kabul / ret / yeni CI.
- `POST /api/v1/ci/{sys_id}/split` — birleştirmeyi geri al.
- `GET /api/v1/meta/classes`, `GET /api/v1/meta/classes/{name}` — sözlük.
- `GET /api/v1/health/cmdb` — sağlık metrikleri.

## Testler

- HTTP testleri soket açmadan uygulama nesnesi üzerinden çalışır.
- Her uç noktada yetkisiz erişim, yanlış rol, makine hesabıyla insan işlemi ve `409` senaryoları test edilir.
