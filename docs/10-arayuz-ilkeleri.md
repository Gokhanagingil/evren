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
9. **Yardım katmanlıdır.** Kılavuzun ilk sayfası kısa ve yönlendiricidir (en fazla 260 sözcük); her konu tek bir işi anlatır (en fazla 900 sözcük) ve teknik ayrıntıya bağlantı verir. Her sayfanın yardım paneli (?) kılavuzun ilgili konusunu açar.

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
| Sistem durumu | F1 ✓ | Veritabanı, göçler, Kafka, OpenSearch, servisler, olay yayını, kuyruklar, kaynak çalışmaları (ADR-0032) |

◐: ekran önizlemede sentetik veriyle çalışır (`tools/build_demo.py`, evren-core hattından üretilir); kalıcı işlemler API (F0-09) ve uzlaştırıcı (F0-10) ile gelir.

## Kabuk (GRC Platformu ile aynı)

- Beyaz, gruplu ve daraltılabilir kenar menü; kırmızı vurgu; etkin sayfada sol çizgi.
- Üst çubukta sayfa izi, "Sentetik demo verisi" işareti, **Ctrl+K** arama paleti (CI, kaynak, kuyruk kaydı, sayfa) ve **?** yardım.
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
