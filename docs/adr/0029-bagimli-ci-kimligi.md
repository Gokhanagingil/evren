# ADR-0029: Bağımlı CI kimliği — ana CI bağlamında yerel anahtar, ana kayıt alım zarfında

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U7.2, F1-07), mimari

## Bağlam

Bazı CI'lar kendi başına tekil değildir:

- Ağ adaptörünün MAC'i sanal makine, konteyner veya HSRP/VRRP ile birden çok cihazda tekrarlanabilir.
- "nginx" adlı uygulama yüzlerce sunucuda çalışır.

ServiceNow IRE bunları **bağımlı** tanımlama kuralıyla çözer: kimlik, ana CI ile yerel anahtarın (MAC, ad) birleşimidir. Ana CI ya bir ilişkiyle (uygulama "Runs on" sunucu) ya da bir başvuru alanıyla (adaptörün `cmdb_ci` alanı) belirtilir.

Evren'de bağımlı kurallar sözlükte tanımlı ve doğrulanıyor (`identification_rules.yaml`: `independent: false`, `parent_relation` veya `parent_reference`; U4). Çözümleyici ise bu sınıflarda bilinçli olarak `insufficient` dönüyor. Bu yüzden:

- NNMi arayüzleri yalnızca düğümün MAC listesine katılıyor; ağ adaptörü CI'ı yok.
- Kurum içi uygulama örnekleri sunucuya bağlanamıyor.

## Karar

1. **Bağlayıcı ana kaydı bildirir, sys_id bilmez.**
   - Alım zarfında yeni, isteğe bağlı bir alan var: `parent: {source_key, data_source?}` (`ParentRef`). Bu alan bağımlı kaydın ana kaydının kaynak anahtarıdır; `data_source` boşsa aynı kaynak kabul edilir.
   - Eşleme dosyasında `parent: {source_key: {from: …}}` yazılır. Bağımlı kayıtta anahtar boşsa eşleme hatası verilir.
   - Kural 2 korunur: bağlayıcı eşleştirme yapmaz, yalnızca ilişkiyi bildirir.

2. **Uzlaştırıcı kaydı ana CI bağlamında çözer.** Sınıfın en yakın kuralı bağımlıysa uzlaştırıcı:
   1. Ana kaydın etkin kimlik bağından ana CI'ı bulur.
   2. Ana kayıt henüz bağlı değilse kaydı saklar ve sonucu `deferred` olarak döner. Çalışma kapanırken (`finish_run`) bekleyen kayıtlar birkaç turda yeniden denenir; böylece veritabanı → örnek → sunucu gibi zincirler de çözülür. Yine de bağlanamayan kayıt beklemede kalır ve sonraki çalışmada denenir. Tahminle CI açılmaz.
   3. Yerel anahtarı kural girdilerinden öncelik sırasıyla kurar: MAC biçimden bağımsız, metin ASCII'ye katlanmış ve küçük harf. Anahtarı `ingest.dependent_key` tablosunda (ana CI, aile, imza, değer) arar. Eşleşme varsa bağlar, yoksa yeni CI açar; yeni CI'ı yalnızca CI açabilen kaynak açabilir.
   4. Anahtarları yalnızca bu tabloya yazar. **Küresel kimlik dizinine (`identity_key`) hiçbir zaman girmez**, bu yüzden aynı MAC iki cihazda iki ayrı adaptördür.
   5. Ana CI ile bağı kaynak kaydına ekler:
      - `parent_reference` kuralında başvuru alanına ana CI'ın sys_id'si yazılır; değer uzlaştırmadan geçer.
      - `parent_relation` kuralında ilişki eklenir. Yön, izinli ilişki kurallarından belirlenir: uygulama "Runs on" sunucu ise bağımlı ebeveyndir, vCenter "Contains" veri merkezi ise bağımlı çocuktur.

3. **Eksik veri tahmin edilmez.** Ana kaydını bildirmeyen ya da yerel anahtar öznitelikleri eksik bağımlı kayıt, gerekçesiyle "yetersiz kimlik" kuyruğuna gider.

4. **İlk uygulama NNMi'dir.** MAC'i olan arayüz `cmdb_ci_network_adapter` kaydı olarak gönderilir (ana kayıt düğümdür). MAC'ler düğümün kimlik eşleşmesine de girmeye devam eder; sunucu ve ağ cihazı birleşmesi değişmez.
   - İki kaynak aynı sunucunun aynı adaptörünü bildirirse tek CI olur, çünkü ana CI aynı golden record'dur.

5. **Görünürlük.** CI ayrıntısında bağımlı CI'lar yerel anahtarlarıyla, bağımlı CI'da ise "bağlı olduğu CI" görünür. CI'a başvuran alanlar CI adıyla gösterilir.

## Sonuçlar

- Ağ adaptörü, uygulama örneği, dosya sistemi, küme düğümü ve veritabanı gibi bağımlı sınıflar artık tanımlanabilir; bağlayıcı eşlemesi gelince çalışır. Kural sözlükte olduğundan yeni bağımlı sınıf için kod gerekmez.
- Bağımlı kayıt ana kaydından önce gelirse aynı çalışmada çözülür; sıra bağlayıcı için zorunlu değildir.
- Ana CI ayrılır (split) veya birleştirilirse bağımlının bağı yapışkandır: bağlı kayıt aynı CI'da kalır. Bağımlıyı yeni ana CI'a taşımak kaynak kaydın yeniden işlenmesiyle olur; bu davranış bilinen bir sınırdır.
- Alım zarfı sözleşmesine geriye uyumlu bir alan eklendi (`schemas/ingest-envelope.schema.json`).

## Alternatifler

- **MAC'i küresel anahtar yapmak:** HSRP sanal MAC'i iki yönlendiriciyi, sanal makine MAC'i iki sunucuyu yanlış birleştirir. Reddedildi.
- **Bağlayıcının ana CI'ı çözmesi:** bağlayıcı CMDB'ye erişmez (kural 2). Reddedildi.
- **Bağımlı kaydı ilişki listesinden çıkarsamak:** ilişki tipi kurala göre değişir ve yön karışır; açık `parent` alanı daha güvenli. Reddedildi.
