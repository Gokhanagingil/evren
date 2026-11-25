# Servisler

Her servis ayrı bir süreç, ayrı bir veritabanı rolü ve ayrı bir kimliktir. Ortak alan mantığı `packages/evren-core` içindedir; servisler yalnızca G/Ç (HTTP, Kafka, PostgreSQL) ve orkestrasyon ekler.

| Servis | Faz | DB rolü | Yazdığı yer | Okuduğu yer |
| --- | --- | --- | --- | --- |
| `api` | F0 | `evren_app_api` | Manual kaynak kayıtları, eşleştirme kararları, şema setleri, rol atamaları | CMDB, sözlük, içgörü |
| `ingest-gateway` | F0 | `evren_app_ingest` | `ingest.source_record`, `ingest.source_run` (ve Kafka) | `ingest.data_source` |
| `reconciler` | F0 | `evren_app_reconciler` | **Golden record** (tek yazar), ilişkiler, kimlik bağları, denetim, outbox | Her şey |
| `sn-sync` | F2 | `evren_app_sync` | `ref.*`, `sync.sn_ci_map`, "ServiceNow" kaynak kayıtları | CMDB |
| `mcp` | F2 | `evren_app_mcp` | Hiçbir yere (öneriler API üzerinden) | CMDB, sözlük, IAM |

Ortak kurallar:

- Sağlık uç noktası: `/healthz` (canlılık) ve `/readyz` (bağımlılıklar).
- Yapılandırılmış JSON log; kişisel veri ve sır içermez.
- Kapanışta yarım işi bırakmaz.
- Konteyner: root olmayan kullanıcı, salt okunur kök dosya sistemi, `cap_drop: ALL`, `no-new-privileges`. GRC Platformu (tedarik) Dockerfile deseni.
- Yapılandırma ortam değişkeninden okunur; sırlar dosya yolundan (`*_FILE`).
