# ADR-0007: MCP — kurumun kendi uygulaması + Evren MCP sunucusu

- Durum: Kabul edildi
- Tarih: 25.11.2026
- Karar verenler: Gökhan Ağıngil

## Bağlam

İster 4 "ServiceNow MCP'si ile entegre olabilmeli" idi. Proje sahibi bunun **kurumun kendi yazdığı MCP uygulaması** olduğunu, ServiceNow'un yerleşik MCP'sinin kullanılmayacağını belirtti.

## Karar

- Evren kendi MCP sunucusunu sunar (`services/mcp`): salt okunur araçlar ve öneri araçları.
- Kurum MCP uygulaması Evren'e bu sunucu üzerinden ve GRC Platformu'ndaki REST sözleşme ilkeleriyle bağlanır.
- MCP veri replikasyonu için kullanılmaz. MCP üzerinden doğrudan yazma yoktur.

## Sonuçlar

- **Olumlu:** Tek bir orkestrasyon katmanı (kurum MCP uygulaması) ServiceNow, Dynamics, GRC Platformu ve Evren'i birlikte kullanabilir.
- **Risk:** İstem enjeksiyonu. CMDB içeriği veri olarak işaretlenir; MCP rolü yazamaz.
