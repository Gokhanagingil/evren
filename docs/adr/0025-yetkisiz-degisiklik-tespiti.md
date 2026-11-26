# ADR-0025: Değişiklik kayıtları ve yetkisiz değişiklik tespiti — gözlenen sapma, onaylı pencere, gecikmeli değerlendirme

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U5.5, F1-04), mimari, güvenlik

## Bağlam

`docs/03` §6 yetkisiz değişikliği şöyle tanımlar: izlenen bir öznitelik (RAM, CPU, işletim sistemi, seri) değişti ve CI için o aralıkta onaylı bir değişiklik kaydı yok. Bu kayıt GRC Platformu'ndaki "yetkisiz değişiklik" kontrolüne kanıt olur.

Hazır olanlar şunlardı:

- `ChangeManagement` rolü (`ci_attribute_source: false`) ve `ServiceNowChange` veri kaynağı.
- `ref.sn_change_request` ve `ref.sn_task_ci` tabloları.
- `insight.anomaly` tablosunda `unauthorized_change` türü.

Eksik olanlar şunlardı:

- Değişiklik kayıtlarını çeken bağlayıcı.
- Tablolara yazan yol.
- Sapmayı gözleyen ve onaylı pencereyle karşılaştıran mantık.

Dört zorluk var:

1. **Ne zaman değişti?** Keşif aracı değişikliği anında görmez. Bildiği tek şey, eski değeri en son ne zaman gördüğü ve yeni değeri ilk ne zaman gördüğüdür. Gerçek değişiklik bu aralıktadır.
2. **Golden record değişimi her zaman sapma değildir.** Kazanan kaynak değişebilir (öncelik, kaynak eksildi). Yeni bir kaynak bağlanabilir. Elle düzeltme yapılabilir. Bunlar makinede bir değişiklik değildir.
3. **Değişiklik kaydı geç gelir.** Bağlayıcı 10 dakikada bir çeker. Acil değişiklikler çoğu zaman uygulamadan sonra kaydedilir.
4. **Eşleme hatası toplu sahte sapma üretir.** Ör. işletim sistemi adı biçimi değişirse yüzlerce sunucu "değişti" görünür. GRC'ye yüzlerce sahte kanıt gitmemelidir.

## Karar

1. **Değişiklik kayıtları referans yolundan akar** (ADR-0022 ile aynı yol). Bağlayıcı `servicenow_change` Table API ile salt okunur çeker:
   - `change_request` alanları: `number`, `short_description`, `type`, `state`, `approval`, `start_date`, `end_date`, `work_start`, `work_end`, `cmdb_ci`.
   - `task_ci` alanları: `task`, `ci_item`. Yalnızca değişiklik kayıtlarına ait satırlar çekilir (`task.sys_class_name=change_request`).
   - Kayıt geçitten ve Kafka'dan uzlaştırıcıya gelir. Uzlaştırıcı aynı sys_id ile `ref.sn_change_request` ve `ref.sn_task_ci` tablolarına yazar.
   - Tablo listesi tektir: `evren_core.reference.CHANGE_TABLES`. Veritabanı sütun düzeyi yetki verir (göç 0021).
   - Bu tabloları yalnızca **değişiklik kaynağı** (`change_source`) rolündeki etkin veya devredilen kaynak yazar. Gölgedeki yeni ürün yazmaz (ADR-0009). Referans kaynağı (ITSM) da bu tabloları yazamaz; değişiklik kaynağı da grup ve kişi tablolarını yazamaz.
   - Çekme kapsamı:
     - Tam liste: açık değişiklikler (`active=true`) ile son `lookback_days` gün içinde güncellenen veya bitişi bu tarihten sonra olan kayıtlar (varsayılan 30).
     - Delta: son başarılı çalışmadan bu yana güncellenenler.
     - `task_ci` için tam liste: bağlı değişikliği açık olan satırlar ile kendisi veya bağlı değişikliği güncellenen satırlar (`task.sys_updated_on`). Delta yalnızca güncellenenleri alır.

2. **Rol sözlüğü.**
   - Durum, onay ve tür değerleri eşleme dosyasında Evren'in değişiklik sözlüğüne çevrilir.
     - Durum: ServiceNow kodu `-5…4` → `new`, `assess`, `authorize`, `scheduled`, `implement`, `review`, `closed`, `canceled`.
     - Onay: `approved`, `rejected`, `requested`, `not_requested`, `cancelled`, `not_required`, `skipped`.
     - Tür: `standard`, `normal`, `emergency`.
     - Tanınmayan değer `other` olur ve yetki saymaz.
   - Kurallar (`metadata/change_control.yaml`) yalnızca bu sözlüğü ve kaynak rollerini kullanır. Ürün adı içermez. Başka bir değişiklik aracı aynı rolde yeni veri kaynağı olur, kurallar değişmez (kural 8).
   - Tablolar ServiceNow adını taşısa da `state`, `approval` ve `type` sütunlarında sözlük değeri durur.

3. **Gözlenen değişiklik** (`insight.config_change`) yalnızca şu koşulların hepsi sağlanınca yazılır:
   - Kaynak **gözlemci rollerinden** biri: Keşif, Sanallaştırma, Ağ keşfi, Uç nokta güvenliği. İzleme envanteri çoğu zaman elle girildiği için dışarıda.
   - Aynı kaynağın **kendi kaydında** o öznitelik boş olmayan bir değerden başka bir değere değişti.
   - Bu değişiklik golden record'u değiştirdi (kazanan bu kaynak).
   - Öznitelik sınıf için izleniyor. İzlenen öznitelikler `watched` altındadır ve alt sınıflara kalıtılır.

   İlk doldurma, kazanan kaynağın değişmesi, elle düzenleme, ayırma, ilk yükleme ve yeniden sınıflandırma sapma sayılmaz.

   Değişiklik aralığı şudur: kaynağın eski değeri en son gördüğü an (`observed_from`) ile yeni değeri ilk gördüğü an (`observed_at`).

4. **Onaylı pencere.** Bir değişiklik kaydı şu üç koşul sağlanırsa gözlemi kapsar:
   - Kaydın **CI'ı** CI'ın kendisi olabilir: `cmdb_ci` alanında ya da `task_ci` satırlarında. CI Evren'de doğmuşsa replika eşlemesindeki ServiceNow sys_id'si de kabul edilir.
   - **Yetkili** olmalı:
     - Durumu `authorized_states` içinde olmalı (varsayılan `scheduled`, `implement`, `review`, `closed`).
     - Onayı `approved` olmalı. Türü önceden onaylı ise (`standard`) onay alanı aranmaz.
     - İptal edilmiş veya reddedilmiş kayıt kapsamaz.
   - **Penceresi** gözlem aralığıyla örtüşmeli. Varsayılan tolerans her iki uçta 60 dakikadır.
     - Pencere planlı (`start_date`–`end_date`) ya da gerçekleşen (`work_start`–`work_end`) olabilir.
     - Uygulamada olan kaydın gerçekleşen penceresi şimdiye kadar uzar.

5. **Gecikmeli ve geri dönebilen değerlendirme.** Uzlaştırıcı çalışanı her turda değerlendirme yapar:
   - **Bekleme:** gözlem `grace_minutes` (varsayılan 60) beklemeden değerlendirilmez. Çalışma kapanmadan da değerlendirilmez.
   - **Sonuç:** kapsayan kayıt varsa sonuç `authorized` olur ve kaydın numarası yazılır. Yoksa sonuç `unauthorized` olur.
   - **Anomali:** CI, kaynak ve gözlem anı başına bir `unauthorized_change` anomalisi açılır. Anomali öznitelikleri, eski ve yeni değerleri ve bakılan değişiklik kayıtlarının neden kapsamadığını Türkçe açıklar.
   - **Önem:** kuralla belirlenir; varsayılan `medium`, üretimde `high`.
   - **Yeniden bakma:** açık anomali `recheck_days` (varsayılan 7) boyunca yeniden değerlendirilir. Sonradan kaydedilen onaylı değişiklik gözlemi kapsarsa anomali `resolved` olur. Kayıt silinmez; "sonradan kaydedilen değişiklik" notu ve değişiklik numarası kanıt olarak kalır.
   - İnsanın `false_positive` olarak işaretlediği anomaliye dokunulmaz.

6. **Devre kesici.** Devre kesici gözlemin geldiği çalışmayı açtıysa (`open`) sonuç `suppressed` olur ve anomali açılmaz. Gerekçe "çalışma güvenilir değil" olarak yazılır. Eşleme hatası GRC'ye sahte kanıt üretmez (kural 4'ün ruhu).

7. **Görünürlük.**
   - API CI ayrıntısında son gözlenen değişiklikleri sonuç ve değişiklik numarasıyla verir. Alan hassasiyeti `history` ile aynı kurala uyar.
   - Özet uç noktası açık yetkisiz değişiklik sayısını verir. CI listesi `kapsam=yetkisiz_degisiklik` süzgecini alır.
   - GRC kontrolü **CMDB-05** "Onaylı değişiklik kaydı olmadan yapılandırması değişen CI":
     - Kanıt: açık `unauthorized_change` anomalisi olan CI'lar.
     - Bağımlılık: değişiklik kaynağı rolü. Değişiklik kaynağının son çalışması sağlıklı değilse sonuç `inconclusive` olur.
   - `insight.config_change` tablosunu okuyucu rolü (`evren_readonly`, MCP) okumaz. Tablo öznitelik geçmişi taşır; API izin ve alan hassasiyetiyle süzer, denetçi okur.

8. **Yazım sınırları.**
   - `insight.config_change` tablosuna yalnızca uzlaştırıcı yazar. Golden record'a dokunmaz; anomali açar (kural 1, 10).
   - Değişiklik kayıtları CI özniteliği olmaz (`ci_attribute_source: false`).
   - Hiçbir şey ServiceNow'a yazılmaz (kural 11).

## Sonuçlar

- Yetkisiz değişiklik ölçülebilir ve kanıtlanabilir hale gelir. Her gözlemin aralığı, kaynağı, çalışması ve değerlendirmesi saklanır.
- Uzun keşif aralığı tespiti zayıflatır. Kaynak sunucuyu haftada bir görüyorsa, o hafta içindeki herhangi bir onaylı pencere değişikliği kapsar. Bu bilinçli bir seçimdir: kesin olmayan zamanı kesinmiş gibi göstermeyiz.
- ServiceNow silinen kaydı Table API'de göstermez. `task_ci`'dan sonradan çıkarılan bir CI, kapsamda kalmaya devam edebilir (eksik tespit yönünde hata). Tam liste çalışmaları bunu sınırlar; sınır `docs/04`'te yazılı.
- İzleme rolü bilinçli olarak dışarıdadır. Kurum kendi izleme envanterine güveniyorsa `observer_roles`'a ekler.
- Kurumun özelleştirdiği durum kodları eşleme dosyasında çevrilir. Çevrilmeyen kod `other` olur ve yetki saymaz (güvenli varsayılan).

## Alternatifler

- **Doğrudan `audit.ci_change`'ten okumak:** Uzlaştırıcının denetim tablosunu okuma yetkisi yok ve olmamalı (en az yetki). Ayrıca denetim satırı kaynağın kendi kaydındaki değişimi ve gözlem aralığını taşımıyor. Reddedildi.
- **Anında karar:** Gecikmeli kaydedilen acil değişikliklerde sahte anomali üretir. Reddedildi; bekleme ve yeniden bakma seçildi.
- **ServiceNow'da iş kuralıyla işaretleme:** Kural 11 gereği reddedildi.
- **ServiceNow durum kodlarını olduğu gibi kullanmak:** Kurallar ürüne bağlanırdı (kural 8). Reddedildi; çeviri eşleme dosyasında yapılır.
