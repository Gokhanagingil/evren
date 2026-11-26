# ADR-0026: Elle girdi — CI oluşturma, ilişki ve CSV içe aktarma istek üzerinden, akıl katmanından geçerek

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U6, F1-21), mimari, güvenlik

## Bağlam

Bugün insan yalnızca var olan bir CI'ı düzenleyebiliyor (ADR-0012), yanlış birleştirmeyi ayırabiliyor (ADR-0011) ve kuyrukta karar verebiliyor. Kurumda keşif araçlarının görmediği CI'lar da var: iş uygulamaları, hizmetler, sözleşmeli cihazlar, keşif ağının dışında kalan sunucular. Bunları ve aralarındaki ilişkileri insanlar girer; ilk geçişte de çoğu zaman bir tablo (CSV) olarak gelir.

Elle girdinin üç tehlikesi var:

1. **Mükerrer CI:** kişi var olan bir sunucuyu yeniden açar. Ya da keşif sonradan aynı sunucuyu bulur ve elle açılanı tanımaz.
2. **Kural ihlali:** elle girilen değer golden record'a doğrudan yazılırsa uzlaştırma, rol önceliği ve denetim izi atlanır (kural 1).
3. **Toplu hata:** CSV'deki bir sütun kayması yüzlerce CI'ı bozar.

## Karar

1. **Her elle girdi bir istektir, uygulayan uzlaştırıcıdır (kural 1).**
   - API yalnızca istek tablosuna yazar: `ingest.manual_create` (CI oluşturma; CSV satırları da), `ingest.manual_relation` (ilişki ekleme ve kaldırma; U6.2).
   - Uzlaştırıcı istekleri PostgreSQL bildirimiyle hemen alır ve her birini kendi işleminde uygular (ADR-0013).
   - Sonuç isteğin üzerine yazılır. Arayüz bekleyen isteği ve sonucunu gösterir.

2. **CI oluşturma kimlik çözümlemesinden geçer.** İstekteki öznitelikler `Manual` kaynak kaydı olarak çözümleyiciye girer. Bunlar seri, FQDN, IP, MAC, BIOS UUID ve sınıfa özgü anahtarlardır.
   - **Eşleşme yok (`new`):** CI açılır. Ardından CI başına tek `Manual` kaynak kaydı (`source_key` = CI sys_id, ADR-0012) ve uzlaştırma gelir.
   - **Kesin eşleşme (`rule_match`, ör. aynı seri):** CI açılmaz, sonuç `duplicate` olur ve var olan CI gösterilir. Kişi o CI'ı düzenleyebilir. Zorlama yoktur: tanımlama kuralının eşleştirdiği kimlikle ikinci CI açmak kural 7'nin tersidir.
   - **Belirsiz eşleşme (`score_match`, `review`):** CI açılmaz, sonuç `needs_decision` olur. Adaylar ve skorlar gösterilir. Kişi gerekçe yazarak "yine de yeni CI aç" diyebilir (`force_new`). Karar ve gerekçe istekte ve denetim izinde kalır.
   - **Kimlik anahtarı yok (`insufficient`, ör. yalnızca adı olan uygulama):** CI açılır. Sonuçta "keşif bu CI'ı tanıyamaz" uyarısı yer alır.

3. **Elle açılan CI'ın kimlik anahtarları dizine girer.** Bu, ADR-0012 §5'e oluşturma anında tanınan istisnadır.
   - Aksi hâlde keşif aynı sunucuyu sonradan bulduğunda tanımaz ve mükerrer açardı. Anahtarlar dizindeyse keşif kaydı elle açılan CI'a bağlanır.
   - Sonraki elle düzenlemeler kimlik anahtarı eklemez; ADR-0012 değişmez.
   - Yazım hatalı bir seri ile açılan CI, keşfin gerçek serisiyle eşleşmez. Bu durum mükerrer adayı olarak görünür. Sınır yazılıdır.

4. **Değerler rol önceliğine uyar.**
   - Elle girilen değer yönetişim alanlarında geçerlidir (sahiplik, destek grubu, kullanım amacı).
   - Keşif sahipli alanlarda (ad, IP, işletim sistemi, seri) keşif gelene kadar geçerlidir, sonra yedek kalır.
   - Doğrulama elle düzenlemeyle aynıdır: alan adı yalnızca sözlükten, tip, seçim listesi, uzunluk, referans varlığı ve alan hassasiyeti (ADR-0012 §6).
   - `name` zorunludur. Sınıf sözlükte tanımlı bir CMDB sınıfı olmalı ve kişinin o sınıfta `create` izni olmalıdır (sınıf kalıtımıyla).

5. **İlişki (U6.2):** elle ilişki CI'ın `Manual` kaynak kaydındaki ilişki listesinde durur.
   - İzinli ilişki kuralları (ADR-0019) elle girişte zorunludur: izin verilmeyen tip veya yön API'de reddedilir. Keşif kaynakları için zorlama kademeli kalır.
   - Kaldırma yalnızca `Manual` kaynaklı ilişkiye uygulanır. Keşfin yazdığı ilişki elle silinmez; kaynağından kalkar (`cmdb_rel_ci/delete` izni açıklamasıyla aynı).

6. **CSV içe aktarma (U6.3):** iki aşamalıdır, önizleme ve onay.
   - **Önizleme:** API dosyayı ayrıştırır. Ayraç (virgül, noktalı virgül, sekme) kendiliğinden bulunur, UTF-8 (BOM'lu da) okunur. Başlıklar sözlükten çözülür; satır başına doğrulama hatası döner. Hiçbir şey yazılmaz.
   - **Onay:** geçerli satırlar bir toplu iş (`batch`) altında `manual_create` isteği olur. Uzlaştırıcı her satırı tek CI oluşturma gibi işler; satır sonucu `created`, `duplicate`, `needs_decision` veya `rejected` olur. CSV ile var olan CI güncellenmez; güncelleme düzenleme yoluyladır, mükerrer satır var olan CI'ı gösterir.
   - **Sınırlar:** en çok 5000 satır ve 2 MB.
   - **Formül enjeksiyonu:** `=`, `+`, `@` ile ya da sekme veya satır başıyla başlayan hücreler reddedilir. Bu değerler sonradan bir elektronik tabloya aktarılırsa çalıştırılmamalıdır.

7. **Sahiplik onayı (U6.4, F1-17)** ayrı bir adımdır. Sahiplik onayı alanları elle düzenleme ve oluşturma formunda yer almaz; yalnızca onay akışıyla yazılır (bugünkü `SYSTEM_MANAGED`).

## Sonuçlar

- Elle girdi keşifle aynı akıl katmanından geçer: mükerrer CI açılmaz, değerler rol önceliğine uyar, her şeyin kim tarafından ve neden girildiği denetim izindedir.
- Oluşturma asenkrondur (uzlaştırıcı genellikle saniyeler içinde uygular). Arayüz sonucu bekler ve gösterir.
- Kişi, keşfin göremediği CI'ı açabilir ama keşfin gördüğü bir CI'ın kopyasını açamaz.
- `force_new` gerekçeli bir insan kararıdır; mükerrer sağlık metriğinde ve gölge mod raporunda görünür.

## Alternatifler

- **API'nin CI'ı doğrudan açması:** kural 1'e aykırı ve mükerrer denetimi atlanır. Reddedildi.
- **Elle açılan CI'a kimlik anahtarı vermemek (ADR-0012 §5'i aynen uygulamak):** keşif aynı sunucuyu bulunca mükerrer açar. Reddedildi; anahtarlar yalnızca oluşturma anında dizine girer.
- **CSV'yi doğrudan uygulamak (önizlemesiz):** sütun kayması toplu bozulma yaratır. Reddedildi.
- **CSV ile güncelleme (upsert):** keşif sahipli alanlarda yedek değer seline ve yanlış eşleşmede toplu ezmeye yol açar. İlk sürümde reddedildi; gerekirse ayrı karar.
