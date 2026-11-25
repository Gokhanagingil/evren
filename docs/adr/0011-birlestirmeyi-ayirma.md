# ADR-0011: Birleştirmeyi ayırma (split)

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

CLAUDE.md kural 3 birleştirmenin geri alınabilir olmasını ister. Yanlış birleştirmede (ör. bir kaynak başka sunucunun seri numarasını bildirdi) üç şey birlikte düzeltilmelidir: kimlik bağı, kimlik aday dizini ve golden record değerleri. Yalnızca bağı kapatmak yetmez: dizinde kalan anahtarlar kaydı sonraki çalışmada aynı CI'a yeniden eşler; CI'da kalan değerler başka bir varlığa aittir.

## Karar

1. **İstek ve uygulama ayrı:** API `ingest.split_request`'e istek yazar (yetki `cmdb_ci/split`; hedef CI seçildiyse hedef sınıfta `merge`). Uygulamayı uzlaştırıcı yapar (`ReconcilerStore.apply_splits`) ve koşulları yeniden doğrular. API'nin bağ veya CI yazma yetkisi yoktur.
2. **Bağ kalıcıdır:** kayıt yeni CI'a (veya seçilen CI'a) `split` yöntemiyle, isteyen kişinin adıyla bağlanır; çözümleyici bağlı kaydı yeniden eşleştirmez.
3. **Kimlik profili yeniden kurulur:** asıl CI'ın anahtarları kalan etkin kaynak kayıtlarından hesaplanır. Dizinde silme, yalnızca tek CI'ın anahtarlarını değiştiren `ingest.replace_identity_keys` işleviyle yapılır (`SECURITY DEFINER`, sabit `search_path`); uzlaştırıcının dizinde genel `DELETE` yetkisi yoktur.
4. **Değer temizleme kuralı:** asıl CI'da önceki kazananı ayrılan kaynak olan öznitelik, yeniden uzlaştırmada başka kaynak değer vermiyorsa temizlenir (denetimde neden `split`). Bu kural yalnızca ayırmada uygulanır; eskime ve kaynak kaybı devre kesicinin denetimindeki ayrı kurallardır.
5. **Ayrılamayanlar:** CI'ın tek kaynak kaydı; ServiceNow ilk yükleme kaydı (CI'ın sys_id'si o kayıttan gelir, replika eşlemesi bozulmasın diye diğer kayıtlar ayrılır); gölge/devir kaynağın kaydı hedef CI seçilmeden (yeni CI açamaz).
6. **İlişkiler:** ayrılan kaydın ilişkileri yeni CI için yeniden kurulur; asıl CI'daki eski ilişki satırları silinmez, eskimeye bırakılır (kural 4: yıkıcı işlemler kontrollü).

## Değerlendirilen seçenekler

- **API'nin bağı doğrudan kapatması:** daha hızlı geri bildirim; ancak kural 1'i ihlal eder ve kimlik dizinini tutarsız bırakır.
- **Dizinde kaynak başına anahtar tutmak** (anahtar → kaynak kaydı): ayırmada yalnızca ilgili satırlar silinir; ancak her alımda yazım hacmini artırır. Profil yeniden kurma, ayırma nadir olduğu için daha ucuzdur.
- **Değerleri temizlememek:** yanlış seri/UUID golden record'da kalır ve SN replikasına gider; reddedildi.

## Sonuçlar

- Ayırma uzlaştırıcı çalıştığında görünür (asenkron); arayüz bekleyen isteği ve sonucunu gösterir.
- Ayrılan kaydı başka bir mevcut CI'a taşımak API'de desteklenir (`target_ci`); arayüzde şimdilik yalnızca "yeni CI" seçeneği var.
- Sentetik senaryolarla test edildi; gerçek yanlış birleştirme örnekleriyle kabul F0-13 gölge mod raporlarında yapılacak.
