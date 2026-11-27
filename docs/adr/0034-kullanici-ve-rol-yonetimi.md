# ADR-0034: Kullanıcı ve rol yönetimi panelden; veritabanı rolleri her istekte okunur

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (U8.3, F1-10), mimari, güvenlik

## Bağlam

Rol atama tablosu (`iam.principal_role`), görevler ayrılığı kısıtları ve rol olaylarının denetim izi ilk günden vardı (göç 0001–0003). Ama rol vermenin bir ekranı yoktu; atama yalnızca SQL ile yapılabiliyordu.

Proje sahibi, yeni kullanıcı oluşturmanın da yönetim panelinden yapılabilmesini istedi. Görev atama işi de bu temele dayanacak: yönetişim görevleri (U8.4–U8.6) kişilere ve gruplara görev açacak. Bunun için önce kişi ve rol modeli oturmalı.

Mevcut durumun üç zayıf noktası vardı:

1. **Roller çerezde saklanıyordu.** Oturum 8 saat sürüyor. Geri alınan rol, çerez bitene kadar geçerli kalıyordu.
2. **Kendine rol vermeyi engelleyen veritabanı kuralı etkisizdi.** Kural `granted_by <> principal_id` biçimindeydi. API ise "veren" alanını `entra:<oid>` biçiminde yazacaktı; bu durumda iki değer hiçbir zaman eşit olmaz.
3. **Kişi kayıtlarını yalnızca ServiceNow eşlemesi yazıyordu.** ServiceNow'da henüz olmayan bir kişi Evren'e eklenemiyordu.

## Karar

### 1. Kişiler `ref.sys_user`'da

Evren'de kişi açılabilir (`source = 'evren'`).

- API, ServiceNow veya Entra kaynaklı kişide yalnızca **Entra nesne kimliğini** (`entra_oid`) bağlayabilir. Ad, e-posta ve durum kaynağında değişir.
- Bu kural iki katmanda zorlanır:
  - Göç 0030'daki `ref.tg_people_api_guard` tetikleyicisi, API rolünün yaptığı yazımı denetler.
  - Sütun düzeyinde yetkiler: API yalnızca izinli sütunlara yazar. ServiceNow eşlemesi (uzlaştırıcı) Entra kimliğine yazamaz. Evren ve Entra kaynaklı satırı ezmez (ADR-0022).
- Pasifleştirilen kişinin doğrudan rolleri aynı işlemde geri alınır. Entra kimliği değişen kişinin eski kimliğine verilmiş roller de geri alınır.

### 2. Rol atamasında kimlik

- **Kişiye verilen rol:** Entra'da nesne kimliğiyle (oid) eşlenir. E-posta veya oturum adı değişebildiği için kullanılmaz. Geliştirme modunda kullanıcı adıyla eşlenir.
- **Gruba verilen rol:** Entra grup kimliğiyle eşlenir. Gruba rol verebilmek için önce `ref.sys_user_group.entra_group_id` bağlanmalıdır.
- **Kimliği olmayan kişi:** rol alamaz ve bu, arayüzde açıkça söylenir. Entra'da nesne kimliği bağlı olmayan kişi Evren'e giremez.

### 3. Rol verme kuralları (API'de, veritabanı da zorlar)

- Gerekçe zorunludur, en az 10 karakter.
- Yetkili (elevated) roller süreli verilir:
  - Yetki yöneticisi ve şema onaylayıcı en çok 90 gün.
  - Break-glass (Platform yöneticisi) en çok 1 gün.
  - Yetkili olmayan roller süresiz olabilir. Süre verilirse en çok 730 gün olur.
- Makine hesabı rolleri (entegrasyon, ServiceNow eşleme) kişiye veya gruba verilmez.
- Kimse kendine veya üyesi olduğu gruba rol veremez, kendi rolünü geri alamaz, kendi kişi kaydını değiştiremez.
  - "Veren" alanı artık kişinin ham kimliğiyle yazılır. Böylece veritabanındaki `granted_by <> principal_id` kuralı gerçekten çalışır.
  - Denetim izinde işlemi yapan kişi `evren.actor` ayarıyla tam kimliğiyle yer alır.
- **Görevler ayrılığı:** hedefin bugünkü rolleri (doğrudan ve bilinen grupları) ile yeni rol karşılaştırılır.
  - Arayüz çatışmayı gönderimden önce söyler ve düğmeyi kapatır.
  - API reddeder.
  - Veritabanı tetikleyicisi doğrudan çatışmayı yine reddeder.
- Aynı rol yeniden verilirse süre ve gerekçe yenilenir; olay "Rol yenilendi" olarak yazılır.
- Geri alma da gerekçe ister. Gerekçe `evren.reason` ayarıyla denetim izine (`audit.security_event`) yazılır. Veritabanındaki denetim fonksiyonu artık verme gerekçesini, geri alma gerekçesini ve kişinin görünen adını da yazar.

### 4. Veritabanı rolleri her istekte okunur

- Oturum çerezinde yalnızca şunlar bulunur:
  - Entra uygulama rollerinden gelen Evren rolleri (`app`).
  - Rol verilmiş Entra grupları (`grp`).
- Her istekte kişiye ve gruplarına verilmiş, süresi dolmamış roller okunur: tek sorgu.
- Sonuçlar:
  - Verilen rol yeniden giriş gerekmeden geçerli olur.
  - Geri alınan veya süresi dolan rol hemen düşer.
- **Görevler ayrılığı her istekte de denetlenir.** Çatışma bir veritabanı atamasından doğuyorsa istek 403 alır. Kimlik sağlayıcının rolleri kendi aralarında girişte denetlenir.
- Eski biçimli oturum çerezi geçersiz sayılır ve yeniden giriş istenir.
- Geliştirme modunda gruplar, kişinin `ref.sys_user_grmember` üyeliklerinden okunur.

### 5. Konfigürasyon yöneticisi rolü

`evren_config_manager` adıyla eklenir. Yeni işlemi `task_admin`'dir ve `governance` kaynağında yönetişim görevlerini tanımlar (U8.4–U8.6).

- Okuyucu rolünü içerir.
- Denetçiyle birleşemez: denetçi, denetlediği süreci yönetemez.

### 6. Okuma ve yönetim yetkisi

- **Yetki yöneticisi:** yönetir ve görür.
- **Denetçi:** salt okunur görür.

Ekran "Yönetim → Kullanıcılar ve Roller"dir. Dört sekmesi vardır:

- **Kişiler:** arama, role ve duruma göre süzme, sağ panelde roller, gruplar ve yetki geçmişi.
- **Gruplar.**
- **Roller ve yetkiler:** rol–yetki matrisi. Kalıtılan izinler soluk gösterilir; birleşemeyen roller, rolü taşıyanlar ve kurallar da buradadır.
- **Yetki geçmişi.**

Yönetim panelinden "Kişi ekle", "Rol ver veya geri al", "Rol–yetki matrisi" ve "Yetki geçmişi" görevleriyle açılır. "Size düşen işler" listesinde süresi 14 gün içinde dolacak rol atamaları görünür.

## Sonuçlar

- **Rol geri alma hemen etkili.** ADR-0010'daki "oturum iptali çerez süresine bağlı" sınırı, veritabanı rolleri için kalktı. Entra uygulama rollerinin geri alınması hâlâ yeniden girişe bağlıdır; bu rol ataması Entra'da yapılır.
- **Yeni grup ataması yeniden girişte görülür.** Çereze yalnızca girişte rol verilmiş gruplar yazılır. Grup tokenı çok sayıda grup içerebilir ve çerez sınırı 4 KB'tır. Grup rolünün geri alınması ise hemen etkilidir.
- **Her istekte bir rol sorgusu.** Birincil anahtar dizinli ve küçük bir tablo üzerinde çalışır.
- **Görev ataması için dayanak hazır:** kişi, grup, Entra kimliği ve Konfigürasyon yöneticisi rolü.
- **ABAC koşul değerlendirmesi hâlâ yok.** Koşullu izin verilmez; örneğin CI sahibinin "yalnızca kendi destek grubu" kuralı. F1-10'un bu parçası açık kalır.
- **Entra'dan kişi ve grup eşitlemesi (Graph) yok.** Hedef ortam ve Entra kaydıyla gelecek (U10). O zamana kadar Entra kimliği elle bağlanır.
- **Kendi hesabını güvenceye alma.** Yetki yöneticisi kendi yetkisini değiştiremez. Bu yüzden kurulumda en az iki yetki yöneticisi olmalıdır.

## Alternatifler

- **Rolleri yalnızca Entra uygulama rolleri ve gruplarıyla yönetmek** (Evren'de atama ekranı yok): rol değişikliği her seferinde Entra yöneticisine giderdi. Gerekçe, süre ve görevler ayrılığı Evren'in denetiminde olmazdı. İki yol birlikte tutuldu:
  - Uygulama rolleri Entra'da kalır, temel erişim içindir.
  - İnce ayar ve süreli roller Evren'dedir.
- **Rolleri çerezde tutup kısa ömürlü oturum kullanmak:** geri alma yine gecikirdi ve kullanıcı sık sık yeniden giriş yapardı. Reddedildi.
- **Sunucu tarafı oturum tablosu:** her istekte bir sorgu yine gerekirdi; ek olarak oturum yaşam döngüsü yönetimi gerekirdi. Rol sorgusu daha basit ve aynı etkiyi verir. Oturumun tamamen iptali (çıkış zorlama) ayrı bir ihtiyaçtır ve açık kalır.
- **Kişi kaydını yalnızca ServiceNow'dan almak:** ServiceNow'dan çıkış hedefiyle çelişir. Ayrıca henüz ServiceNow'da olmayan kişiye rol verilemezdi. Reddedildi.
