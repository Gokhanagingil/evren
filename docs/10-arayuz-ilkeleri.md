# 10 · Arayüz ilkeleri

Hedef: konfigürasyon analistinin ve CI sahibinin işini hızlandıran, kendini açıklayan ve Türkçe bir arayüz. GRC Platformu ile aynı görsel dil kullanılır; iki uygulama ileride tek uygulama gibi davranabilir.

## İlkeler

1. **Her değerin kaynağı görünür.** CI ekranında her özniteliğin yanında kaynak rozeti (vCenter, BMC…) bulunur. Üzerine gelince gözlem zamanı, uygulanan kural ve ezilen değerler görünür.
2. **Karar bekleyen iş önce gelir.** Eşleştirme kuyruğu, anomaliler ve eskime önerileri genel bakışta sayı ve öncelikle gösterilir.
3. **Açıklama olmadan otomasyon yok.** Otomatik veya önerilen her eşleşme Türkçe kanıt listesiyle gelir.
4. **Geri alınabilirlik görünür.** Birleştir/ayır, kural değişikliği ve şema yayını için "ne değişecek" önizlemesi gösterilir.
5. **Klavye ve erişilebilirlik:**
   - Tüm etkileşimler klavyeyle yapılabilir; Enter seçer, Esc kapatır.
   - Odak görünürdür ve `aria-*` etiketleri vardır.
   - WCAG 2.2 AA kontrastı.
   - Mobilde yatay taşma olmaz.
6. **Performans:**
   - Listeler sunucu taraflı sayfalanır ve sanal kaydırılır.
   - Arama 300 ms altında yanıt verir (OpenSearch).
   - İlk ekran 1 saniyenin altında açılır.
7. **Güvenlik görünmez değil, varsayılandır.**
   - Kısıtlı alanlar yetkisi olmayana hiç gönderilmez; arayüzde gizlemek yeterli sayılmaz.
   - Dış kaynak yüklenmez.
8. **Yönetim görev dilinde, tek merkezden** (ADR-0033):
   - Yönetim paneli sayfa adlarını değil işleri listeler ("Yeni CI ekle", "Sınıfa alan ekle"); her kartta tek eylem ve "Nasıl yapılır?" bağlantısı.
   - Yapılamayan iş gizlenmez: hangi rolün gerektiği yazılır. Henüz hazır olmayan iş "Hazırlanıyor" diye görünür.
   - Kişinin karar vermesi gereken işler ("Size düşen işler") panelin en üstündedir ve ilgili ekrana götürür.
9. **Sadelik ve aşamalı açıklama** (ADR-0041):
   - İlk bakışta yalnızca karar için gereken görünür; ayrıntı katlanır alanda, yardım panelinde (?) veya kılavuzdadır.
   - Alt başlık tek cümledir. Bilgi bir kez gösterilir.
   - Uzun listeler önce en acil dört öğeyi gösterir ("Tümünü göster (n)").
   - Süzgeçler tek satırdadır.
   - Koşullu öğe asla "null" metnine dönüşmez (`append` yardımcısı). Katlanır alan başlığı Türkçedir ve boş alan gizlenir.
10. **Yardım katmanlıdır.** Kılavuzun ilk sayfası kısa ve yönlendiricidir (en fazla 260 sözcük); her konu tek bir işi anlatır (en fazla 900 sözcük) ve teknik ayrıntıya bağlantı verir. Her sayfanın yardım paneli (?) kılavuzun ilgili konusunu açar.

## Ekranlar ve yol haritasındaki yeri

| Ekran | Faz | Öz |
| --- | --- | --- |
| Genel Bakış | F0 ✓ | Karar bekleyenler, kapsam, kaynak sağlığı, GRC Platformu'na giden kontroller |
| Eşleştirme kuyruğu | F0 ◐ | Yan yana karşılaştırma, kanıtlar (log-odds), birleştir/yeni CI/ertele; önizlemede kararlar oturumluk, kalıcı kayıt API ile |
| Konfigürasyon öğeleri (liste + CI 360°) | F0 ◐ | Arama, sınıf/kaynak/kapsam filtresi; öznitelik başına kaynak, kural gerekçesi, ezilen değerler, yetkisiz deneme; ilişkiler; kaynak kayıtları |
| GRC Kontrolleri | F0 ◐ | Kontrol sonucu, provenance, kanıt; kısmi toplamada "kesin değil" |
| Veri kaynakları | F0 ✓ | Rol → ürün, yaşam döngüsü, devre kesici ve gerekçesi, çalışma geçmişi, ürün değişimi yordamı |
| Kurallar | F0 ✓ | Uzlaştırma (sınıf × rol öncelik ızgarası), tanımlama (IRE), veri tazeleme ve eskime |
| Büyük Resim | F0 ✓ | Tek sayfalık mimari özet |
| Mimari Harita | F0 ✓ | Bölümlü harita, arama, detay paneli, yol haritası bağlantısı, SVG indirme |
| Yol Haritası | F0 ✓ | Fazlar, adımlar, "sizden gereken", çıkış kriterleri |
| CMDB sağlığı | F1 | Tamlık, doğruluk (mükerrer, yetim, eski), uyum, kapsam; sınıf ve destek grubu kırılımı |
| Şema ve yetkiler | F1 | Sınıf ağacı, alan düzenleyici, değişiklik setleri; rol × kaynak × işlem matrisi |
| Yönetim paneli | F1 ◐ | Görev odaklı tek merkez: CMDB kayıtları, veri modeli, kullanıcılar ve yetkiler, entegrasyonlar, veri kalitesi, işletim; "Size düşen işler"; kalıcı görev adresi `#/yonetim/<görev>` (U8.2; kullanıcı ve rol görevleri U8.3) |
| Kullanım kılavuzu ve teknik belge | F1 ✓ | Katmanlı kılavuz (`#/kilavuz/<konu>`; konu arama, "Bu sayfada", önceki/sonraki); teknik belge ve ADR'ler uygulama içinde (`#/belge/<ad>`, oturumla) |
| Görevler | F1 ◐ | Bana atanan görevler ve kararı; Konfigürasyon yöneticisi için tümü, atanmamış, dikkat, tek seferlik görev açma ve kanal ayarı (ADR-0035); Kampanyalar sekmesi: önizlemeli tanım, etkinleştir/duraklat/emekliye ayır, şimdi çalıştır, çalışmalar, kampanyanın görevleri (ADR-0036); kural görevinde bulgu ve kararın etkisi (ADR-0037) |
| Sihirbazlar | F1 ✓ | Ortak adım adım pencere (adım göstergesi sabit, Geri/İleri, Enter/Esc, odak, hata alanda); Yeni sınıf, Sınıfa alan (aranabilir sınıf ağacı, devralınan alanlar, kimlik kuralı, teknik ad önerisi, seçim değerleri, kaydetmeden doğrulama), Yeni entegrasyon (kaynak türü, bağlantı, zamanlama, geçit kimliği, kurulum paketi, canlı doğrulama ve deneme çalışması) (ADR-0038) |
| CMDB Sağlığı | F1 ✓ | Genel puan ve derece, dört boyut kartı, boyut ölçümleri (ölçek listesi), genel puanın 30 günlük eğilimi (gerçek genişlikte çizilir, ok tuşlarıyla gezilir, tablo görünümü); Kurallar: şablondan başlatılan önizlemeli tanım, etkinleştir/duraklat/emekliye ayır, şimdi değerlendir, değerlendirmeler; Bulgular: neden, önem, görev, istisna ve yeniden açma; CI sayfasında "Kalite bulguları" (ADR-0037) |
| Kullanıcılar ve roller | F1 ✓ | Kişiler (arama, role göre süzme, sağ panelde roller, gruplar, yetki geçmişi), gruplar, rol–yetki matrisi (kalıtılan izinler soluk, birleşemeyen roller, rolü taşıyanlar), yetki geçmişi; rol ver/geri al gerekçeli, yetkili rol süreli, çatışma önceden uyarılır (ADR-0034) |
| Sistem durumu | F1 ✓ | Veritabanı, göçler, Kafka, OpenSearch, servisler, olay yayını, kuyruklar, kaynak çalışmaları (ADR-0032) |

◐: ekran önizlemede sentetik veriyle çalışır (`tools/build_demo.py`, evren-core hattından üretilir); kalıcı işlemler API (F0-09) ve uzlaştırıcı (F0-10) ile gelir.

## Kabuk (ADR-0041; görsel dil GRC Platformu ile aynı)

- **Uygulama çubuğu** (80 px): altı bölüm, her biri simge ve kısa adla. Bölümler: Ana sayfa, İşlerim, CMDB, Kaynaklar, Yönetim, Kılavuz. Bekleyen eşleştirme sayısı İşlerim'de rozettir. Kırmızı vurgu kullanılır; etkin bölümde sol çizgi vardır.
- **Bölüm sekmeleri** üst çubuktadır. Etkin sekme `aria-current="page"` taşır. Bölüm son açılan sekmeyi hatırlar. Sayfa izi yoktur.
- **Hesap paneli** çubuğun altındaki baş harf düğmesindedir: ad, roller ve Çıkış. Esc kapatır.
- **Mobilde** (< 860 px) çubuk alt gezinme çubuğuna dönüşür; sekmeler üst çubuğun ikinci satırına iner.
- Üst çubukta ayrıca "Sentetik demo verisi" işareti, **Ctrl+K** arama paleti (CI, kaynak, kuyruk kaydı, sayfa), bildirim zili ve **?** yardım bulunur.
- Sayfa başlığı + açıklama + eylemler; metrik kartları sol renk şeridiyle durumu gösterir.
- Kayıt ayrıntısı **sağdan açılan panelde**; Esc kapatır, odak açan satıra döner; derin bağlantı (`#/ci/<sys_id>`, `#/kuyruk/<id>`, `#/kaynaklar/<ad>`).

## Teknik yaklaşım

- Derleme adımı yok. ES modülleri, Web API'leri; üçüncü taraf çalışma zamanı kodu yok (ADR-0006).
- `lib/dom.js` güvenli eleman üretimi sağlar. Metin her zaman `textContent` ile yazılır; `innerHTML` yasaktır.
- Grafikler satır içi SVG ile çizilir: mimari harita, ilerleme çubukları, ileride ilişki grafiği.
- Durum yönetimi sayfa başına yereldir. Paylaşılan veriler `fetch` ve kısa ömürlü önbellek üzerinden gelir.
- Her ekran için Playwright duman testi yazılır. Konsol hatası ve CSP ihlali testi başarısız kılar. Mobil (390 px) yatay taşma tüm ekranlarda denetlenir.

## İçerik kuralları

- Kılavuz metni `docs/kilavuz/` altındadır ve kodla birlikte gözden geçirilir. Düğme ve sayfa adları arayüzdekiyle birebir aynı yazılır; bir etiket değişirse kılavuz da değişir.

- Etiketler sade ve Türkçe: "Konfigürasyon öğesi", "Eşleştirme kuyruğu", "Kaynak", "Uzlaştırma".
- ServiceNow terimleri parantez içinde ilk kullanımda verilir: "Tanımlama kuralı (IRE identification rule)".
- Tarihler `GG.AA.YYYY SS:dd`, sayılar Türkçe biçimde (`%12`, `1.250`).
