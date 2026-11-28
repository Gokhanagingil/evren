# ADR-0046: Kapalı döngü kapsam güvencesi

- Durum: Kabul edildi (P1.1, P1.2); sonraki parçalar aşağıda, her parça bu kayda eklenir
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (öneri: "akıl katmanı ters yönlü de çalışabilir mi?", 28.11.2026), mimari

## Bağlam

Akıl katmanı veriyi kaynaktan Evren'e taşır. Ters yönde de değer üretebilir: Evren uzlaştırdığı bilgiyle bir **kapsam boşluğunu** bulur. Örnek: üretimde çalışan, ama izleme sisteminde olmayan ve uç nokta koruması görmeyen sunucu. Bu boşluk tek bir kaynakta görünmez; ancak kaynakları birleştiren bir katman görür.

Akış şöyle olmalı:

1. Evren boşluğu sorumluya görev olarak gönderir.
2. Sorumlu düzeltir.
3. Evren sonucu yine kaynaklardan doğrular ve görevi kanıtla kapatır.

Proje sahibi bunu erken bir pilotta göstermek istiyor (izlek P1, plan F1-30).

Altyapının çoğu hazırdı: `not_seen_by_role` kural türü, bulgu → sorumluya görev, bulgu giderilince görevin kapanması, kaynak sağlıksızken "belirsiz" sonuç (ADR-0037). Eksikler şunlardı:

- Aynı CI için rol başına ayrı bulgu ve ayrı görev açılıyordu.
- "Çalışıyor" kanıtı yoktu; kapatılmış makine için de görev açılırdı.
- Görülme tazeliği yoktu; izlemesi duran sunucu "izleniyor" sayılırdı.
- Kapsam boşluğu ile kimlik boşluğu ayrılmıyordu.
- Doğrulama olay tetikli değildi.
- Tek ekran yoktu.

## Karar

### P1.1 — Kapsam politikası (`coverage_gap` kural türü)

Kapsam politikası yeni bir kalite kuralı türüdür. Panelden şablonla tanımlanır; ürün adı içermez, rollere yazılır (kural 8).

| Parametre | Anlam | Varsayılan |
| --- | --- | --- |
| `roles` | CI'ı görmesi zorunlu kaynak rolleri | — (en az bir) |
| `fresh_days` | Zorunlu rol için görülme tazeliği (gün, 1–90) | 7 |
| `observed_by` | "Çalışıyor" kanıtı veren roller (isteğe bağlı; zorunlu rollerle kesişemez) | — |
| `observed_within_hours` | Gözlemci rolün CI'ı son kaç saatte görmüş olması gerektiği (1–720) | 48 |

Davranış:

- **Bulgu koşulu:** Kapsamdaki CI, gözlemci rollerden biri tarafından son *N* saatte görülüyorsa (yani çalışıyorsa) ve zorunlu rollerden en az biri tarafından son *M* günde görülmüyorsa bulgu olur.
- **Tek CI, tek bulgu, tek görev.** Bulgunun ayrıntısı eksik rolleri ve her rol için son görülme zamanını taşır, örneğin "İzleme rolünde 14 gündür görülmüyor; Uç nokta güvenliği rolünde hiç görülmedi". Ayrıntı her değerlendirmede güncellenir.
- **Kapanış:** Bir rol giderilince bulgu daralır. Bütün roller giderilince bulgu kapanır ve açık görev kendiliğinden kapanır (ADR-0037).
- **Sayılan kaynaklar:** Yalnızca etkin ve devrediliyor durumdaki kaynaklar kapsam sayar. Gölge ve emekli kaynak saymaz (ADR-0009).
- **Sağlık koşulu:** Zorunlu veya gözlemci rollerden birinin kaynağı son çalışmada sağlıklı değilse kural sonuç vermez ve bulgular değişmez (kural 4).
- **Şablon:** "Üretimde çalışan ama izlenmeyen veya korumasız sunucu". İzleme + uç nokta güvenliği, 7 gün; sanallaştırma veya keşif, 48 saat.
- **En az yetki:** Görev çalışanı yalnızca `ingest.source_record` tablosunun kimlik ve `last_seen` sütunlarını okur. Kayıt içeriğini göremez, yazamaz (göç 0038).

### P1.2 — Kapsam boşluğu mu, kimlik boşluğu mu?

Bir rol sunucuyu "görmüyor" olabilir; ama o rolün kaynağı sunucuyu tanıyor, yalnızca kaydı sunucuya bağlanamamış da olabilir. O durumda sorun sunucu sahibinde değil eşleştirmededir. Yanlış kişiye giden görev güveni en hızlı bozan şeydir.

- **İpuçları:** Eksik her rol için iki iz aranır:
  - o rolün kaynağından gelen ve bu CI'ı aday gösteren **açık eşleştirme kuyruğu kaydı** (`candidate_ci` veya `candidates`);
  - o rolün kaynağına bağlı, aynı kısa ada veya IP'ye sahip **başka bir CI** (olası mükerrer).
- **Ayrıntı:** `identity` (ipuçları), `identity_roles` (ipucu olan eksik roller), `identity_only` (eksik rollerin hepsinde ipucu var).
- **Yönlendirme:**
  - `identity_only` ise sahibine görev açılmaz. Bulgu eşleştirme analistinin "Size düşen işler" listesine düşer. Kuyruk kararı kaydı bağlayınca sonraki değerlendirmede bulgu kendiliğinden kapanır.
  - Karışık durumda (bir rol kimlik, bir rol gerçek eksik) sahibine görev açılır. Metin kimlik kısmını "olası kimlik boşluğu" diye ayırır.
- **Arayüz:** Bulgu ayrıntısında "Olası kimlik boşluğu" bölümü yer alır; kuyruk kaydına veya diğer CI'a bağlantı verir.
- **En az yetki:** Görev çalışanı kuyruk kaydının yalnızca kimliğini, kaynağını, durumunu ve adaylarını okur. Karar ve gerekçe sütunlarını göremez, yazamaz (göç 0039).
- **Sınır:** Ad eşleşmesi kısa ad (ilk noktaya kadar) ve büyük/küçük harf duyarsızdır; Türkçe harf katlaması SQL'de yapılmaz. Bu yalnızca bir ipucudur; görevi sahibinden alır, bağı ise kurmaz.

### Sonraki parçalar (planlandı)

- **P1.3 Olay tetikli doğrulama ve kanıtlı kapanış.** Bir rolün kaynak çalışması sağlıklı bitince o role bağlı kurallar hemen yeniden değerlendirilir. Kapanış notu kanıtı taşır, örneğin "Zabbix çalışması 28.11 14:05'te gördü"; kanıt denetim izine ve GRC Platformu'na gider.
- **P1.4 Öncelik:** Görev süresi ve sırası, CI'ın taşıdığı hizmetin kritikliğine göre belirlenir.
- **P1.5 Kapsam dosyası ekranı:** Tek cümle özet; ardından istenince açılan rol × kaynak × son görülme tablosu, taşıdığı hizmet, sorumlu ve görev durumu.

### Değişmeyenler

- Evren kaynak sisteme yazmaz (kural 2). Düzeltmeyi sorumlu yapar, Evren doğrular.
- Otomatik düzeltme istenirse ayrı bir eylem kanalı ve insan onayı gerekir; bunun için ayrı bir karar kaydı yazılır.

## Sonuçlar

- Aynı motor yedekleme, zafiyet tarama, yama ve log gönderimi kapsamı için de çalışır: yeni bir rol, politikada yeni bir satırdır. Maruziyet yönetimi (F2-12) bunun üzerine kurulur.
- **Sınırlar:**
  - Testler sentetik veriyle yapıldı.
  - Pilotun gerçek veriyle mi (üç kaynağın salt okuma hesabı erkene çekilir), sentetik gösterimle mi yapılacağı proje sahibinin kararına bağlıdır.
  - Bir kuralın değerlendirme sıklığı hâlâ kendi zamanlamasıdır; olay tetiklemesi P1.3'tedir.
  - Görev açıklaması görev açıldığı andaki eksikleri yazar. Güncel durum bulgudadır.

## Değerlendirilen seçenekler

- **Her rol için ayrı `not_seen_by_role` kuralı:** Aynı sunucuya iki görev açar ve "ne eksik" sorusunu iki yere böler. Reddedildi; eski tür, tek rollük basit durumlar için kalır.
- **Kapsam politikasını ayrı bir tabloda tutmak:** Kural altyapısının önizlemesini, istisnasını, görev açmasını ve sağlık ölçümünü yeniden yazmayı gerektirirdi. Reddedildi.
