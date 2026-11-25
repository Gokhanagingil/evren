# ADR-0012: Elle düzenleme istek üzerinden, Manual kaynak kaydı olarak

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

CLAUDE.md kural 1: API ve arayüzdeki elle düzenlemeler `Manual` kaynak kaydı olarak yazılır ve uzlaştırmadan geçer. İlk tasarımda (0003) API'ye `ingest.source_record` üzerinde yazma yetkisi verilmişti. Bu, API'nin herhangi bir kaynak adına kayıt yazabilmesi demekti ve uzlaştırıcıyı tetikleyen bir mekanizma yoktu.

## Karar

1. **İstek tablosu:** API `ingest.manual_edit`'e istek yazar (yalnızca istek sütunlarına `INSERT`); `source_record` yazma yetkisi API'den geri alındı (göç 0008). Uzlaştırıcı `apply_manual_edits` ile uygular.
2. **CI başına tek Manual kaydı:** `source_key` = CI sys_id. İstekteki değerler bu kayda eklenir; `null` o alandaki elle değeri kaldırır. Uygulamanın sahibi (bağın ve değişimin `actor`'ü) isteği yapan kişidir; denetimde neden `manual`.
3. **Rol önceliği geçerlidir:** yönetişim alanlarında (Manual en öncelikli) elle değer geçerli olur; keşif sahipli alanlarda yalnızca yedektir. Sonuç alan başına isteğe yazılır (`applied`, `overridden` + kazanan kaynak, `cleared`) ve arayüzde gösterilir. Manual'ın yetkili olmadığı alanlar API'de 422 ile reddedilir.
4. **Kaldırılan elle değer sahipsiz kalmaz:** önceki kazananı Manual olan ve artık hiçbir kaynağın bildirmediği alan temizlenir (ADR-0011'deki kuralın aynısı).
5. **Kimlik dizini:** Manual kayıt kimlik anahtarı eklemez; elle yazım hatası (yanlış seri) başka kayıtları bu CI'a eşleyemez.
6. **Doğrulama:** alan adları yalnızca sözlükten çözülür; tip, seçim listesi, uzunluk ve referans varlığı API'de denetlenir; geçersiz alan adı yanıta yansıtılmaz. Alan hassasiyeti yazma izniyle sınırlanır.

## Sonuçlar

- Elle düzenlemenin etkisi uzlaştırıcı çalıştığında görünür (asenkron); arayüz bekleyen isteği ve alan başına sonucu gösterir.
- ABAC koşullu izinler (ör. "CI sahibi yalnızca kendi grubunun CI'ları") koşul değerlendirmesi gelene kadar verilmez; şimdilik yalnızca Konfigürasyon analisti düzenler.
- İlişki düzenleme (Manual kaynaklı ilişki ekleme/silme) ayrı adım.
