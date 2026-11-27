# ADR-0038: Yönetim sihirbazları — sınıf ve alan, yeni entegrasyon

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (istek: "yeni CI girişinden, CMDB'ye class ya da alan eklemeye, yeni kullanıcı oluşturmaktan entegrasyonlara kadar her şey buradan yapılabilmeli"; U8.7), mimari, güvenlik

## Bağlam

Yönetim paneli (ADR-0033) işleri tek yerden başlatıyordu. İki iş ise hâlâ uzmanlık istiyordu:

- **Sınıf ve alan eklemek:** değişiklik seti penceresi genel amaçlıydı. Kullanıcı işlem türünü seçiyor, teknik adı (`u_…`) kendisi yazıyor, devralınan alanları ve kimlik kuralını görmüyordu. Hatalar ancak kayıtta çıkıyordu.
- **Yeni entegrasyon kurmak:** yeni bir bağlayıcı örneği (ör. ikinci veri merkezindeki izleme sunucusu) dört ayrı yerde işlem istiyordu. Veri Kaynakları sayfasından geçit kimliği verilmesi, çalıştırıcının örnek dosyasına YAML yazılması, sırların kasaya konması ve sonra "şimdi çalıştır". Doğru YAML'ı yazmak belgeyi okumayı gerektiriyordu.

ADR-0021 şunu kararlaştırmıştı: örnek tanımı (kaynak adresi, kullanıcı adı, sır dosya yolları) **yalnızca çalıştırıcıda** durur ve veritabanına bildirilmez. API kaynak sırlarını görmez. Sihirbaz bu kararı bozmamalıydı.

## Karar

### 1. Ortak adım adım pencere

`apps/web/assets/js/lib/wizard.js` bütün sihirbazların ortak bileşenidir. Davranışı:

- Üstte adım göstergesi vardır: geçilen, bulunulan ve sıradaki adımlar. Gösterge kaydırınca da üstte kalır.
- Altta Vazgeç, Geri ve İleri düğmeleri vardır. Son adımda İleri düğmesi işin adını alır.
- Klavye: metin kutusunda Enter ilerletir, Esc kapatır. Her adım değişince odak adımın ilk alanına gider. Bulunulan adım ekran okuyucuya `aria-current="step"` ile bildirilir.
- Bir adım ileri geçmeden önce kendi denetimini yapar. Hata pencerenin altında, odak hatalı alana gider.
- Geri dönülünce girilenler kaybolmaz.

### 2. Sınıf ve alan sihirbazı

Sihirbaz bir değişiklik seti üretir; akış ADR-0015'teki gibidir. Adımlar:

| Sihirbaz | Adımlar |
|---|---|
| Yeni sınıf | üst sınıf → ad → alanlar (isteğe bağlı) → gözden geçir |
| Sınıfa alan | sınıf → alanlar → gözden geçir |

Veri Modeli sayfasındaki "Bu sınıfa alan ekle" alan sihirbazını sınıf seçili açar.

- **Sınıf seçimi:** sınıf ağacı aranabilir (Türkçe harf duyarlı). Seçilen sınıf için şunlar gösterilir:
  - kaç alanın devralınacağı;
  - alt sınıflar;
  - kimlik kuralı: kendi kuralı mı, devralınmış mı, hangi öznitelikler, ana CI bağlamı.
- **Teknik ad:** Türkçe addan önerilir. Türkçe harfler ASCII'ye çevrilir, `u_` öneki eklenir. Elle değiştirilebilir.
- **Alanlar:** bir sette birden çok alan eklenebilir. Tip açıklamalıdır. Uzunluk yalnızca metinde, referans tablosu yalnızca referansta görünür. Hassasiyet etkisiyle açıklanır. Seçim listeli alanın değerleri aynı sette eklenir (`add_choice`); teknik değer etiketten üretilir. Devralınan bir adla veya listede tekrarlanan bir adla alan eklenmez.
- **Gözden geçir:** sunucu kaydetmeden doğrular: `POST /api/v1/schema/change-sets/validate`. Bu, kayıttaki doğrulamayla aynıdır. Ekranda yapılacaklar ve etkileri listelenir. "Oluştur" taslağı kaydeder; "Hemen onaya gönder" seçiliyse onaya da gönderir.
- Hazırlayan yine onaylayamaz. Yayın ve dağıtım akışı değişmez.
- Genel amaçlı pencere "Değişiklik seti hazırla (gelişmiş)" adıyla kalır. Alan değiştirme ve kullanımdan kaldırma gibi seyrek işler içindir.

### 3. Entegrasyon sihirbazı: tanım saklanmaz, kurulum paketi üretilir

Altı adım vardır:

1. **Kaynak türü:** bağlayıcı kataloğundan seçilir. Her seçenekte rol, kimlik türü, veri kaynağının yaşam döngüsü ve mevcut örnekler görünür. Yarım kalan kurulumlar da listelenir: kimliği verilmiş ama çalıştırıcıda görünmeyen örnekler.
2. **Bağlantı:** örnek adı, kaynak adresi, kimlik türüne göre alanlar, CA ve kaynağa özgü seçenekler. Sır alanlarına **değer değil, kasadaki dosya yolu** yazılır.
3. **Zamanlama:** seçenekler şunlardır:
   - kaynağın varsayılanı;
   - hazır sıklıklar (her saat, iki saatte bir, her gece);
   - özel cron;
   - yalnızca elle.

   Hazır sıklıklarda dakika örnek adından türetilir; zamanlamalar saatin başına yığılmaz. Çalışma türü (tam liste veya yalnızca değişenler) ve süre sınırı da bu adımda seçilir.
4. **Geçit kimliği:** mevcut kimlik verme ucuyla verilir (ADR-0021 §5). Belirteç yalnızca bu adımda görünür ve adımdan çıkınca pencereden silinir. Bu ad için kimlik varsa yeniden verilmez.
5. **Kurulum paketi:** `POST /api/v1/integrations/plan` tanımı doğrular ve çalıştırıcı örnek dosyasına eklenecek YAML bloğunu üretir. Pakette ayrıca kasaya konacak dosyaların listesi ve sonraki iki çalışma zamanı vardır. Blok kopyalanır veya indirilir.
6. **Doğrulama:** `GET /api/v1/integrations/{ad}` adımları türetir: veri kaynağı, kimlik, çalıştırıcının örneği bildirmesi, son çalışma. Pencere beş saniyede bir yenilenir. Örnek bildirilince "Deneme çalışması" mevcut çalıştırma isteği ucuyla başlatılır.

Güvenlik kararları:

- **Doğrulama tektir.** Örnek tanımı kuralları (`parse_instance`) çalıştırıcıdan çekirdeğe taşındı: `evren_core/connectors/instances.py`. Çalıştırıcı dosyayı yüklerken dosyaların varlığını da denetler. Sihirbaz yalnızca biçimi denetler.
- **Sır değeri hiç istenmez.** API modelinde sır alanı yoktur (`extra="forbid"`). Doğrulayıcı `password`, `token`, `secret` gibi anahtarları reddeder. Dosya yolu mutlak olmalıdır ve `..` içeremez. Adres https olmalıdır.
- **Tanım saklanmaz.** Plan ucu adres ve dosya yollarını yalnızca istek süresince görür. Veritabanına, günlüğe veya tekrar güvenliği kaydına yazmaz. Örneğin varlığı ancak çalıştırıcının bildirmesiyle görünür (ADR-0021 §2 aynen geçerli).
- **Yetki:** bütün uçlar `data_source/connector_admin` ister (Veri sorumlusu); okuyucu 403 alır.

### 4. Yönetim panelinde

| Grup | Görev | Açtığı sihirbaz |
|---|---|---|
| Veri modeli | Yeni sınıf ekle | Sınıf sihirbazı |
| Veri modeli | Sınıfa alan ekle | Alan sihirbazı |
| Entegrasyonlar | Yeni entegrasyon kur | Entegrasyon sihirbazı |

Görevlerin kalıcı adresleri vardır: `#/yonetim/sinif`, `#/yonetim/alan`, `#/yonetim/yeni-entegrasyon`.

## Sonuçlar

- Sınıf ve alan eklemek ile yeni entegrasyon kurmak panelden, belge okumadan, adım adım yapılır. Hata kayıttan önce yakalanır.
- Çalıştırıcıya erişim gerektiren iki adım panelden yapılamaz, sihirbaz bunları paketler ve yol gösterir:
  - sırların kasaya konması;
  - bloğun çalıştırıcıya eklenmesi.

  Bunlar kurum sır kasası ve dağıtım süreciyle yapılır.
- Çalıştırıcı ve API aynı doğrulayıcıyı kullandığı için sihirbazın kabul ettiği blok çalıştırıcıda da kabul edilir (testli: blok çalıştırıcı kurallarıyla geri okunur).

## Bilinen sınırlar

- Yeni bir ürün (katalogda olmayan bağlayıcı) sihirbazla eklenmez. Önce bağlayıcı geliştirilir (istemci ve eşleme dosyası; `/yeni-baglayici`), sonra sihirbazla kurulur. Veri kaynağı yaşam döngüsü (gölge → etkin) ayrı değişiklik setidir.
- Çalıştırıcı dosyasını otomatik güncelleyen bir akış yok: hedef ortamın dağıtım biçimi (GitOps, yapılandırma yönetimi) belli olunca bağlanabilir (U10).
- Sihirbaz bir sınıf için kimlik kuralı veya izinli ilişki kuralı tanımlamaz. Yeni sınıf üst sınıfın kuralını devralır; ilişki kuralları F1-22'dedir.
- Testler sentetik veriyle yapıldı. Çalıştırıcının bildirmesi testlerde veritabanına doğrudan yazılarak taklit edildi; gerçek çalıştırıcıyla akış yığın testinde ayrıca sınanır.
