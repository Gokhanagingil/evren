# ADR-0005: Uygulama yığını — Python 3.12, FastAPI, psycopg 3

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

KS GRC (`control`) ve tedarik portalı Python 3.12 ile yazıldı. Evren bunlarla birleşebilmeli veya tek uygulama gibi davranabilmeli. Öte yandan Evren'in ölçeği (milyonlarca kayıt, eşzamanlı alım) standart kütüphane HTTP sunucusunun ötesinde.

## Karar

- Alan mantığı saf Python olarak `packages/evren-core` içinde yazılır. G/Ç içermez ve testlidir.
- Servisler: FastAPI (ASGI) + psycopg 3 (bağlantı havuzu) + Pydantic v2. Kafka için confluent-kafka.
- Bağımlılıklar `pip-compile --generate-hashes` ile kilitlenir ve `--require-hashes` ile kurulur; KS GRC'deki gibi.
- Kimlik: Entra ID, KS GRC'deki `entra_auth.py` deseni.

## Sonuçlar

- **Olumlu:** Ekip ve platform tutarlılığı; ortak kimlik ve sözleşme ilkeleri.
- **Olumsuz:** KS GRC'ye göre daha fazla bağımlılık. Her biri ADR ve güvenlik taramasıyla eklenir.
