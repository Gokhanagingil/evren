# 11 · Yapay zekâ

## Kullanım alanları

| Alan | Ne yapar | Faz | Sınır |
| --- | --- | --- | --- |
| Eşleşme açıklama ve öneri | Kuyruktaki belirsiz eşleşmeler için kanıtları özetler, karar önerir | F3 | Öneri; analist karar verir |
| Doğal dil sorgu | "İstanbul DC'de Zabbix'te izlenmeyen Windows 2016 sunucular" → encoded query | F3 | Üretilen sorgu kullanıcıya gösterilir, sonra çalışır |
| Anomali açıklama | Kaynak hacmi düşüşü, toplu değişim, yetkisiz değişiklik için olası nedenler ve ilgili Change'ler | F3 | Bilgi amaçlı |
| Veri kalitesi önerileri | Yanlış sınıf, eksik ilişki, olası sahip/destek grubu | F3 | Öneri; `propose_ci_update` |
| Etki analizi özeti | Change için etkilenen servislerin sade özeti | F3 | Kaynak ilişkiler gösterilir |

**Yapay zekâ olmadan da çalışan temel:** Eşleştirme ve anomali tespiti deterministik kurallar ve istatistikle (skor, MAD, OpenSearch RCF) çalışır. Yapay zekâ bunların üzerine açıklama ve öneri katmanıdır, yerine geçmez.

## Yönetişim (ISO/IEC 42001 ile uyumlu)

- **İnsan gözetimi:** Yapay zekâ çıktısı golden record'a doğrudan yazılmaz. Her öneri onay akışından geçer ve karar verenin kimliği kaydedilir.
- **İzlenebilirlik:** Her öneride şunlar saklanır:
  - model ve sürüm;
  - istem şablonu sürümü;
  - girdi özeti (hangi CI'lar ve alanlar);
  - çıktı ve gerekçe;
  - kabul/ret sonucu.
  Kayıt yerleri: `ingest.match_candidate.ai_suggestion`, `insight.anomaly.explanation_model`.
- **Performans izleme:** öneri kabul oranı, yanlış öneri oranı ve sapma zaman serisi olarak izlenir. Eşik aşılırsa özellik kapatılabilir (özellik bayrağı).
- **Veri koruma:**
  - Modele yalnızca görev için gereken alanlar gönderilir.
  - Kısıtlı alanlar (güvenlik duruşu) ve kişisel veriler varsayılan olarak gönderilmez.
  - Model kurum onaylı olmalı (kurum içi veya veri yerleşimi sözleşmeli).
- **Güvenlik:**
  - CMDB içeriği istem içinde veri olarak işaretlenir. İçerikteki talimatlar uygulanmaz (istem enjeksiyonu).
  - Araç yetkileri en az yetki ilkesine uyar; MCP sunucusunun yazma yetkisi yoktur.
- **Etki değerlendirmesi:** her yapay zekâ özelliği devreye alınmadan önce amaç, riskler, kontroller ve sorumlu kişi belgelenir (F3-05).

## Açık karar

Hangi model ve hangi veri yerleşimi? Seçenekler: kurum içi barındırılan model ya da sözleşmeli bulut model. F3 öncesi bilgi güvenliği ve hukuk birimleriyle karara bağlanır.
