# services/ingest-gateway — Alım geçidi (F0-10)

Bağlayıcıların tek giriş noktasıdır.

## Doğrulama zinciri

1. mTLS: bağlayıcının istemci sertifikası.
2. Sertifika veya anahtar ↔ `data_source` eşlemesi. Bir bağlayıcı yalnızca kendi kaynağı adına yazabilir.
3. JSON şema doğrulaması: `schemas/ingest-envelope.schema.json`. Bilinmeyen alan reddedilir.
4. Boyut ve hız sınırı.

## Doğrulamadan sonra

- Kafka `ingest.record` konusuna yazılır.
- `ingest.source_run` başlatma ve bitiş olayları işlenir: `ingest.run.started.v1` ve `ingest.run.completed.v1`.

## Neyi yapmaz

Eşleştirme, uzlaştırma veya CMDB yazımı yapmaz.

## Hata sözleşmesi

- `400` şema hatası (hangi alan)
- `403` kaynak yetkisi yok
- `409` kapalı çalışmaya kayıt
- `429` hız sınırı

Kısmi gönderimde çalışma `completed=false` kalır; devre kesici açılır.
