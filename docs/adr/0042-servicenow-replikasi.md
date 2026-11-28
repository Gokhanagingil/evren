# ADR-0042: ServiceNow replikası — IRE ile itme, gölge → etkin, önce doğrula sonra yaz, toplu değişiklik freni

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (hedef: Evren ana kaynak, ServiceNow replika; U9), mimari, güvenlik

## Bağlam

Geçiş döneminde Evren ana kaynaktır, ServiceNow CMDB ise replikadır (`docs/05-servicenow-gecisi.md`). Olay, değişiklik ve talep süreçleri ServiceNow'da çalışmaya devam eder. Bu süreçler CI seçimi, atama, onay ve etki hesabı için ServiceNow'daki `cmdb_ci` kayıtlarına bakar. Bu yüzden Evren'in golden record'u ServiceNow'a güvenle yansımalıdır.

Şimdiye kadar ServiceNow yalnızca kaynaktı: ilk yükleme ve Change kayıtları okunuyordu (ADR-0020, ADR-0025). Yazma yönü yoktu. Sorular şunlardı:

- **Nasıl yazılır?** ServiceNow'un kendi tablolarına doğrudan yazmak, ServiceNow'un kimlik ve uzlaştırma kurallarını atlar. ServiceNow belgesi de bunun yerine Identification and Reconciliation API'sini (IRE) önerir. ServiceNow tarafında iş kuralı, ACL veya görev alanı değiştirilmez (kural 11).
- **ServiceNow kaydı yanlış eşlerse ne olur?** IRE kaydı kendi kurallarıyla tanır; örneğin ad aynıysa var olan kaydı günceller. Evren'deki bir CI, ServiceNow'daki başka bir CI'ın üzerine yazılabilir. Bu, Evren'in "ad tek başına kimlik değildir" kuralının (kural 7) replikadaki karşılığıdır ve sessizce olmamalıdır.
- **Toplu hata nasıl sınırlanır?** Bir kural hatası veya yanlış kapsam binlerce ServiceNow kaydını tek turda değiştirebilir. Bu kayıtlar canlı süreçlerin kullandığı kayıtlardır.

Sözleşmenin kaynağı ServiceNow'un herkese açık API başvurusudur ("Identification and Reconciliation API"; ServiceNow/ServiceNowDocs deposu, australia sürümü):

- **İstek:** `POST /api/now/identifyreconcile?sysparm_data_source=…`, gövdede `items[]` ve `relations[]`.
  - Öğe alanları: `className`, `internal_id`, `values`, `sys_object_source_info.source_name/source_native_key/source_recency_timestamp`.
  - İlişki alanları: `parent`/`child` öğe sırası, `type` = `cmdb_rel_type.name`.
- **Yanıt:** `result.items[]` (`operation`, `sysId`, `inputIndices`, `errors`) ve `result.relations[]`.
- **İşlemeden deneme:** `POST /api/now/identifyreconcile/query` aynı yükü alır ama veritabanına yazmaz.

## Karar

### 1. Ayrı servis, ayrı kimlik, en az yetki

Replikayı ayrı bir çalışan, **sn-sync** yazar (`services/sn-sync`, `evren sn-sync`). Veritabanı rolü `evren_app_sync`'tir.

- Rol CMDB'ye, replika ayarına ve onay sütunlarına yazamaz. Yalnızca eşleme durumunu, çalışma kayıtlarını ve nabzını yazar.
- Eski tasarımdan kalan "kaynak kaydı yazma" yetkisi geri alındı. ServiceNow'dan alım bağlayıcı ve uzlaştırıcıdan geçer (ADR-0020).
- ServiceNow yazma kimliği yalnızca bu serviste durur ve dosyadan okunur (`EVREN_SN_REPLICA_*_FILE`). Bu kimlik, görev kanalının kimliğinden (ADR-0035) ayrıdır.

### 2. Kapsam metadata'da

`metadata/sn_sync.yaml` iki şeyi tanımlar:

- **Sınıflar:** sunucu, ağ cihazı, sanal makine, uygulama, hizmet ve iş uygulaması; alt sınıflarıyla birlikte.
- **Dışlanan alanlar:** `discovery_source`, `first_discovered`, `last_discovered` (IRE bunları kendisi tutar) ve `duplicate_of` (Evren'in iç izi).

Alan kuralları:

- ServiceNow'a gönderilen alanlar sözlükten gelir: `sn_sync` işaretli, ServiceNow'da da var olan (`origin <> 'evren'`) ve kısıtlı olmayan alanlar.
- Tohum yüklemesi kapsamı yalnızca ilk kez ekler. Panelden yapılan açma/kapama korunur.
- Kişi ve grup referansı yalnızca kayıt ServiceNow kaynaklıysa gönderilir. Evren'de veya Entra'da doğan kişinin ServiceNow'da karşılığı yoktur; gönderilmeyen alan kayıtta görünür (`skipped_fields`).
- CI referansı, eşlemedeki ServiceNow sys_id'sine çevrilir.
- Evren'de boş olan alan ServiceNow'da silinmez.

### 3. Önce gölge, sonra etkin

Replikanın modu **kapalı → gölge → etkin**'dir. Bu, ürün değişiminin deseniyle aynıdır (ADR-0009).

- **Gölge modda** yük `/identifyreconcile/query` uç noktasına gider; ServiceNow'a hiçbir şey yazılmaz. ServiceNow her CI'ı nasıl tanıyacağını söyler: yeni kayıt, güncelleme, değişiklik yok, çelişki veya hata. Sonuç eşleme tablosuna yazılır.
- **Etkin moda** geçmek için en az bir tamamlanmış gölge turu gerekir.
- Modu değiştirmek, kapsamı açıp kapamak, toplu itmeyi onaylamak ve çelişki kararı vermek `platform/replica_admin` yetkisi ister. Bu yetki Konfigürasyon yöneticisi ve Platform yöneticisi rollerindedir. Her işlem gerekçelidir ve denetim izine yazılır.

### 4. Önce doğrula, sonra yaz

Etkin modda eşlemesi henüz doğrulanmamış bir CI'ın ilk itmesi önce denenir (`/query`). Yalnızca beklenen sonucu veren CI yazılır. Beklenen sonuç iki türlüdür:

- **ServiceNow kökenli CI:** aynı sys_id'li kaydın bulunması.
- **Evren'de doğan CI:** yeni kayıt açılması.

Aşağıdaki durumlar **çelişki**dir. Çelişkili kayıt ServiceNow'a yazılmaz, "Karar bekleyenler"e düşer:

- ServiceNow başka bir kayıt bulursa;
- Evren'de doğan CI var olan bir kayıtla eşleşirse;
- bulunan kayıt başka bir Evren CI'ına eşliyse.

Çelişki üç kararla çözülür:

- **"ServiceNow kaydını kabul et":** Kabul edilen kayıt ayrı bir sütunda tutulur. sn-sync sonraki turda o kaydı bekleyerek yeniden dener; doğrulayınca eşlemeye yazar ve değerleri gönderir. API ServiceNow sys_id'sini kendisi yazamaz (sütun yetkisi). Bulunan kayıt başka bir Evren CI'ına eşliyse bu karar verilemez; önce Evren'de birleştirme kararı gerekir.
- **"Dışarıda tut":** CI replikaya gönderilmez.
- **"Yeniden dene":** CI sonraki turda yeniden denenir.

İlk başarılı itmeden sonra ServiceNow CI'ı kaynak anahtarıyla tanır: `sys_object_source` = kaynak adı + Evren sys_id. Sonraki itmeler doğrudan yazılır; dönen sys_id yine eşlemeyle karşılaştırılır.

**Aynı kimlik anahtarını taşıyan ikinci CI aynı turda gönderilmez.** Kimlik anahtarı seri numarası veya sınıf + addır. İki CI aynı denemede ikisi de "yeni kayıt" görünür, ama yazmada ilki kaydı açar ve ServiceNow ikinciyi onunla eşleştirip üzerine yazar. Bu sorun yerel denemede bulundu ve şöyle çözüldü:

- gölgede ikinci CI çelişki olarak öngörülür;
- etkin modda ikinci CI ertelenir ve sonraki turun denemesi çelişkiyi yazmadan önce yakalar.

### 5. Toplu değişiklik freni

Tur başına en çok değişiklik ayarlanır (`max_changes`, varsayılan 200). Bu sayı **gerçekten değişen** CI'larla karşılaştırılır; yük özeti aynı olan CI sayılmaz.

- Sınırı aşan tur gönderilmez, **bekletilir**. Yönetim panelinde "Size düşen işler"e ve sistem durumuna düşer.
- Yetkili kişi gerekçeyle bir kez onaylar. Çalışan bekleyenleri sırayla gönderir.
- Onaylı itmede de eşlemesi doğrulanmamış CI'lar önce denenir.

İlk yükleme bu yüzden doğal olarak onayla başlar.

Kapsam dışı bir alanın toplu değişmesi (ör. sahiplik onayı kampanyası) freni tetiklemez. ServiceNow'a gidecek bir şey yoktur; yalnızca görülen sürüm ilerler.

### 6. Değişiklik algılama sürümle

Bir CI'ın değişip değişmediği zaman damgasıyla değil, CI'ın her güncellemede artan sürümüyle (`golden_version`) izlenir. Eşlemede son itmede ve son gölge denemesinde görülen sürüm durur.

Zaman damgası karşılaştırması bir değişikliği kaçırabilirdi: itme sırasında başlayıp sonra işlenen bir uzlaştırma, daha eski bir zaman damgası taşır. Sürümle izlemede bu yarış durumu yoktur. Yük özeti (`last_pushed_hash`) aynı içeriğin tekrar gönderilmesini önler.

### 7. İlişkiler

İki ucu da replikada eşit olan ilişki `relations` ile gönderilir. Uçlar kaynak anahtarıyla tanınır; uçlar için yalnızca sınıf ve ad gönderilir, diğer alanlar değişmez.

IRE ilişki silmez. Evren'de kalkan ilişki eşlemede **kaldırıldı** olarak işaretlenir. ServiceNow'da kaldırılması şimdilik işletim prosedürüdür; bkz. Sonuçlar.

### 8. Hata ve yeniden deneme

ServiceNow'a ulaşılamazsa kayıt hata olur ve tur başarısız sayılır. Kayıt artan aralıkla yeniden denenir: 1, 2, 4… dakika, en çok 6 saat.

- Hata iletisi sır ve sunucu gövdesi içermez.
- Bilinen IRE hata kodları Türkçe açıklanır. Örnekler: aynı kimlikte birden çok kayıt (`MULTIPLE_DUPLICATE_RECORDS`), kimlik alanı eksik (`MISSING_MATCHING_ATTRIBUTES`).

### 9. Görünürlük

- **ServiceNow Replikası sayfası** (Kaynaklar → SN replikası) aşamalı açıklama ilkesine uyar (ADR-0041). İlk bakışta mod, sayılar, bekletilen itme ve karar bekleyen kayıtlar görünür. Kapsam ve çalışma geçmişi katlanır alandadır.
- Sistem durumunda "ServiceNow replikası (sn-sync)" servisi; mod, bağlantı ve eşit/çelişki/hata sayılarıyla birlikte.
- Yönetim panelinde bir görev kartı ve iki "Size düşen iş": onay bekleyen toplu itme, karar bekleyen kayıt.

## ServiceNow tarafında gerekenler (F2-02, canlı kabulde)

Bunlar sözlük ve yapılandırma ayarıdır. İş kuralı, ACL veya görev alanı değişikliği gerektirmez.

1. `cmdb_ci.discovery_source` değer listesine **Evren** değeri eklenir. Ad `metadata/sn_sync.yaml` → `source_name` ile aynı olmalıdır.
2. Kapsamdaki sınıflar ve alanlar için "Evren" veri kaynağına en yüksek öncelikli uzlaştırma kuralı verilir. Böylece eski entegrasyonlar Evren'in yazdığını ezemez.
3. Ayrı bir entegrasyon kullanıcısı açılır. Yetkileri: IRE ile yazma ve kapsamdaki CMDB tablolarını okuma.
4. Eski doğrudan yazan entegrasyonlar kapatılır (F2-03).

## Sonuçlar

- ServiceNow CMDB, Evren'in golden record'unu süreçlerin kullandığı alanlarla yansıtır. Görev ve değişiklik kayıtlarındaki CI referansları değişmez.
- Yanlış eşleşme yazmadan önce yakalanır ve insan kararına düşer. Toplu hata tek turda yayılamaz.
- **Sınırlar:**
  - Sınamalar yalnızca bellek içi simülatörle yapıldı. Gerçek ServiceNow kimlik kuralları kuruma göre değişir ve simülatör bunların sadeleştirilmiş bir alt kümesidir. Canlı kabul U10'dadır.
  - ServiceNow'da ilişki kaldırma ve emekli CI'ın ilişkileri işletim prosedürüdür.
  - Evren'de kalıcı silinme yoktur, emeklilik değerleri gider. Silme ServiceNow'a yansımaz.
  - Kapsam dışına çıkarılan sınıfın ServiceNow'daki kayıtları olduğu gibi kalır.
  - Replika ayarları için platform uyarı kuralı yok. Bekleyen itme ve karar bekleyen kayıt "Size düşen işler"de ve sistem durumunda görünür.
  - ITOM abonelik birimi sayımı ServiceNow ile teyit edilmedi (F2-04).
- Geliştirme yığınında sn-sync simülatörle çalışır. Simülatör açılışta Evren'in ServiceNow kökenli CI'larını ve daha önce gönderilmiş kayıtları bilir.

## Değerlendirilen seçenekler

- **Table API ile doğrudan yazma:** ServiceNow'un kimlik ve uzlaştırma kurallarını atlar; mükerrer ve sahipsiz kayıt üretir. ServiceNow belgesi IRE'yi önerir. Reddedildi.
- **Doğrulamadan yazma, çelişkiyi sonradan raporlama:** Yanlış eşleşmede ServiceNow'daki başka bir CI'ın değerleri ezilir; süreçler o anda yanlış veriyle çalışır. Reddedildi. Doğrulama ilk itmede bir istek daha ekler; sonraki itmelerde ek maliyet yoktur.
- **MCP ile replikasyon:** Kural 9 gereği reddedildi; replikasyon REST ve IRE ile yapılır.
