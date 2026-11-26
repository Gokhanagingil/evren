# ADR-0028: Eskime işletimi — gözlemci kaynaklar görmeyince öneri, emekliye ayırma insan kararıyla

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U7.1, F1-06), mimari, güvenlik

## Bağlam

Keşif bir sunucuyu artık görmüyorsa sunucu kapatılmış, taşınmış veya ağdan çıkmış olabilir. Bağlayıcı da hata vermiş olabilir. ServiceNow'da bunun karşılığı CMDB Health "staleness" ölçüsü ile emekliye ayırma iş akışıdır.

Evren'de bugün yapılanlar şunlar:

- Tam liste (snapshot) çalışmasında görülmeyen kaynak kaydı `missing` olarak işaretlenir. Bu yalnızca devre kesici `OK` iken yapılır (ADR-0011, kural 4).
- `metadata/reconciliation_rules.yaml` içindeki `staleness` kuralları yüklenir ve gösterilir. Örneğin donanımda 14 gün eskime, 45 gün emekliye ayırma adayı.

Kuralları işleten bir adım yoktur. Kurallar bu yüzden gösterişten öteye geçmiyor. Eski CI'lar CMDB'de "operasyonda" görünmeye devam ediyor.

İki tehlike var:

1. **Yanlış emekliye ayırma.** Bağlayıcı kısmi veri getirirse yüzlerce canlı CI emekli görünür. Bir kaynak hiç çalışmamışsa "görmedi" ile "bakmadı" karışır.
2. **Yanlış "canlı" sayma.** ServiceNow'daki eski kayıt veya elle girilen değer bir CI'ı "görülüyor" gösterirse eskime hiç çalışmaz.

## Karar

1. **Gözlemci roller.**
   - Eskime yalnızca CI'ı gerçekten gözleyen rollerin kaynaklarıyla hesaplanır: keşif, sanallaştırma, ağ, izleme ve uç nokta güvenliği (rol türü `discovery`, `monitoring` veya `security`; etkin ya da devredilen kaynak).
   - ITSM, Manual ve Platform rolleri CI'ı "canlı" tutmaz. Gölgedeki ve emekli kaynak da sayılmaz.
   - Bu ayrım ürüne değil role bağlıdır (kural 8): `SourceRegistry.counts_for_staleness`.
   - Hiç gözlemci kaydı olmayan CI eskimeye girmez. Örnekler: yalnızca ServiceNow'dan gelen iş uygulaması, elle açılan hizmet. Bu CI'ların doğruluğu sahiplik onayıyla izlenir (ADR-0027).

2. **Değerlendirme kuralı.** Uzlaştırıcı, en az bir gözlemci kaydı `missing` olan CI'ları dönemsel olarak değerlendirir. Kural sınıf hiyerarşisinde en yakın atadan alınır. Sıra şöyledir:
   - Gözlemci kayıtlarından biri `active` ise CI görülüyordur. Açık öneri varsa kapanır ("yeniden görüldü").
   - Bağlı gözlemci kaynaklarından birinin son çalışması tamamlanmamışsa veya devre kesicisi `OK` değilse karar verilmez (**bastırılır**). Açık öneri ne ilerler ne kapanır.
   - Bütün gözlemci kayıtları `missing` ise (`require_all_sources_absent`) son görülme, en yeni `last_seen` değeridir. Süre `stale_after_days` değerini geçtiyse aşama **eskimiş**, `absent_candidate_after_days` değerini geçtiyse **emekliye ayırma adayı** olur.
   - Bu sürümde `require_all_sources_absent: false` desteklenmez. Doğrulayıcı reddeder; tek kaynağın görmemesi yetmez.

3. **Öneri ayrı bir tablodadır; golden record kendiliğinden değişmez.**
   - `insight.staleness_case` CI başına en çok bir açık öneri tutar. İçinde aşama, son görülme, kaynak başına son görülme ve kural bulunur.
   - Uzlaştırıcı öneriyi açar, ilerletir ve kapatır. Golden record'a yazmaz.
   - Platform rolünün durum alanına otomatik değer yazması reddedildi. ITSM önceliği yüksek olduğu için etkisiz kalırdı; yetkisiz bir yaşam döngüsü değişikliği olurdu (kural 5).

4. **Emekliye ayırma bir insan kararıdır (kural 4 ve 10).**
   - Veri sorumlusu (yeni `retire` işlemi) adaydaki CI için iki karardan birini verir. İkisi de gerekçe ister:
     - **Emekliye ayır:** `life_cycle_stage` = End of Life, `life_cycle_stage_status` = Retired, `install_status` = 7 (Retired), `operational_status` = 6 (Retired). Değerler CI'ın Manual kaydına yazılır ve uzlaştırmadan geçer; değişim nedeni `staleness`. CI silinmez, kimlik bağları kalır. Geri almak için düzenle penceresinden yaşam döngüsü yeniden yazılır.
     - **Koru:** öneri kapanır. CI yeniden görülüp sonra yine kaybolmadıkça aynı son görülme için öneri yeniden açılmaz. Örnek: bilerek kapalı tutulan yedek sunucu.
   - İstek tablosu `ingest.retirement_request`'tir. Uzlaştırıcı uygulama anında aday durumunu ve devre kesiciyi yeniden denetler. CI yeniden görüldüyse veya kaynak sağlıksızsa uygulamaz.

5. **Emekli CI yeniden görülürse** öneri satırında `reappeared_at` işaretlenir ve `retired_ci_seen` anomalisi açılır. Veri sorumlusu inceler; CI'ı otomatik olarak canlandırmayız.

6. **İlişkiler.** Emekli CI'ın ilişkileri bu adımda silinmez. Keşfin yazdığı ilişki kaynağından kalkar, elle girilen ilişkiyi kişi kaldırır. İlişki temizliği U7'nin sonraki adımlarıyla değerlendirilir.

## Sonuçlar

- Eskime kuralları işler. Yanlış emekliye ayırmaya karşı üç kilit vardır: devre kesici, bütün gözlemcilerin görmemesi ve insan kararı.
- Eskime değerlendirmesi uzlaştırıcıda dönemsel çalışır (varsayılan 10 dakikada bir). Yalnızca `missing` kaydı olan CI'lara bakar; büyük envanterde bile ucuzdur.
- Delta çalışması kaydı `missing` yapmaz. Yalnızca delta çalışan kaynakta eskime tam liste çalışmasını bekler. Bu güvenli taraftır.
- Platform rolü bu sürümde hâlâ değer yazmaz. Öneriler `insight` şemasındadır.

## Alternatifler

- **Süre dolunca otomatik emekliye ayırma:** kısmi veri veya yanlış eşleme toplu emekliye ayırmaya yol açar. Reddedildi; kural 10.
- **ITSM'in "görmesini" canlılık saymak:** ServiceNow'daki eski kayıt eskimeyi sonsuza dek durdurur. Reddedildi.
- **Tek kaynağın görmemesiyle eskime:** izleme veya uç nokta ajanı kaldırılınca yanlış öneri çıkar. Reddedildi; bütün gözlemciler görmemeli.
