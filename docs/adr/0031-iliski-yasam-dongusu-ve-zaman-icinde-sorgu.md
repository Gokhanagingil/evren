# ADR-0031: İlişkiyi bildiren kaynak tutulur; bildirilmeyen ilişki kalkar; CI'ın geçmiş hâli değişim geçmişinden çıkarılır

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U7.4), mimari

## Bağlam

U7.4'ün konusu iki yetenekti: görsel ilişki haritası ve "bu CI üç ay önce nasıldı?" sorusu (F1-19). Hazırlıkta temel bir eksik çıktı.

**Kaynaktan gelen ilişki hiç kalkmıyordu.** Uzlaştırıcı kaynağın bildirdiği ilişkiyi `cmdb_rel_ci`'a ekliyor ya da `last_discovered` alanını güncelliyordu. Kaynak ilişkiyi bildirmeyi bıraktığında satır yerinde kalıyordu.

- Bir sunucu başka bir kümeye taşınsa, eski "Virtualized by" ilişkisi sonsuza dek dururdu.
- Etki analizi (ADR-0030) bu eski ilişkiler yüzünden yanlış hizmeti etkilenmiş gösterirdi.
- ADR-0030 "ServiceNow'da silinen ilişki tam çalışmada kalkar" diyordu. Bu cümle kodda karşılıksızdı ve düzeltildi.

Yalnızca elle girilen ilişki kaldırılabiliyordu. O da tek bir `discovery_source` sütununa bakıyordu: önce elle girilip sonra keşfin de bildirdiği bir ilişki, elle kaldırılınca keşfe rağmen siliniyordu.

**Zaman içinde sorgu için ilişkinin açılıp kalktığı an gerekir.** Öznitelik değişiklikleri `audit.ci_change`'ta vardı; kaynağın açtığı ilişkiler ise değişim geçmişine hiç yazılmıyordu.

## Karar

1. **İlişkiyi bildiren tutulur.** Yeni `cmdb.cmdb_rel_ci_source` tablosu (göç 0028) şu bilgileri saklar:
   - ilişki;
   - bildiren veri kaynağı ve kaynak kaydı (ilişki listesinde bu ilişkiyi taşıyan kayıt);
   - ilk ve son bildirim zamanı;
   - son çalışma.

   Aynı ilişkiyi iki kaynak, ya da aynı kaynağın iki ucu bildirebilir. Her bildirim ayrı bir satırdır. Tabloyu yalnızca uzlaştırıcı yazar.

2. **Bildirilmeyen ilişki kalkar.** Bu yıkıcı bir işlemdir ve şu koşulların hepsini ister:
   - Çalışma tam (snapshot), tamamlanmış ve devre kesicisi `OK` olmalı (kural 4).
   - Kaynak kaydı bu çalışmada gelmiş ama ilişkiyi artık listelemiyor olmalı. Ancak o zaman o kaydın bildirimi düşer.
   - Delta çalışmasında ilişki listesi eksik olabilir (ör. ServiceNow yalnızca güncellenenleri verir); bu yüzden delta hiçbir bildirimi düşürmez.
   - Görülmeyen kaydın ilişkileri korunur: ya kayıt geri döner ya da CI eskime kararıyla emekliye ayrılır (ADR-0028).

   Bildirimi kalmayan ilişki `cmdb_rel_ci`'dan silinir; CI'lar silinmez (kural 3). Çalışma notuna "biten ilişki: N" yazılır.

3. **Elle giriş de bir bildirimdir.** Elle eklenen ilişkinin bildireni `Manual` kaynağıdır.
   - Keşif bir ilişkiyi bırakınca, elle de girilmişse ilişki kalır.
   - Elle kaldırma yalnızca `Manual` bildirimini siler. Başka bir kaynak hâlâ bildiriyorsa satır kalır.

4. **Açılma ve kalkma değişim geçmişine yazılır.**
   - Uzlaştırıcı yeni bir ilişki satırı açtığında ya da kaldırdığında iki ucun `audit.ci_change` kaydına `relation` satırı ve bir CI olayı yazar. Alanlar şunlardır: eski veya yeni `{type, parent, child}` değeri, kaynak, neden, çalışma kimliği.
   - Satırı değiştirmeyen elle ekleme ya da kaldırma (başka bir kaynak da bildiriyordu) `effect: none` ile işaretlenir.

5. **Göç öncesi veri geriye dönük doldurulur.** Göç 0028 mevcut ilişkilerin bildirenlerini kaynak kayıtlarının ilişki listelerinden çıkarır; çözümleme uzlaştırıcınınkiyle aynıdır. Elle girilmiş ilişkilere `Manual` bildirimi eklenir. Demo verisinde bu doldurma, uzlaştırıcının yazdığıyla birebir aynı çıktı (29/29).

6. **Zaman içinde sorgu, golden record'un ikinci bir kopyasını tutmaz.** Bir andaki hâl bugünkü hâlden geri sarılarak bulunur (`evren_core/timeline.py`):
   - Öznitelik: o andan sonraki ilk değişikliğin eski değeridir; sonra hiç değişmediyse bugünkü değerdir.
   - İlişki: o andan önce açılmış bugünkü ilişkilerden başlanır. O andan sonraki ilişki olayları geri sarılır: sonradan açılan çıkar, sonradan kalkan geri gelir. `effect: none` olaylar atlanır.
   - CI o anda yoksa (oluşturulma tarihi sonra) bu açıkça söylenir.
   - Türetilen zaman damgaları (`first_discovered`, `last_discovered`) değişim geçmişine yazılmadığı için gösterilmez.

7. **API ve arayüz.**
   - `GET /api/v1/ci/{id}/as-of?at=` döndürür: o andaki öznitelikler ve bugünle farkları; o andaki ilişkiler; sonradan açılan ve kalkan ilişkiler.
   - `GET /api/v1/ci/{id}/timeline` değişiklikleri anlara toplar: aynı çalışmanın (`run_id`) ya da aynı elle isteğin değişiklikleri tek andır. Sayfalama bir anı ikiye bölmez.
   - `GET /api/v1/ci/{id}/graph?depth=1..3&at=` CI'ın çevresini döndürür:
     - Düzey bağımlılık yönünden gelir: bağımlılar üstte, bağımlılıklar altta, etki taşımayan bağlantılar aynı düzeyde.
     - Her kenarda bildiren kaynaklar yer alır.
     - Okunamayan sınıftaki CI haritaya girmez ve üzerinden genişlenmez; yalnızca sayısı döner.
     - `at` verilirse ilişkiler o ana geri sarılır; düğüm adları bugünkü hâliyledir.
   - Arayüzde yeni bir "İlişki Haritası" sayfası vardır: SVG çizim, klavyeyle düğüm seçimi, "Merkeze al", geçmiş an seçimi ve liste olarak metin alternatifi.
   - CI panelinde "Geçmiş bir an" ve "Zaman çizelgesi" bölümleri vardır. An seçimi saniye hassasiyetindedir, çünkü bir çalışmanın değişiklikleri saniyeler içindedir.

## Sonuçlar

- Etki analizi ve harita, kaynakların bugün bildirdiği ilişkilerle çalışır; eski ilişkiler birikmez.
- Bir ilişkinin neden durduğu sorulabilir: CI panelinde ve haritada "Bildiren: BMC, vCenter".
- Geçmiş bir anı sorgulamanın maliyeti o andan bu yana yapılan değişiklik sayısıyla artar. Sorgu 20 000 değişiklikte durur ve "daha yakın bir an seçin" der. Çok eski anlar için dönemsel anlık görüntü gerekirse ayrıca değerlendirilir (F1-20 ölçek testi).
- Değişim geçmişi aylık bölümlerde tutulur. Saklama süresi kısaltılırsa o süreden eski anlar sorgulanamaz; bu saklama politikasıyla birlikte karar verilmelidir (F2-10).
- Kaynağın ilişkiyi bırakması ile görülmeyen kayıt ayrımı bilinçlidir. Kaynak kaydı hiç gelmiyorsa ilişki CI'ın eskime kararını bekler.

## Alternatifler

- **İlişkiye "son görülme" süresi koyup eskitmek:** keşif sıklığı kaynaktan kaynağa değişir; süre seçimi kurum başına ayar gerektirir. Kaynak kaydı geldiği hâlde ilişkiyi listelemiyorsa bu kesin bir bilgidir. Reddedildi; süre tabanlı ilişki eskimesi gerekirse CMDB sağlık panosunda ölçü olarak ele alınır.
- **Golden record'un zaman damgalı tam kopyası (bitemporal tablo):** her yazım iki kat maliyet getirir ve tek yazan kuralını karmaşıklaştırır. Değişim geçmişi zaten tam ve değiştirilemezdir. Reddedildi.
- **Bildireni `discovery_source` sütununda virgüllü liste olarak tutmak:** kaynak kaydı düzeyinde bilgi kaybolur ve aynı kaynağın iki ucunu ayıramaz. Reddedildi.
