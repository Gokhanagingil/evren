# Evren CMDB

Kurum içi ve on-prem çalışan, ServiceNow veri deseniyle uyumlu konfigürasyon yönetim platformu. ServiceNow'dan kademeli çıkışın ilk adımıdır.

- **Kaynaklar:** vCenter, BMC, NNMi, Zabbix, Defender, ServiceNow.
- **Ara katman:** kimlik çözümleme ve uzlaştırma.
- **Golden record:** PostgreSQL.
- **Geçiş dönemi:** ServiceNow CMDB'yi replika olarak besler.

> **Durum:** Faz 0 — Temel ve gölge mod. Ayrıntı: [`DURUM.md`](DURUM.md) ve uygulamadaki **Yol Haritası** sayfası.

## Hızlı başlangıç (yerel)

```bash
# 1) Çekirdek testleri
cd packages/evren-core && python -m pytest && cd ../..

# 2) Arayüz: Genel Bakış, Mimari Harita, Yol Haritası
python tools/dev_server.py --port 8088     # http://127.0.0.1:8088

# 3) Veritabanı (yerel compose; .env.example → .env)
docker compose -f deploy/compose.dev.yaml up -d postgres
python tools/load_metadata.py --sql build/seed.sql
psql "postgresql://evren_owner@127.0.0.1:55432/evren" -v ON_ERROR_STOP=1 -f build/seed.sql
```

Gereksinimler: Python 3.12+, PostgreSQL 16+. Tarayıcı testleri için Playwright + Chromium.

## Belgeler

| Belge | Konu |
| --- | --- |
| [CLAUDE.md](CLAUDE.md) | Claude Code yönergesi: değişmez kurallar, komutlar, tamamlanma tanımı |
| [docs/01-mimari.md](docs/01-mimari.md) | Katmanlar, veri akışı, teknoloji yığını |
| [docs/02-veri-modeli.md](docs/02-veri-modeli.md) | ServiceNow uyumu, hibrit şema, yüksek hacimli tablolar |
| [docs/03-kimlik-ve-uzlastirma.md](docs/03-kimlik-ve-uzlastirma.md) | Normalizasyon, IRE kuralları, skor, çelişki, devre kesici |
| [docs/04-kaynaklar.md](docs/04-kaynaklar.md) | Kaynak sistemler ve tuzakları |
| [docs/05-servicenow-gecisi.md](docs/05-servicenow-gecisi.md) | Ana kaynak / replika deseni |
| [docs/06-guvenlik.md](docs/06-guvenlik.md) | Tehdit modeli (STRIDE), denetimler, standart eşlemesi |
| [docs/07-yetkilendirme.md](docs/07-yetkilendirme.md) | Rol–yetki matrisi, SoD |
| [docs/08-api-ve-entegrasyon.md](docs/08-api-ve-entegrasyon.md) | REST, ServiceNow uyumlu API, olaylar, KS GRC |
| [docs/09-mcp-sozlesmesi.md](docs/09-mcp-sozlesmesi.md) | Kurum MCP uygulaması ve Evren MCP sunucusu |
| [docs/10-arayuz-ilkeleri.md](docs/10-arayuz-ilkeleri.md) | UX ilkeleri ve ekranlar |
| [docs/11-yapay-zeka.md](docs/11-yapay-zeka.md) | Yapay zekâ kullanımı ve ISO/IEC 42001 yönetişimi |
| [docs/12-operasyon-ve-olcek.md](docs/12-operasyon-ve-olcek.md) | Hacim, kapasite, HA/DR, gözlemlenebilirlik |
| [docs/adr/](docs/adr/README.md) | Mimari karar kayıtları |

## Repo yapısı

Ayrıntılı dosya haritası [CLAUDE.md](CLAUDE.md#dosya-haritası) içinde.
