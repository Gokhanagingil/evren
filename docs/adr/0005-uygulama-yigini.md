# ADR-0005: Uygulama yığını — Python 3.12, FastAPI, psycopg 3

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

GRC Platformu (`Gokhanagingil/tedarik`) Python 3.12 ile yazıldı. Evren bunlarla birleşebilmeli veya tek uygulama gibi davranabilmeli. Öte yandan Evren'in ölçeği (milyonlarca kayıt, eşzamanlı alım) standart kütüphane HTTP sunucusunun ötesinde.

## Karar

- Alan mantığı saf Python olarak `packages/evren-core` içinde yazılır. G/Ç içermez ve testlidir.
- Servisler: FastAPI (ASGI) + psycopg 3 (bağlantı havuzu) + Pydantic v2. Kafka için confluent-kafka.
- Bağımlılıklar hash ile kilitlenir ve `--require-hashes` ile kurulur; GRC Platformu'ndaki gibi. Kilit `uv pip compile --generate-hashes` ile CPython 3.12 / Linux için üretilir (`requirements/*.in` başındaki komut).
- **Tedarik zinciri bekleme süresi:** yalnızca en az 14 gün önce yayımlanmış sürümler kilitlenir (`--exclude-newer`). Yeni yayımlanan bir sürümün geri çekilme veya ele geçirilme riskine karşı.
- Kimlik: Entra ID, GRC Platformu'ndaki `tedarik/vendor/entra_auth.py` deseni.

## Bağımlılıklar ve gerekçeleri (25.11.2026)

| Paket | Nerede | Gerekçe |
| --- | --- | --- |
| pydantic, PyYAML | çekirdek | Alım zarfı doğrulaması; metadata ve eşleme dosyaları |
| fastapi, starlette, uvicorn | API | ASGI servis, OpenAPI 3.1; uvicorn `[standard]` ekleri bilinçli olarak alınmaz |
| psycopg, psycopg-binary, psycopg-pool | API, uzlaştırıcı | PostgreSQL sürücüsü ve bağlantı havuzu; GRC Platformu ile aynı sürücü |
| PyJWT, cryptography | API | Entra ID belirteci (RS256) doğrulama; Fernet ile mühürlü akış ve oturum çerezi |
| httpx | yalnızca test | FastAPI `TestClient` gereksinimi |
| pytest, jsonschema, playwright, ruff | yalnızca geliştirme | Test, şema, tarayıcı duman testi, lint |

Kafka istemcisi (confluent-kafka) uzlaştırıcının Kafka adaptörüyle (F0-10 ikinci adım) ayrı değişiklikte eklenir.

## Sonuçlar

- **Olumlu:** Ekip ve platform tutarlılığı; ortak kimlik ve sözleşme ilkeleri.
- **Olumsuz:** GRC Platformu'na göre daha fazla bağımlılık. Her biri ADR ve güvenlik taramasıyla eklenir.
