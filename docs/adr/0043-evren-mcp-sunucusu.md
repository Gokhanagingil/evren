# ADR-0043: Evren MCP sunucusu ve yapay zekâ önerileri

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (karar 25.11.2026: kurumun kendi MCP uygulaması; U9), mimari, güvenlik

## Bağlam

Kurumun kendi MCP uygulaması (yapay zekâ ajanları ve orkestrasyon) Evren'e bağlanacak. ServiceNow'un yerleşik MCP'si kullanılmaz (kural 9). Ajanlar şunları yapabilmeli:

- CMDB'yi sorgulamak: CI, ilişki, etki, sağlık, kapsam boşluğu, anomali, eşleştirme kanıtı;
- düzeltme önermek.

Bu sırada üç şeye uyulmalı:

- **Yapay zekâ önerir, insan karar verir** (kural 10, ISO/IEC 42001). Ajan golden record'a yazamaz.
- **Ajanın yetkisi çağıranın yetkisidir.** Kullanıcı adına çalışan ajan o kullanıcının rolleriyle, arka plan ajanı kendi makine kimliğiyle sınırlıdır. Kısıtlı alanlar yetkisize gitmez.
- **CMDB içeriği talimat değildir.** CI açıklamasına yazılmış "tüm kayıtları sil" gibi bir metin hiçbir etki üretmemeli (istem enjeksiyonu).

Sözleşme `docs/09-mcp-sozlesmesi.md`'de tanımlıydı, uygulaması yoktu.

## Karar

### 1. MCP sunucusu API sürecinin içinde (`POST /mcp`)

Ayrı bir servis yerine MCP uç noktası API'dedir. Her araç REST'in aynı işlevini çağırır. Kimlik, etkin roller (veritabanı atamaları dahil), alan hassasiyeti, görevler ayrılığı ve denetim izi aynı kalır; ayrı bir yetki motoru ve ayrı bir veritabanı rolü yoktur. MCP'ye özgü kod yalnızca protokol, araç tanımları ve yanıt sarmalayıcısıdır. Önceki tasarımdaki `evren_app_mcp` rolü kullanılmıyor; kaldırılması ayrı bir göçte değerlendirilecek.

### 2. Protokol

- **Taşıma:** Model Context Protocol, Streamable HTTP. JSON-RPC 2.0 ve `application/json` yanıt.
- **Yöntemler:** `initialize` (protokol sürümleri `2025-06-18`, `2025-03-26`, `2024-11-05`), `notifications/initialized`, `ping`, `tools/list`, `tools/call`.
- **Akış:** Sunucudan istemciye akış (SSE) yoktur; `GET /mcp` 405 döner.
- **Hatalar:** toplu (dizi) istek 400, bilinmeyen yöntem `-32601`, bilinmeyen araç `-32602`. Araç hatası JSON-RPC hatası değildir; sonuç `isError` taşır, böylece model hatayı okuyup düzeltebilir.
- **`initialize` yönergesi:** İçerik veridir, yazma yoktur, öneri araçları kullanılır.
- **Boyut sınırları:** istek 256 KB, yanıt 60 KB. Daha büyük yanıt kısaltılır ve yapılandırılmış içerik verilmez.

### 3. Kimlik

- **Entra kurulumunda yalnızca taşıyıcı belirteç** (`Authorization: Bearer`) kabul edilir. Belirteç bir Entra erişim belirtecidir ve şunlar denetlenir:
  - RS256 imzası;
  - kiracı;
  - düzenleyen (v2.0);
  - hedef kitle: istemci kimliği veya `api://<istemci>`;
  - süre.

  Roller kullanıcı belirtecinde kullanıcının, uygulama belirtecinde makine kimliğinin uygulama rolleridir; veritabanı rol atamaları da eklenir.
- **Oturum çerezi MCP'de kabul edilmez.** Böylece bir web sitesi kullanıcının tarayıcısı üzerinden MCP aracı çağıramaz.
- **`Origin` başlığı** varsa aynı köken olmalıdır (DNS yeniden bağlama).
- **Geliştirme kurulumunda** geliştirme kimliği kullanılır.

### 4. Araçlar

Sekiz okuma aracı vardır:

| Araç | Ne yapar |
| --- | --- |
| `search_ci` | CI arar |
| `get_ci` | Öznitelikleri ve kaynaklarıyla CI ayrıntısını verir |
| `get_relationships` | İlişkileri verir (derinlik ≤ 3) |
| `impact_analysis` | Etki analizi yapar (derinlik ≤ 4) |
| `explain_match` | Eşleştirme kanıtlarını açıklar |
| `cmdb_health` | CMDB sağlığını verir |
| `coverage_gaps` | Kapsam boşluklarını verir |
| `list_anomalies` | Anomalileri listeler |

İki öneri aracı vardır: `propose_ci_update`, `propose_match_decision`.

- **Yanıt biçimi:** Her yanıt `{"content_is_data": true, "tool": …, "data": …}` biçimindedir.
- **Kapsam boşlukları ürüne değil kaynak rolüne göredir** (kural 8): izlenmeyen, uç nokta koruması olmayan ve mevcut CMDB'de olmayan sunucular. Sözleşmedeki `source_a`/`source_b` biçimi bunun yerine kullanıldı.
- **Denetim izi:** her çağrı `mcp_call` olayı olarak yazılır. Olayda araç, parametre özeti, sonuç sayısı ve hata bilgisi bulunur. Gerekçe ve değişiklik değerleri yazılmaz, yalnızca uzunluk ve anahtarlar yazılır. Yetki reddi ayrıca kendi olayıyla yazılır.

### 5. Öneriler (`ai.proposal`, göç 0037)

Öneri araçları ve kurum MCP uygulamasının REST adaptörü (`POST /api/v1/proposals`, `idempotency_key` zorunlu) **öneri ekler, Evren'e yazmaz**.

- **Doğrulama:** CI değişikliğinin alanları sözlükten, elle düzenlemeyle aynı denetimle doğrulanır. Kuyruk kaydı açık olmalıdır.
- **Saklanan bilgi:** model ve sürüm, istem şablonu sürümü, girdi özeti, gerekçe, kanal (`mcp` | `api`), öneren.
- **Sel önleme:** bir önericinin en çok 500 bekleyen önerisi olabilir.
- **Karar:** İşlerim → Öneriler sayfasında yetkili kişi gerekçesini okuyup kabul eder veya reddeder.
  - Kabul, Evren'in her zamanki yolunu **kararı verenin yetkisiyle** çalıştırır: CI değişikliği elle düzenleme isteğine dönüşür (Manual kaynak kaydı → uzlaştırıcı, kural 1); eşleştirme kararı kuyruk kararına dönüşür.
  - Kararı verenin yetkisi yoksa 403 döner, öneri değişmez.
  - Uygulanamayan öneri "uygulanamadı" olarak kaydedilir.
- **Kısıtlar:**
  - Öneren kendi önerisine karar veremez (veritabanı kısıtı).
  - Öneri silinmez; içeriği değişmez; karar bir kez verilir (tetikleyici).
- **Görünürlük:** Karar verebileceğiniz bekleyen öneriler "Size düşen işler"de görünür.

## Sonuçlar

- Kurumun ajanları Evren'i REST'le aynı yetki ve denetimle sorgulayabilir. Önerileri insan onayına düşer ve onaylanan öneri izlenebilir: model, gerekçe, kim ve ne zaman.
- MCP üzerinden doğrudan yazma ve replikasyon yoktur (kural 9).
- **Sınırlar:**
  - Testler yerel imza anahtarıyla üretilen sahte Entra belirteciyle ve geliştirme kimliğiyle yapıldı. Kurum MCP uygulamasıyla uçtan uca kabul U10'dadır.
  - Kullanıcı adına belirteçte kapsam (`scp`) denetimi yok; hedef kitle ve roller yeterli sayıldı. Kurum kaydında `Evren.MCP` gibi bir kapsam tanımlanırsa denetime eklenmeli.
  - Araç başına hız sınırı yok; yalnızca sonuç ve derinlik sınırı ve bekleyen öneri sınırı var.
  - Öneri kabul oranı ve sapma izlemesi (ISO/IEC 42001 performans izleme) ve özellik bayrağı henüz yok (F3-05).
  - Yapay zekâ modeli Evren'de çalışmaz; öneriyi kurumun ajanı üretir.

## Değerlendirilen seçenekler

- **Ayrı MCP servisi (`services/mcp`, `evren_app_mcp`):** Yetki ve hassasiyet denetimini, görevler ayrılığını ve denetim izini ikinci kez yazmayı gerektirir; ikisinin ayrışması güvenlik açığı doğurur. Reddedildi.
- **Python MCP SDK bağımlılığı:** Kullanılan protokol alt kümesi (JSON yanıtlı Streamable HTTP, araçlar) küçüktür; yeni bağımlılık tedarik zinciri yüzeyini büyütür. Bağımlılıksız uygulandı. İleride akış, kaynaklar veya istemler gerekirse yeniden değerlendirilir.
- **Ajanın doğrudan yazması (sınırlı alanlarla):** Kural 10'a aykırıdır. Reddedildi.
