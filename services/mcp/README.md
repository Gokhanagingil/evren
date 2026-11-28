# services/mcp — Evren MCP sunucusu (U9.2, F2-07, ADR-0043)

MCP sunucusu ayrı bir servis değildir: API sürecinde `POST /mcp` uç noktasıdır
(`services/api/evren_api/routers/mcp.py`). Araçlar REST'in aynı işlevlerini, çağıranın etkin rolleri ve alan hassasiyetiyle
çağırır; ayrı yetki motoru ve ayrı veritabanı rolü yoktur.

- **Sözleşme ve araçlar:** `docs/09-mcp-sozlesmesi.md`.
- **Karar:** `docs/adr/0043-evren-mcp-sunucusu.md`.
- **Öneriler:** `services/api/evren_api/routers/proposals.py` (`ai.proposal`, göç 0037).
