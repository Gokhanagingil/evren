# ADR-0033: Yönetim paneli tek merkez; kullanım kılavuzu katmanlı ve tek kaynaktan

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (U8.2), mimari

## Bağlam

Yönetim işleri arayüzde birçok sayfaya dağılmıştı:

- CI oluşturma CI listesindeydi.
- Sınıf ve alan ekleme Şema sayfasındaydı.
- Kimlik verme Veri Kaynakları sayfasındaydı.
- Eskime ve sahiplik onayı ayrı sayfalardaydı.

Bir işin nerede yapıldığını bilmeyen kullanıcı menüde aramak zorundaydı. Bir işi yapamadığında da nedenini göremiyordu: düğme yoktu, açıklama da yoktu.

Kullanım kılavuzu yoktu. Teknik belgeler (`docs/`) depodaydı ve uygulama içinden okunamıyordu.

Proje sahibinin beklentisi şuydu:

- Yönetim ekranları kolay ve yönlendirici olmalı.
- Hemen her işlem tek panelden yapılabilmeli: yeni CI, sınıf ve alan ekleme, kullanıcı, entegrasyon.
- Kılavuz önce genel bilgi vermeli. Teknik ayrıntıya bağlantıyla gidilmeli. İlk sayfada her şey anlatılıp okuyucu kaybedilmemeli.

## Karar

### 1. Yönetim paneli görev odaklıdır (`#/yonetim`)

Panel sayfa listesi değil, **görev listesidir**: "Yeni CI ekle", "Sınıfa alan ekle", "Bağlayıcıya kimlik ver" gibi. Görevler işin konusuna göre gruplanır:

- CMDB kayıtları
- Veri modeli
- Kullanıcılar ve yetkiler
- Entegrasyonlar
- Yönetişim görevleri (27.11.2026'da eklendi; zamanlanmış ve kurala bağlı görevler, plan F1-24 … F1-26)
- Veri kalitesi
- İşletim

Her kartta şunlar bulunur:

- Tek cümlelik açıklama.
- Tek eylem düğmesi.
- "Nasıl yapılır?" bağlantısı; kılavuzun ilgili konusunu açar.

Mümkün olan işler paneli bırakmadan, **pencere içinde** yapılır:

- yeni CI ve CSV ile toplu içe aktarma,
- ilişki ekleme (önce CI seçilir),
- yeni sınıf, sınıfa alan, seçim listesine değer,
- yeni veri kaynağı ve kaynak yaşam döngüsü (gölge, etkin, emekli).

Veri modeli ve kaynak işleri değişiklik seti penceresini doğrudan ilgili işlemle açar; taslak, onay ve yayın kuralı değişmez. Ayrıntılı ekran gerektiren işler ilgili sayfaya götürür: eşleştirme kuyruğu, kimlik verme, şimdi çalıştırma, eskime.

Her görevin kalıcı adresi vardır (`#/yonetim/<görev>`). Kılavuz ve başka sayfalar doğrudan göreve bağlanır; adres açılınca kart odaklanır, pencere içi görev başlar. Görev araması yazarken süzer, Enter ilk görevin düğmesine götürür.

### 2. Yetkisi olmayan görev gizlenmez, nedeniyle gösterilir

Kullanıcının izni olmayan görev kilitli görünür ve hangi rolün gerektiği yazılır (ör. "Bu iş için Veri sorumlusu rolü gerekir."). Böylece kullanıcı kimden yetki isteyeceğini bilir.

Yetkiler `/api/v1/me` → `can` alanından okunur. Bu ADR ile `can` alanına şu yetenekler eklendi: `relate`, `schema_draft`, `schema_approve`, `connector_read`, `connector_admin`, `anomaly_review`, `iam_admin`, `audit_read`.

Arayüzdeki kilit yalnızca yönlendirmedir. Asıl denetim API'de ve veritabanı rollerindedir; bunlar değişmedi.

Henüz hazır olmayan görevler "Hazırlanıyor" etiketiyle durur: kullanıcı ekleme, rol–yetki matrisi, yönetişim görevleri (zamanlanmış görev, kuraldan görev, görev izleme, görev kanalı), denetim izi, sağlık panosu. Yol haritasındaki adımları U8.3 ile U8.8 arasındadır. Bu sayede panel, ürünün tamamlandığında neye benzeyeceğini dürüstçe gösterir.

### 3. "Size düşen işler" tek uç noktadan gelir

`GET /api/v1/admin/attention`, kişinin karar vermesi gereken bekleyen işleri döndürür. Yalnızca kişinin yetkisi olan işler listelenir:

- Eşleştirme kuyruğu.
- Onay bekleyen değişiklik setleri (hazırlayanın kendi seti hariç).
- Yayın bekleyen ve taslakta kalan setler.
- Emekliye ayırma adayları.
- İncelenmemiş yetkisiz değişiklikler.
- Süresi 14 gün içinde dolacak bağlayıcı kimlikleri.
- Devre kesicisi açık kaynaklar.
- Kişinin onayını bekleyen CI'lar.
- Sistem durumu.

Liste önem sırasına göre dizilir: sorun, dikkat, bilgi. Her satır ilgili ekrana götürür.

Uç nokta salt okunurdur. Yeni tablo ya da izin gerektirmez, var olan izinli görünümleri kullanır. Örneğin bağlayıcı kimliklerinde belirteç özeti olmayan `connector_credential_status` görünümü okunur.

### 4. Kılavuz katmanlıdır ve tek kaynaktan üretilir

- **Kaynak:** `docs/kilavuz/kilavuz.yaml` ve konu başına bir Markdown dosyası.
- **Üretim:** `python tools/build_guide.py` şu dosyayı üretir: `apps/web/content/guide.json`.
- **Uygulama içi adres:** "Kullanım Kılavuzu" sayfası (`#/kilavuz/<konu>`).
- **İçerik zamanlaması (proje sahibi kararı, 27.11.2026):** altyapı şimdi kurulur; içeriğin son hâli, ekranlar değişebileceği için en sonda yazılır (plan F1-27). O zamana kadar `kilavuz.yaml` → `draft: true` ile her konunun başında "Taslak" notu görünür; yeni ekran için yalnızca konu iskeleti ve sayfa bağı eklenir.
- **Başlangıç sayfası** en fazla 260 sözcüktür. Evren'in ne olduğunu, üç temel kavramı ve "ne yapmak istiyorsunuz?" yönlendirmesini verir.
- **Konu sayfaları** en fazla 900 sözcüktür. Her konunun yanında "Teknik ayrıntı" bağlantıları vardır.
- **Üretici denetimleri.** Üretici aşağıdaki durumlardan biri varsa reddeder; CI'da `--check` ile sınanır:
  - sözcük sınırı aşılmışsa,
  - kırık konu, belge veya arayüz bağlantısı varsa,
  - başlangıçtan ulaşılamayan konu varsa,
  - bir arayüz yolu iki konuya bağlanmışsa.
- **Bağlantı biçimleri:**
  - `kilavuz:<konu>`
  - `belge:<ad>` (teknik belge veya ADR)
  - `#/<arayüz yolu>`
- **Yardım paneli:** her sayfadaki yardım paneli (F1 / ?), o sayfanın kılavuz konusuna "Kılavuzda aç" bağlantısı verir (`route_topics`).

### 5. Teknik belgeler uygulama içinde, oturumla

`GET /api/v1/docs` ve `GET /api/v1/docs/{ad}`, `docs/*.md` ve `docs/adr/*.md` dosyalarını Markdown olarak döndürür. Arayüzde "Teknik belge" sayfası (`#/belge/<ad>`) bunları gösterir.

- Oturum gerekir. Mimari ve güvenlik tasarımı oturumsuz erişime açılmaz.
- Ad yalnızca izinli biçimde kabul edilir: `^(?:adr/)?[0-9A-Za-z][0-9A-Za-z.-]{0,120}$`, `..` yasak. Ad `docs/` altında çözülür; dizin dışına çıkılamaz.
- İmaja `docs/` eklenir (`deploy/Dockerfile`, `.dockerignore`).
- Demo modunda (statik arayüz) teknik belge yoktur. Sayfa bunu açıkça söyler.

### 6. Markdown güvenli işleyiciyle DOM'a çevrilir

`apps/web/assets/js/lib/markdown.js` HTML dizgesi üretmez. Her metin `textContent` ile, her öğe `createElement` ile yazılır. Kuraldaki `innerHTML` yasağı bu yolla korunur.

Desteklenen yapılar: başlıklar, listeler (bir alt düzeye kadar), tablolar, kod bloğu, not kutusu, satır içi kod, kalın ve eğik yazı, bağlantılar.

Bağlantılar şöyle işlenir:

- Desteklenen hedefler (kılavuz konusu, teknik belge, arayüz yolu, belgeler arası göreli `.md`) arayüz adresine çevrilir.
- `https://` adresleri tıklanmaz; okunabilir metin olarak gösterilir. Arayüz dışarıya bağlantı vermez, CSP değişmedi.
- Başka her hedef düz metin olur.

## Sonuçlar

- Yeni gelen bir kullanıcı tek sayfadan işleri bulur, yapamadığı işin nedenini görür ve bir tıkla nasıl yapılacağını okur.
- Kılavuz kodla birlikte gözden geçirilir ve sürümlenir. Bağlantı ve uzunluk kuralları CI'da denetlendiği için kılavuz dağılmaz.
- Görevler eklendikçe panel büyür: U8.3 kullanıcılar ve roller, U8.4–U8.6 yönetişim görevleri ve sağlık panosu, U8.7 sihirbazlar, U8.8 denetim izi. "Hazırlanıyor" kartları adım tamamlandıkça açılır.
- Kılavuz şimdilik yalnızca Türkçedir. Ekran görüntüsü ve video içermez; bunlar hedef ortam ve gerçek veriyle hazırlanmalıdır (U10).
- Teknik belgelerdeki dış bağlantılar tıklanmaz. Bu bilinçli bir seçimdir: kurum içi, internete kapalı kurulumda çalışmayacak bağlantı verilmez.

## Alternatifler

- **Menüye "Yönetim" grubu ekleyip sayfaları orada toplamak:** kullanıcı yine sayfa adlarından işi tahmin etmek zorunda kalırdı ve yetki nedeni görünmezdi. Reddedildi; sayfalar menüde kalır, panel görev dilini kullanır.
- **Kılavuzu ayrı bir statik site (MkDocs vb.) olarak yayımlamak:** yeni bağımlılık ve ayrı bir yayın hattı gerekirdi; uygulama içi yardımla bağ kurulamazdı. Reddedildi.
- **Markdown için hazır kütüphane (marked, markdown-it) ve sanitizer (DOMPurify):** hem yeni bağımlılık eklerdi hem de `innerHTML` yolunu açardı. Bizim gereksinimimiz küçük bir alt küme olduğu için reddedildi.
- **Teknik belgeleri oturumsuz statik dosya olarak sunmak:** güvenlik tasarımı ve tehdit modeli oturumsuz okunabilirdi. Reddedildi.
