# ADR-0035: Yönetişim görevleri Evren'de; teslim kanal soyutlamasıyla (şimdilik ServiceNow)

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (istek 27.11.2026; U8.4, F1-24), mimari, güvenlik

## Bağlam

Proje sahibi isteğini örneklerle anlattı ve genelleştirilmesini istedi. Örnekler şunlardı:

- Konfigürasyon yöneticisi, uygulama sahiplerine düzenli görev atayabilmeli: verilerinin güncelliğini teyit etsinler, kullanıcı erişimlerini gözden geçirsinler. Tanımı yönetim panelinden yapabilmeli.
- Görev ServiceNow üzerinden açılmalı. İleride Evren üzerinden de açılabilmeli; altyapı buna hazır olmalı.
- Hiçbir CI ile ilişkisi olmayan bir veritabanı bulunduğunda, veritabanının sahibine görev açılmalı.

Ortak ihtiyaç şudur: Evren yalnızca veriyi tutmakla kalmamalı, verinin güncel kalması için gereken işi de yönetmeli. Bu iş sorumlulara görev olarak gitmeli. Görevi iki şey doğurur:

- zamanlanmış kampanya (F1-25),
- bulgu kuralı (F1-26).

Teslim ise bugün ServiceNow'da, yarın Evren'de olacak.

Bu ADR görev modelini, kanal soyutlamasını, görev çalışanını ve tek seferlik görevi tanımlar (U8.4). Kampanyalar ve bulgu kuralları aynı altyapıyı kullanır (U8.5, U8.6).

## Karar

### 1. Görevin doğruluk kaynağı Evren'dir (`gov.task`)

Bir görev şunları taşır:

- **Ne:** başlık, açıklama, tür.
- **Hangi CI için.**
- **Kime:** kişi ve/veya grup, ve bunun dayanağı (`owned_by`, `managed_by`, `support_group`, `assigned`, `unassigned`).
- **Neden:** kaynak (`manual`, `campaign`, `rule`), kaynak kimliği ve tekillik anahtarı. Aynı nedenle yalnızca bir açık görev olabilir.
- **Ne zamana kadar:** son tarih.
- **Sonuç:** karar ve açıklama.

Kanal yalnızca teslim ve iş takibidir: görev ServiceNow'da da açılsa karar Evren'de verilir.

Görev olayları (`gov.task_event`) salt eklemedir. Kapanan görev yeniden açılmaz, kararı değişmez; veritabanı tetikleyicisi bunu zorlar.

### 2. Sorumlu CI'dan bulunur

Sıra şöyledir: sahip → yöneten → destek grubu. Ayrıntılar:

- Pasif kişi atlanır.
- Kişi seçildiğinde destek grubu da eklenir. Böylece kişi ayrılırsa grubun üyeleri görevi görür.
- Kimse yoksa görev **atanmamış** olur ve Konfigürasyon yöneticisinin listesine düşer.

Konfigürasyon yöneticisi isterse görevi doğrudan bir kişiye ya da gruba da açabilir.

### 3. Kanal soyutlaması (`gov.task_channel`)

İki kanal vardır:

- **`evren`:** uygulama içi kanal; her zaman etkindir. Görev "Size düşen işler" listesinde ve Görevler sayfasında görünür.
- **`servicenow`:** görev çalışanı, ServiceNow'da standart bir görev tablosuna kayıt açar.
  - Tablo varsayılan olarak `sc_task`'tir; panelden değiştirilebilir. `sys_` ve `cmdb` ile başlayan tablolar reddedilir.
  - Kayıt yalnızca standart görev alanlarıyla açılır: `short_description`, `description`, `assignment_group`, `assigned_to`, `cmdb_ci`, `due_date`, `correlation_id`, `correlation_display`.
  - ServiceNow'da iş kuralı, ACL veya alan değişikliği gerekmez (kural 11).
  - Açıklamada Evren'deki görevin bağlantısı bulunur.

Teslim kuralları:

- `correlation_id`, Evren görevinin kimliğidir. Çalışan önce bu kimlikle arar; yanıtı kaybolmuş bir denemenin ardından ikinci kayıt açılmaz.
- ServiceNow'a ancak orada karşılığı olan kişi ve grup atanır, yani ServiceNow'dan gelen `ref.sys_user` veya `ref.sys_user_group` kaydı. Karşılık yoksa kanalın varsayılan atama grubu kullanılır. O da yoksa görev Evren'de kalır (`kept_in_evren`) ve bu, gerekçesiyle gösterilir.

Varsayılan kanal panelden değiştirilir. Değişiklik yeni görevleri etkiler; açık görevler kendi kanallarında kalır. Kanalın ServiceNow'dan Evren'e devri, ürün değişimi deseniyle yapılacak (ADR-0009; F2-11).

### 4. Görev çalışanı (`services/governance`, rol `evren_app_governance`)

Her turda (`poll_seconds` aralığıyla veya `pg_notify('evren_task')` gelince):

- **Teslim:** bekleyen görevleri ServiceNow'a gönderir.
- **Eşitleme:** ServiceNow'daki kayıt kapatılmış mı diye bakar. Kapatılmışsa görev Evren'de açık kalır ve "kanalda kararsız kapatıldı" diye işaretlenir; Konfigürasyon yöneticisinin dikkat listesine düşer.
- **Kapatma:** Evren'de kapanan görevin ServiceNow kaydını kapatır. Kapanış durumu ve kapanış notu karara göre yazılır.

Hata ve güvenlik:

- Hata olursa görev `failed` olur ve en çok 10 kez yeniden denenir. Hata iletisi sır ve sunucu gövdesi içermez.
- Çalışan CMDB'ye yazamaz, karar veremez, kanal ayarını değiştiremez; yalnızca teslim alanlarını yazar.
- ServiceNow bağlantı adresi ve kimliği ortamdan gelir; sırlar dosyadan okunur. Panelden değiştirilen kanal ayarı (tablo, durum eşlemesi, varsayılan grup) sır içermez.
- Geliştirme yığınında bellek içi ServiceNow simülatörü kullanılır.
- Çalışan sistem durumunda görünür ("Görev çalışanı"), nabzıyla birlikte kanal durumunu ve bekleyen/başarısız sayılarını bildirir.

### 5. Yetki

- **Herkes:** kendine ve grubuna atanan görevi görür ve kararını verir. Başkasının görevi 404 döner; varlığı sızmaz.
- **Konfigürasyon yöneticisi** (`governance` / `task_admin`): görev açar ve iptal eder (gerekçeli); bütün görevleri, atanmamışları ve dikkat isteyenleri görür; kanal ayarını değiştirir. Kanal ayarı değişikliği denetim izine yazılır.
- **Denetçi:** hepsini salt okunur görür.
- **Gerekçe kuralı:** karar "Doğrulandı / yapıldı" değilse en az 10 karakterlik açıklama zorunludur.

### 6. Arayüz

- **"Görevler" sayfası** (Genel menüde): Bana atanan, Tümü, Atanmamış, Dikkat ve Kanallar sekmeleri vardır.
  - Görev panelinde CI, sorumlu, son tarih, kanal ve ServiceNow kayıt numarası, geçmiş, "Kararı ver" ve "İptal et" bulunur.
- **Yönetim panelinden açılanlar:** "Tek seferlik görev aç", "Görevleri izle", "Görev kanalını yönet".
- **"Size düşen işler" listesi:**
  - Kişiye: "Size atanmış açık görev"; süresi geçenler varsa öne çıkar.
  - Konfigürasyon yöneticisine: "Teslim edilemeyen veya kararsız kapatılan görev" ve "Sorumlusu olmayan görev".

## Sonuçlar

- Zamanlanmış kampanyalar (U8.5) ve bulgu kuralları (U8.6) bu görev modelini ve kanalları olduğu gibi kullanır. Tekillik anahtarı, aynı bulgu için ikinci görev açılmasını önler.
- **Görevi kapatmak kararı yalnızca görevde kaydeder; CMDB'ye dokunmaz.** Örneğin "Düzeltildi" kararı golden record'u değiştirmez; düzeltme her zamanki yolla (elle düzenleme → uzlaştırıcı) yapılır. Veri teyidi görevlerinin sahiplik onayına bağlanması kampanyalarla gelecek (U8.5).
- **Hatırlatma, tırmanma ve e-posta/Teams bildirimi yok.** ServiceNow kanalı kendi bildirimlerini kullanır; Evren kanalının bildirimleri U8.9 ve F2-11'de gelecek.
- **ServiceNow'a yazmak için bir entegrasyon hesabı gerekir.** Hesap, seçilen görev tablosuna kayıt açıp güncelleyebilmelidir. Bu, kurum kararı ve canlı kabul işidir (U10). Hedef tablonun gerçek alan ve durum değerleri kurum örneğinde doğrulanacak.
- **ServiceNow'da `cmdb_ci` alanı dolu değilse:** Evren CI'ı ServiceNow ile aynı sys_id'yi taşımıyorsa (yalnızca keşifle bulunan CI) ServiceNow kaydında CI alanı boş kalır; CI adı görevin açıklamasında yer alır. ServiceNow'da da bulunan CI'da alan doludur.

## Alternatifler

- **Görevi doğrudan ServiceNow'da tutmak, Evren'den yalnızca açmak:** karar ServiceNow'da kalırdı ve Evren kanala bağımlı olurdu; ileride Evren'e geçişte geçmiş kaybolurdu. Reddedildi.
- **ServiceNow'da özel bir "Evren görevi" tablosu veya alanları açmak:** kural 11'e aykırıdır, ServiceNow ekibine iş çıkarır. Reddedildi; standart görev tablosu ve `correlation_id` yeterlidir.
- **Teslimi API'nin kendisinin yapması:** API'ye ServiceNow yazma kimliği verilmesi gerekirdi ve hata durumunda istek beklerdi. Ayrı çalışan seçildi: tek amaçlı, kendi rolüyle, ServiceNow sırrı yalnızca onda.
- **Mevcut bağlayıcı çalıştırıcısını (connector-runner) kullanmak:** o servis kaynaklardan **okur** ve CMDB yetkisi yoktur. Görev kanalı ise ServiceNow'a **yazar**. Yazma kimliğini okuma servisinden ayırmak daha güvenlidir.
