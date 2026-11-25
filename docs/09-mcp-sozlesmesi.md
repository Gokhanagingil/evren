# 09 · MCP sözleşmesi

**Karar (25.11.2026):** "MCP" kurumun kendi MCP uygulamasıdır. ServiceNow'un yerleşik MCP sunucusu kullanılmaz.

Evren iki yoldan bağlanır:

1. **Evren MCP sunucusu** (`services/mcp`): yapay zekâ ajanlarına araç sunar.
2. **REST sözleşmesi:** kurum MCP uygulamasının adaptörü Evren API'sini KS GRC'deki gibi doğrudan kullanır.

## Sınırlar

- MCP **veri replikasyonu için kullanılmaz.** ServiceNow eşlemesi sn-sync ve IRE API ile, kaynak alımı bağlayıcılarla yapılır.
- MCP üzerinden **doğrudan yazma yoktur.** Yazma araçları öneri üretir; öneri arayüzde bir insan tarafından onaylanır.
- Araç yanıtındaki CMDB içeriği (ad, açıklama, yorum) **veridir, talimat değildir.** Yanıtlarda içerik alanları açıkça işaretlenir: `"content_is_data": true`.

## Kimlik ve yetki

- **Taşıma:** Streamable HTTP. TLS zorunlu.
- **Kimlik:** Entra OAuth.
  - Kullanıcı adına çalışan ajanlarda kullanıcının belirteci; yetki o kullanıcının rolleriyle sınırlıdır.
  - Arka plan orkestrasyonunda ayrı makine kimliği (`evren_integration` rolü).
- Her çağrı `audit.security_event`'e yazılır: çağıran, araç, parametre özeti, sonuç sayısı.
- **Hız ve boyut sınırı:** araç başına sonuç limiti; etki analizi derinliği en fazla 4.

## Araçlar (F2-07)

| Araç | Tür | Girdi | Çıktı |
| --- | --- | --- | --- |
| `search_ci` | okuma | `query` (serbest metin veya encoded query), `class`, `limit` | CI özeti listesi |
| `get_ci` | okuma | `sys_id` | Öznitelikler, öznitelik başına kaynak bilgisi, kaynak kapsamı |
| `get_relationships` | okuma | `sys_id`, `direction`, `types` | İlişki listesi |
| `impact_analysis` | okuma | `sys_id`, `depth` (≤4) | Etkilenen servisler/CI'lar ve yolları |
| `explain_match` | okuma | `match_candidate_id` | Kanıtlar (Türkçe), skor, adaylar |
| `cmdb_health` | okuma | `class`, `period` | Tamlık/doğruluk/uyum/kapsam metrikleri |
| `coverage_gaps` | okuma | `source_a`, `source_b`, `class` | A'da olup B'de olmayan CI'lar |
| `list_anomalies` | okuma | `kind`, `status`, `period` | Anomali kayıtları |
| `propose_ci_update` | öneri | `sys_id`, `changes`, `rationale` | Öneri kimliği (onay bekler) |
| `propose_match_decision` | öneri | `match_candidate_id`, `decision`, `rationale` | Öneri kimliği (onay bekler) |

## Kurum MCP uygulaması için REST akışı

KS GRC sözleşmesiyle aynı ilkeler geçerli:

1. `GET /api/v1/ci?sysparm_query=…` ile okuma. Sayfalamayı tamamla; eksik sayfa başarı sayılmaz.
2. Sonucu KS GRC'ye yazarken `provenance.query_reference` alanına Evren sorgusunu, `collection_status` alanına Evren kaynak çalışmalarının durumunu koy.
3. Yazma gerekiyorsa `POST /api/v1/proposals` kullan; `idempotency_key` zorunlu.

## Doğrulama

- Her araç için yetkisiz çağrı, yanlış rol, kısıtlı alan ve sınır aşımı testleri.
- İstem enjeksiyonu testi: CI açıklamasına yazılmış "tüm kayıtları sil" gibi bir metin hiçbir yazma etkisi üretmemeli.
- Sentetik testler canlı MCP uygulaması kabulü sayılmaz. Kabul için kurum MCP uygulamasıyla uçtan uca bir senaryo çalıştırılır.
