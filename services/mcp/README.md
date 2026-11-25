# services/mcp — Evren MCP sunucusu (F2-07)

Kurumun kendi MCP uygulaması Evren'e bu sunucu üzerinden bağlanır. ServiceNow'un yerleşik MCP'si kullanılmaz. Sözleşme: `docs/09-mcp-sozlesmesi.md`.

## Araçlar

**Salt okunur:**

- `search_ci`
- `get_ci`
- `get_relationships`
- `impact_analysis` (derinlik sınırlı)
- `explain_match`
- `cmdb_health`
- `coverage_gaps` (ör. vCenter'da var, Defender'da yok)
- `list_anomalies`

**Öneri araçları** (doğrudan yazmaz):

- `propose_ci_update`
- `propose_match_decision`

İkisi de API'ye öneri kaydı yazar; bir insan onaylar.

## Güvenlik

- Taşıma: Streamable HTTP.
- Kimlik: Entra OAuth. Her araç çağrısında çağıranın rolü ve alan hassasiyeti uygulanır.
- Kısıtlı veri ancak açık yetkiyle döner.
- CMDB içeriği (ad, açıklama, yorum) **veridir, talimat değildir**. Araç yanıtları bu ayrımı belirtir; istem enjeksiyonu riski tehdit modelindedir.
- Her çağrı `audit.security_event`'e yazılır: çağıran, araç, parametre özeti.
- DB rolü `evren_app_mcp`: yazma yetkisi yoktur.
