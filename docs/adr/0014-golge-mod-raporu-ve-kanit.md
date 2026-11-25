# ADR-0014: Gölge mod raporu ve değiştirilemez kanıt anlık görüntüsü

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

F0'ın amacı ServiceNow'a yazmadan önce ölçmek, karşılaştırmak ve kanıtlamaktır. Proje sahibi ve CMDB sahibi, mevcut CMDB'nin (ITSM rolü; bugün ServiceNow) kapsam boşluklarını, mükerrerlerini ve değer farklarını görmeli; GRC Platformu bu bulguları denetim kanıtı olarak almalıdır (F2-08). Kanıtın sonradan değiştirilmediği gösterilebilmelidir.

## Karar

1. **Rapor canlı üretilir** (`GET /api/v1/reports/shadow`), rol tabanlıdır: ITSM, keşif (Discovery/Virtualization/Network), izleme ve uç nokta güvenliği rolleri `ingest.data_source.role`'dan okunur; ürün değişse rapor değişmez (ADR-0009).
2. **Mükerrer sınıflandırması kimlik kurallarına uyar** (kural 7): seri/UUID/sanal makine kimliği ortak → *kesin*; FQDN/MAC ortak → *güçlü* (seri farklıysa "eski kayıt olabilir" notuyla; ör. fizikselden sanala taşıma); yalnız ad ortak → *olası*; yalnız ad ortak ve donanım kimliği farklı → *çelişkili* (birleştirilmemeli). IP grup kurmaz. Keşfin görmediği kayıt emekliye ayırma adayı olarak önerilir. Öneriler ServiceNow'da veri düzeltmesidir; business rule/ACL değişikliği gerektirmez (kural 11).
3. **Değer farkları** uzlaştırma kaydındaki ezilen değerlerden (`winners[*].overridden`) okunur; büyük/küçük harf ve alan adı eki farkları "yalnız yazım" olarak ayrılır. Gösterilen öznitelikler okuma izninin hassasiyet sınırıyla süzülür.
4. **Kesinlik:** bağımlı rollerin son çalışması sağlıklı değilse (karantina veya sürüyor) rapor `inconclusive`, `provenance.collection_status = partial` (GRC sözleşmesi).
5. **Kanıt anlık görüntüsü:** `insight.evidence_snapshot` salt eklemedir (sahibi dahil güncelleme/silme tetikleyiciyle engellenir). İçerik kararlı JSON (sıralı anahtar) olarak SHA-256'yla saklanır; indirme aynı baytları ve `X-Evren-Content-SHA256` başlığını döner. Kaydetme izni `shadow_report/create`: Veri sorumlusu ve Denetçi.

## Değerlendirilen seçenekler

- **Raporu önceden hesaplanmış tablolara yazmak:** büyük envanterde hızlı; ancak şimdilik canlı SQL yeterli. Hacim büyüyünce (F1-09 sağlık panosu) özet tabloları eklenebilir.
- **Kanıtı dosya olarak dışarıda saklamak:** bütünlük ve erişim denetimi ayrı çözülmeli; veritabanında salt ekleme + özet daha basit ve denetlenebilir.

## Sonuçlar

- Sentetik veriyle doğrulandı. Gerçek rapor, ServiceNow ilk yüklemesi (F0-08) ve kaynak bağlantılarından sonra üretilir; değerlendirmesini CMDB sahibi yapar.
- Rapor demo modunda (statik sunucu) yoktur; veritabanı gerekir.
- GRC Platformu'nun kanıtı çekmesi makine hesabı (F0-09 devamı) ve F2-08 entegrasyonuyla tamamlanır.
