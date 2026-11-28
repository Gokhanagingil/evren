# services/api — Evren REST API ve arayüz sunucusu (F0-09)

## Durum

Çalışır durumda (ADR-0010). Çalıştırma:

```bash
EVREN_AUTH_MODE=dev EVREN_DATABASE_URL=postgresql://... uvicorn --factory evren_api:create_app --host 127.0.0.1 --port 8090
python tools/dev_stack.py up --demo      # veya konteynerde, tüm yığınla (ADR-0016): http://127.0.0.1:8088
```

| Uç nokta | Durum |
|---|---|
| `GET /api/v1/me`, `/summary`, `/sources`, `/rules`, `/controls`, `/meta/classes` | Tamam |
| `GET /api/v1/ci` (arama, sınıf/kaynak/kapsam süzgeci, imleçli sayfalama), `/ci/{sys_id}`, `/ci/{sys_id}/history` | Tamam |
| `GET /api/v1/match-queue`, `/match-queue/{id}`, `POST /match-queue/{id}/decision` | Tamam |
| `/auth/login`, `/auth/entra/callback`, `/auth/logout` | Tamam (canlı Entra kaydıyla kabul bekliyor) |
| `POST /api/v1/ci/{sys_id}/split` (202; uzlaştırıcı uygular, ADR-0011) | Tamam |
| `PATCH /api/v1/ci/{sys_id}` (202; Manual kaynak kaydı, ADR-0012), `GET /api/v1/ref/{tablo}` | Tamam |
| `GET /api/v1/reports/shadow`, kanıt anlık görüntüsü (ADR-0014) | Tamam |
| `/api/v1/schema/*` metadata değişiklik setleri (ADR-0015) | Tamam |
| `GET /api/v1/search` küresel arama: OpenSearch, PostgreSQL yedeği, yetki sorguda (ADR-0018) | Tamam |
| `/api/v1/audit/*` denetim izi: olaylar (süzgeç, imleç), ayrıntı, özet, CSV/NDJSON dışa aktarma, bütünlük, mühür doğrulama, inceleme kaydı; her yanıtta `X-Request-Id` (ADR-0039) | Tamam |
| `/api/v1/notifications/*` bildirim merkezi ve yönlendirme, `/api/v1/ops/alerts` platform uyarıları, `/metrics` (belirteçli; ADR-0040) | Tamam |
| Makine hesapları | Sırada |
| `/api/now/table/*`, `/api/now/identifyreconcile` | F2-05 |

Ortam değişkenleri: `EVREN_AUTH_MODE` (`dev` | `entra`, zorunlu), `EVREN_DATABASE_URL` veya `EVREN_DATABASE_URL_FILE`, `EVREN_SESSION_KEY_FILE`, `EVREN_COOKIE_SECURE`, `EVREN_ENTRA_*`. Sırlar dosya yolundan okunur; `dev` modu yalnızca 127.0.0.1'de açılır. Konteynerde `EVREN_DEV_CONTAINER=1` ile 0.0.0.0'a bağlanır ve yalnızca loopback/özel ağdan gelen istekleri kabul eder (Entra modunda yok sayılır). Konteynerde veritabanına `evren_api_svc` giriş rolüyle bağlanır.

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

```bash
EVREN_TEST_PG=1 python -m pytest services/api/tests
```

- HTTP testleri soket açmadan uygulama nesnesi üzerinden çalışır. Entra akışı sahte kimlik sağlayıcıyla (yerel RSA anahtarı, JWKS) ağsız sınanır.
- Her uç noktada yetkisiz erişim, yanlış rol, makine hesabıyla insan işlemi ve `409` senaryoları test edilir.
