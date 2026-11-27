# ADR-0037: Kalite ve uyum kuralları, bulgular ve CMDB sağlık panosu

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (istek 27.11.2026; U8.6, F1-09, F1-18, F1-26), mimari, güvenlik

## Bağlam

Proje sahibinin örneği şuydu: hiçbir CI ile (uygulama sunucusu, uygulama vb.) ilişki kurulmamış bir veritabanı tespit edildiğinde, veritabanının sahibine görev atanabilmeli. Proje sahibi örneğin genelleştirilmesini istedi.

Genel ihtiyaç şudur:

- CMDB'de olmaması gereken bir durumu tanımlayan bir **kural**,
- kuralın bulduğu her CI için bir **bulgu**,
- bulgunun sorumlusuna bir **görev**,
- düzeltilince bulgu ve görevin kendiliğinden kapanması.

Aynı zemin plandaki iki maddeyi de karşılar:

- **CMDB sağlık panosu (F1-09):** tamlık, doğruluk, uyum, kapsam. Puanlama çekirdeği vardı (`evren_core.health`, `metadata/health_rules.yaml`).
- **Uyum denetimi (F1-18):** beklenen ile gerçeğin karşılaştırılması; kaynaktan gelen izinsiz ilişkinin raporlanması dahil.

Görev altyapısı (ADR-0035) ve kampanyalar (ADR-0036) hazırdı.

## Karar

### 1. Kural türleri sabit bir katalogdan, tanım panelden (`insight.quality_rule`)

Kural, kapsamdaki CI'lardan hangilerinin bulgu olduğunu söyler. Kapsam sınıf ve koşulla yazılır, kampanyadaki gibi: alanlar sözlükten çözülür, değerler parametre olarak geçer, emekli CI kapsama girmez.

Türler (`evren_core.quality.CHECK_KINDS`):

| Tür | Bulgu ne zaman? | Parametreler |
|---|---|---|
| `no_relation` | Seçilen sınıflardan hiçbir CI ile ilişkisi yok | `related_classes`, `exclude_classes`, `rel_types` |
| `missing_fields` | Seçilen alanlardan biri boş | `fields` (sözlükten) |
| `not_seen_by_role` | Bu roldeki hiçbir veri kaynağında yok | `role` (kaynak rolü; ürün adı değil) |
| `matches_condition` | Kapsamdaki CI ek koşula da uyuyor | `condition` (encoded query) |
| `relation_not_allowed` | Ebeveyni olduğu bir ilişki izinli ilişki kurallarına uymuyor (tip izinsiz veya yön ters) | yok |

Notlar:

- Parametreler doğrulanır. Bilinmeyen sınıf, alan, rol veya ilişki tipi Türkçe nedenle reddedilir.
- Serbest SQL yoktur.
- Kural ürün adı içermez. Örneğin "izlenmeyen sunucu" kuralı izleme ürününe değil izleme rolüne bağlıdır (ADR-0009).
- Kuralın üç niteliği daha vardır:
  - sağlık boyutu: tamlık, doğruluk, uyum veya kapsam;
  - önem;
  - değerlendirme sıklığı: saatte bir, altı saatte bir, günde bir veya haftada bir.
- Kural görev açıp açmayacağını, görev başlığını, yönergeyi ve süreyi de taşır.
- Panelde beş şablon sunulur: ilişkisiz veritabanı, sahibi veya destek grubu boş üretim CI'ı, izlenmeyen üretim sunucusu, uç nokta koruması olmayan sunucu, izinli olmayan ilişki. Şablon seçilince alanlar dolar, kullanıcı değiştirebilir.

Kural yaşam döngüsü kampanyayla aynıdır:

- taslak → etkin ↔ duraklatıldı → emekli;
- durum değişikliği gerekçe ister;
- yalnızca taslak veya duraklatılmış kural düzenlenir.

Etkinleştirilen kural birkaç saniye içinde değerlendirilir. Kaydetmeden önce önizleme gösterilir: kaç bulgu çıkacağı, görevlerin sahibine, yönetenine, grubuna gideceği veya atanmamış kalacağı, örnek CI'lar ve nedenleri.

### 2. Bulgu: kural × CI (`insight.finding`)

Bir bulgu üç durumdan birindedir:

- **Açık:** kural CI'ı buluyor.
- **İstisna:** durum bilinçli olarak kabul edildi.
- **Giderildi:** kural artık CI'ı bulmuyor.

Bir kural ve CI için açık veya istisnalı bulgu tektir (kısmi tekil dizin). Giderilen bulgu geçmişte kalır, yeniden açılmaz; durum tekrar ederse yeni bulgu açılır.

Bulgu, nedenin Türkçe açıklamasını taşır. Örnekler: "Boş alan: Sahibi", "Uygulama, bilgisayar, iş uygulaması veya servis ile ilişkisi yok", "İzinli olmayan ilişki: Runs on::Runs → uyg-1 (yön ters)".

**İstisna:**

- Geçerli bir süre ve kimin verdiği olmadan istisna olmaz (veritabanı kısıtı ve tetikleyici).
- Süre dolunca bulgu yeniden açılır.

### 3. Görev çalışanı değerlendirir

Görev çalışanının her turu şunları yapar:

1. Süresi dolan istisnaları yeniden açar.
2. Zamanı gelen etkin kuralları ve "şimdi değerlendir" isteklerini değerlendirir:
   - eşleşen CI'a bulgu açar veya bulguyu günceller;
   - artık eşleşmeyen bulguyu kapatır;
   - açık bulgunun sorumlusuna tek görev açar.
3. Görevi açarken ADR-0035'teki sorumlu sırasını izler. Tekillik anahtarı `rule:<kural>:<ci>`, `source_ref` ise bulgudur. Görev açma kodu kampanyalarla ortaktır.

Değerlendirme şu durumlarda hiçbir şeyi değiştirmez:

- **Kaynak sağlıksızsa:** kural bir kaynak rolüne bağlıysa (`not_seen_by_role`) ve o rolün etkin kaynaklarından birinin son çalışması tamamlanmamış veya devre kesici OK değilse, değerlendirme "sonuç yok" olur. Sağlıksız bir kaynak yüzünden yüzlerce yanlış bulgu açılmaz (kural 4'ün ruhu).
- **Sınır aşılırsa:** bulgu sayısı kuralın sınırını aşarsa değerlendirme "başarısız" olur.

### 4. Görev kararı bulguya döner, bulgu görevi kapatır (tetikleyiciler)

| Olay | Sonuç |
|---|---|
| Bulgu giderildi | Açık görev "Bulgu giderildi" (`resolved`) ile kapanır; ServiceNow kaydı da kapatılır |
| Sorumlu "Geçerli değil" dedi | Bulgu, kuralın istisna süresi kadar istisna olur |
| Sorumlu "Reddedildi" (sorumlusu ben değilim) dedi | Bulgu ayıklamaya düşer: CI'ın sorumlusu değişene kadar yeni görev açılmaz; Konfigürasyon yöneticisinin "Size düşen işler" listesine girer |
| Sorumlu "Düzeltildi" dedi | Kural bir sonraki değerlendirmede doğrular; bulgu sürüyorsa yeni görev açılır |

Görev çalışanına karar yetkisi verilmez. Tetikleyiciler `SECURITY DEFINER` ve dar kapsamlıdır.

Konfigürasyon yöneticisi panelden de istisna verebilir. Bu, gerekçe ve süre ister; açık görev iptal edilir. İstisnayı kaldırabilir veya ayıklamayı bitirebilir. Her işlem denetim izine yazılır.

### 5. Sağlık ölçümü arka planda, pano okur (`insight.health_snapshot`)

Görev çalışanı CMDB sağlığını saatte bir ölçer; bir kural değerlendirildiğinde hemen ölçer. Ölçümler şunlardır:

| Boyut | Ölçümler |
|---|---|
| Tamlık | Sınıf başına zorunlu ve önerilen alanlar (`health_rules.yaml`) |
| Doğruluk | Açık eskime önerisi olmayan CI'lar |
| Uyum | Üretim sunucusunda yönetişim alanları |
| Kapsam | Sunucuların keşif, izleme, uç nokta koruması ve mevcut CMDB rollerince görülme oranı; rolünde etkin kaynak olmayan ölçülmez |

Her etkin kural da kendi boyutuna bir ölçüm ekler: kapsamdaki CI'lardan açık bulgusu olmayanların oranı. İstisna, kabul edilmiş risk olarak iyi sayılır.

Puanlar `evren_core.health` ile hesaplanır: boyut puanı ölçümlerin ortalaması, genel puan boyutların ağırlıklı ortalamasıdır. Pano son ölçümü ve son 30 günün günlük eğilimini okur. Ağır sorgular sayfa açılışında değil arka planda çalışır; bu, milyonlarca CI'da da ölçeklenir.

### 6. Yetki

| Kim | Ne yapabilir | Ne yapamaz |
|---|---|---|
| Konfigürasyon yöneticisi | Kural tanımlar ve değiştirir, istisna verir ve kaldırır | — |
| CI okuma izni olan herkes | Sağlık panosunu, kuralları ve bulguları görür; okuyamadığı sınıftaki CI'ın bulgusunu görmez | — |
| API | Kural tanımı ve durumu, çalışma isteği, istisna sütunları yazar | Bulgu açamaz, kapatamaz, silemez; ölçüm yazamaz |
| Görev çalışanı | Bulgu ve ölçüm yazar | Kural tanımlayamaz, istisna veremez, karar veremez |

Salt okur rol bulguları görmez. Bunların hepsi veritabanı sütun yetkileriyle zorlanır ve testlidir.

## Sonuçlar

- Proje sahibinin örneği ilk şablondur. Aynı altyapı sahipsiz üretim CI'ını, izlenmeyen sunucuyu, korumasız sunucuyu ve izinsiz ilişkiyi de kapsar; yeni bir bulgu türü katalog maddesiyle eklenir.
- Kaynaktan gelen izinsiz ilişki atılmaz (keşfin gördüğü gerçek), bulgu olarak raporlanır. Bu, F1-18'in ilk adımıdır.
- GRC kontrolleri CMDB-01…06 bugün sabit sorgularla çalışır. İleride kurallardan beslenebilir; bu ayrı bir karardır.
- Yönetim panelinde "Kuraldan görev aç", "CMDB sağlık panosu" ve "Bulguları izle" açıldı. CI sayfasında "Kalite bulguları" bölümü var.

## Bilinen sınırlar

- Doğruluk boyutu yalnızca eskime ve kurallarla ölçülüyor. Mükerrer adayı ve kopuk referans ölçümleri sonra eklenecek.
- Uyum boyutunda sahiplik onayı oranı ve GRC kontrol sonuçları henüz puana girmiyor.
- Görsel koşul oluşturucu yok; koşullar ServiceNow sözdizimiyle yazılıyor, önizleme ve hata mesajı yol gösteriyor.
- Eğilim günde tek noktadır; trend anomalisi (F3-04) yok.
- `relation_not_allowed` adayları 50 000 CI ile sınırlıdır; daha büyük kapsam daraltılmalıdır.
- Bütün testler sentetik veriyle yapıldı.
