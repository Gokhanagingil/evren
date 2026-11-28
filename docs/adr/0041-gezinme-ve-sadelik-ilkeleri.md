# ADR-0041: Gezinme ve sadelik ilkeleri — uygulama çubuğu, bölüm sekmeleri, aşamalı açıklama

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (kararı UX gözden geçirmesine bıraktı; U8.10, F1-28), arayüz

## Bağlam

Evren 22 ekrana ulaştı. Proje sahibinin beklentisi açık: ekranlar basit ve kolay anlaşılır olmalı, gereksiz bilgi veya tık olmamalı, her bilgi ilk bakışta verilmemeli. Sol menünün sürekli yer kaplaması yerine daha modern bir yapı kurulabilir.

Bütün ekranlar 1440 px ve 390 px genişlikte tek tek incelendi. Bulgular:

- **Kenar menü ekranın yaklaşık %18'ini sürekli kaplıyordu.** 256 px genişlikteydi; 5 grupta 22 bağlantı vardı ve gruplar daraltılabiliyordu. Kullanıcı her sayfada bütün haritayı görüyordu, oysa bir anda yalnızca bir bölümle çalışır.
- **Sayfa izi (breadcrumb) menüyü tekrarlıyordu.** Sayfalar en fazla iki düzeyliydi; iz yeni bilgi vermiyordu.
- **Alt başlıklar iki üç cümleydi.** Yöntemi ve kaynağı anlatıyorlardı; bunlar yardım panelinin (?) işidir.
- **Bilgi yinelenmesi vardı.** Örneğin Genel Bakış'ta üst çubuktaki "Sentetik demo verisi" işaretinin yanında bir de uzun demo bandı vardı.
- **İlk bakışta çok fazla kutu vardı.** Yönetim Paneli'ndeki "Size düşen işler" on kutuyu açıklamalarıyla birlikte gösteriyordu. Eskime sayfasında karar mantığı listenin altında hep açıktı.
- **Görsel kusurlar vardı.** Görevler ve Veri Modeli sayfalarında koşullu öğe boş kalınca "null" yazıyordu. İlişki haritasında boş bir katlanır alanın tarayıcı varsayılanı olan İngilizce "Details" başlığı görünüyordu. CI listesinde uzun seçenekli bir filtre süzgeçleri iki satıra taşıyordu.

## Karar

### 1. Dar uygulama çubuğu ve altı bölüm

Kenar menünün yerini 80 px genişliğinde bir **uygulama çubuğu** alır. Çubukta altı bölüm vardır; her birinin bir simgesi ve kısa bir adı bulunur:

| Bölüm | Sayfalar (sekme) |
| --- | --- |
| Ana sayfa | Genel Bakış |
| İşlerim | Görevler · Eşleştirme · Sahiplik onayı |
| CMDB | Öğeler · İlişki haritası · Sağlık · Eskime · Veri modeli |
| Kaynaklar | Veri kaynakları · Kurallar · Gölge mod · GRC kontrolleri |
| Yönetim | Panel · Kullanıcılar · Şema · Denetim izi · Sistem durumu |
| Kılavuz | Kılavuz · Büyük resim · Mimari · Yol haritası (teknik belgeler de bu bölümdedir) |

Gruplama kullanıcının niyetine göre yapılır ("işim ne", "CMDB'de ne var", "veri nereden geliyor", "platformu yönet", "öğren"), ekip yapısına göre yapılmaz. Bekleyen eşleştirme sayısı İşlerim simgesinde rozet olarak görünür.

### 2. Bölümün sayfaları üst çubukta sekme

Etkin bölümün sayfaları üst çubukta sekme olarak görünür. Etkin sekme `aria-current="page"` ile işaretlenir. Tek sayfalık bölümde (Ana sayfa) sekme satırı gizlenir.

**Her bölüm son açılan sekmeyi hatırlar.** Örneğin CMDB'de Eskime açıkken Kaynaklar'a geçip geri dönen kullanıcı yine Eskime'ye gelir. Böylece gereksiz tık kalkar.

Sayfa izi kaldırıldı. Bölüm ve sekme, kullanıcının nerede olduğunu zaten gösterir.

### 3. Hesap paneli

Kimlik kutusunun yerini uygulama çubuğunun altındaki baş harf düğmesi alır. Kutu, kullanıcının adını ve bütün rollerini sürekli gösteriyordu. Düğme paneli açar; panelde ad, roller ve (Entra oturumunda) Çıkış bulunur.

- Panel Esc ile kapanır ve odak düğmeye döner.
- Dışarı tıklamak da paneli kapatır.
- Düğme `aria-haspopup` ve `aria-expanded` taşır.

### 4. Mobilde alt çubuk

860 px'in altında uygulama çubuğu **alt gezinme çubuğuna** dönüşür ve altı bölüm her an görünür. Sekmeler üst çubuğun ikinci satırına iner ve yatay kaydırılır.

Böylece menü düğmesi ve açılır çekmece kalktı; her bölüme tek dokunuşla ulaşılır.

### 5. Aşamalı açıklama (progressive disclosure)

İlk bakışta yalnızca karar vermek için gereken gösterilir. Ayrıntı isteyen açar.

- **Alt başlık tek cümledir** ve sayfanın ne işe yaradığını söyler. Yöntem, kaynak ve kural ayrıntısı yardım panelinde (?) ve kılavuzdadır.
- **Bilgi bir kez gösterilir.** Üst çubuktaki işaretin anlattığını sayfa bandı tekrarlamaz.
- **Uzun listeler önce en acil dört öğeyi gösterir.** Örneğin Yönetim Paneli'ndeki "Size düşen işler" önem sırasıyla dizilir ve kutularda yalnızca başlık ile sayı vardır. Açıklama kutunun ipucundadır. "Tümünü göster (n)" düğmesi `aria-expanded` taşır.
- **Karar mantığı katlanır alandadır.** Örneğin Eskime'deki kural ve gözlemci kaynak ayrıntısı "Nasıl karar veriliyor?" başlığı altındadır. Katlanır alanın başlığı her zaman Türkçedir. İçi boşsa alan gizlenir.
- **Süzgeçler tek satırdadır.** Seçim kutusu en fazla 240 px genişliktedir; uzun seçenek üç noktayla kısalır.

### 6. Koşullu öğe asla metne dönüşmez

Sayfalar öğe eklemek için `lib/dom.js` içindeki `append` yardımcısını kullanır. Yardımcı `null`, `undefined` ve `false` değerlerini atlar. Yerel `Element.append` bunları "null" metnine çevirdiği için koşullu öğede kullanılmaz.

Tarayıcı taraması her ekranda ve sekmede "null", "undefined", "NaN", "[object Object]" ve "Details" metinlerini arar. Geliştirme sırasında bu tarama çalıştırılır; testler de ana ekranlarda "null" denetler.

## GRC Platformu ile görsel dil

Görsel dil korunur:

- beyaz yüzeyler ve kırmızı vurgu;
- üst çubukta Ctrl+K arama;
- sağdan açılan detay paneli;
- metrik kartlarında sol renk şeridi.

Değişen yalnızca gezinmenin biçimidir: gruplu kenar menünün yerini uygulama çubuğu ve sekmeler aldı. GRC Platformu da aynı kabuğu benimseyebilir; iki uygulama tek uygulama gibi davranacaksa bu önerilir. Karar GRC Platformu reposunda ayrıca verilir.

## Sonuçlar

- İçerik alanı masaüstünde 176 px genişledi. Tablolar ve harita daha az sarar.
- Kullanıcı bir anda yalnızca bulunduğu bölümün sayfalarını görür; bilişsel yük azalır. Bölümler arası geçiş tek tıktır.
- Bölüm başına son sekme sayfa yenilenene kadar bellekte tutulur; tarayıcı depolamasına bir şey yazılmaz.
- Yeni ekran eklerken bir bölüme ve sekme adına karar vermek gerekir (`app.js` → `HUBS`, `ROUTES[...].tab`). Bir bölümde yedi sekmeden fazlası olursa bölüm bölünür.
- Testler yeni gezinmeyi denetler: bölümler ve sekmeler klavyeyle açılır, son sekme hatırlanır, hesap paneli Esc ile kapanır, mobilde alt çubukta altı bölüm görünür ve yatay taşma yoktur.

## Değerlendirilen seçenekler

- **Daraltılabilir kenar menü (simge ↔ tam):** Her sayfada aynı 22 bağlantıyı gösterir; daraltınca etiketler kaybolur ve simgeyle 22 sayfa ayırt edilemez. Reddedildi.
- **Yalnızca üst menü (açılır menüler):** Her geçiş iki tık ister ve açılır menüler mobilde ve klavyede zayıftır. Reddedildi.
- **Yalnızca Ctrl+K komut paleti:** Uzman için hızlıdır ama keşfedilebilir değildir. Ek yol olarak korunur.
