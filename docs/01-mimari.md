# 01 · Mimari genel bakış

> Görsel karşılığı uygulamadaki **Mimari Harita** sayfasıdır. Kaynağı: `apps/web/content/architecture.yaml`.

## Amaç ve kapsam

Evren, kurum içi ve on-prem çalışan bir konfigürasyon yönetim platformudur:

- Çok sayıda kaynaktan gelen gözlemleri tek bir ara katmanda anlamlandırır.
- ServiceNow veri deseniyle uyumlu bir CMDB oluşturur.
- Geçiş döneminde ServiceNow CMDB'yi bir replika olarak besler.

Hedef ölçek: 40.000 sunucu ve 100.000 ağ cihazı. Türev kayıtlarla birlikte 3–5 milyon CI ve 10–20 milyon ilişki.

## Katmanlar

| # | Katman | Bileşenler | Not |
| --- | --- | --- | --- |
| 01 | Kaynaklar ve alım | Bağlayıcılar + YAML eşleme, alım geçidi, Kafka, ham yük arşivi | Keşif aracı değişirse yalnızca bağlayıcı ve eşleme değişir |
| 02 | Anlamlandırma | Değişim süzgeci, normalizasyon, kimlik çözümleme, uzlaştırma, devre kesici, eşleştirme kuyruğu, yetkisiz değişiklik, trend/anomali | Ara katmanın asıl değeri |
| 03 | Kayıt sistemi | PostgreSQL golden record (hibrit şema), ilişkiler, metadata sözlüğü, kaynak bilgisi, denetim izi, outbox; OpenSearch dizini; Valkey | Arama dizini ve önbellek kayıt sistemi değildir |
| 04 | Tüketim | Arayüz, REST API, ServiceNow uyumlu API, olay akışı, Evren MCP sunucusu | Kurumun MCP uygulaması, KS GRC ve diğer uygulamalar |
| 05 | ServiceNow geçişi | sn-sync → IRE API → SN CMDB replika; değişiklik çekme; CI seçici | Task alanları ve business rule'lar değişmez |
| 06 | Güvenlik ve yönetişim | Entra ID, RBAC+ABAC, SoD, şema setleri, sır kasası, mTLS, DB rolleri, SIEM, tedarik zinciri, yapay zekâ yönetişimi | `docs/06`, `docs/07` |

## Temel tasarım kararları

1. **"Ara katman" tek bir ürün değildir; üç rol vardır:**
   - Olay omurgası (Kafka).
   - Anlamlandırma motoru (Evren uzlaştırıcısı).
   - Arama/analiz deposu (OpenSearch).
   Redis/Valkey yalnızca önbellek ve kilit içindir. → ADR-0002
2. **Kayıt sistemi PostgreSQL'dir; şema hibrittir.** `cmdb_ci` çekirdek alanları fiziksel kolondur, sınıfa özgü alanlar JSONB'dedir. Tip ve geçerliliği metadata sözlüğü belirler ve bir veritabanı tetikleyicisi zorlar. → ADR-0001
3. **Kimlik çözümleme ServiceNow IRE semantiğini korur ve genişletir:** olasılıksal skor, çelişki denetimi, geri alınabilir birleştirme. → ADR-0003
4. **Evren ana kaynak, ServiceNow CMDB replikadır.** Task alanları SN'deki CI'ları göstermeye devam eder. → ADR-0004
5. **Telemetri CMDB'ye girmez.** Metrik ve alarm kaynakta kalır; CMDB yalnızca konfigürasyon verisi ve özet sinyal alır. Değişmeyen kayıtlar içerik özetiyle elenir. → ADR-0002
6. **Golden record'a tek yazan uzlaştırıcıdır.** Elle düzenleme de Manual kaynağı olarak kurallardan geçer. → ADR-0008
7. **Arayüz derlemesizdir;** üçüncü taraf çalışma zamanı kodu yoktur ve CSP sıkıdır. → ADR-0006

## Veri akışı (bir kaydın yolculuğu)

```
vCenter API ──► bağlayıcı (sayfalama tamam) ──► eşleme (ServiceNow alan adları) ──► CloudEvents zarfı
    ──► alım geçidi (mTLS, şema, kaynak yetkisi) ──► Kafka ingest.record
    ──► uzlaştırıcı: içerik özeti değişmemiş mi? ── evet ──► yalnızca last_seen
                                           └─ hayır ─► normalizasyon ─► kimlik çözümleme
                                                ├─ kural/skor eşleşmesi ─► identity_link
                                                ├─ şüpheli ─► eşleştirme kuyruğu (insan)
                                                └─ yeni ─► yeni CI (sys_id)
    ──► uzlaştırma (öncelik, tazeleme) ──► cmdb_ci + kaynak bilgisi + denetim izi + outbox (tek işlem)
    ──► outbox ─► Kafka cmdb.ci.changed ─► OpenSearch dizini, webhook'lar, sn-sync (F2)
çalışma sonu ──► devre kesici ── OK ─► görülmeyen kayıtlar "missing", eskime önerileri
                               └─ WARN/OPEN ─► karantina + anomali kaydı
```

## Teknoloji yığını

| Alan | Seçim | Gerekçe |
| --- | --- | --- |
| Dil | Python 3.12 | KS GRC ve tedarik ile aynı; veri işleme ekosistemi |
| API | FastAPI + psycopg 3 + Pydantic v2 | Asenkron G/Ç, OpenAPI, tip doğrulama |
| Kayıt sistemi | PostgreSQL 16+ | Olgun, lisanssız; JSONB, bölümleme, rol tabanlı güvenlik |
| Olay omurgası | Apache Kafka (KRaft) | Yeniden oynatma, bölüm başına sıra, Apache 2.0 |
| Arama/analiz | OpenSearch | Bulanık arama, toplama, Apache 2.0 anomali tespiti (RCF) |
| Önbellek/kilit | Valkey | BSD lisanslı Redis çatalı |
| Kimlik | Entra ID (OIDC) | KS GRC ile ortak |
| Arayüz | ES modülleri + Web API'leri | Derleme yok, tedarik zinciri riski yok, CSP uyumlu |

## Ölçek özeti

Ayrıntı `docs/12-operasyon-ve-olcek.md`'de.

- Günlük gözlemlerin büyük kısmı değişmez ve süzgeçte elenir; uzlaştırmaya giren hacim %1–5 mertebesindedir.
- Yüksek hacimli tablolar bölümlenir: `ingest.source_record` kaynağa göre, `audit.ci_change` aya göre, `cmdb_sam_sw_install` hash ile.
- ServiceNow replikasına yalnızca süreç sınıfları gider; port ve yazılım kurulumu gibi kayıtlar gitmez.
