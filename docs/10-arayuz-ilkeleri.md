# 10 · Arayüz ilkeleri

Hedef: konfigürasyon analistinin ve CI sahibinin işini hızlandıran, kendini açıklayan ve Türkçe bir arayüz. KS GRC ile aynı görsel dil kullanılır; iki uygulama ileride tek uygulama gibi davranabilir.

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

## Ekranlar ve yol haritasındaki yeri

| Ekran | Faz | Öz |
| --- | --- | --- |
| Genel Bakış | F0 ✓ | Durum, bekleyen kararlar, rehber sayfalar |
| Mimari Harita | F0 ✓ | Bölümlü harita, arama, detay paneli, yol haritası bağlantısı, SVG indirme |
| Yol Haritası | F0 ✓ | Fazlar, ilerleme, filtre, çıkış kriterleri, haritaya bağlantı |
| Eşleştirme kuyruğu | F0 | Yan yana karşılaştırma, kanıtlar, kabul/ret/yeni CI, toplu karar (aynı kural kümesi) |
| Veri kaynakları | F0 | Son çalışmalar, kayıt/değişim/hata trendi, devre kesici durumu ve gerekçesi |
| Konfigürasyon öğeleri (liste + CI 360°) | F1 | Küresel arama (Ctrl+K), filtre, CI ekranı: öznitelik + kaynak, ilişki grafiği, zaman çizgisi (değişiklik + Change), kaynak kapsamı rozetleri |
| CMDB sağlığı | F1 | Tamlık, doğruluk (mükerrer, yetim, eski), uyum, kapsam; sınıf ve destek grubu kırılımı |
| Şema ve yetkiler | F1 | Sınıf ağacı, alan düzenleyici, değişiklik setleri; rol × kaynak × işlem matrisi |
| Kurallar | F1 | Tanımlama ve uzlaştırma kuralları (öznitelik × kaynak öncelik ızgarası), etki önizlemesi |

## Teknik yaklaşım

- Derleme adımı yok. ES modülleri, Web API'leri; üçüncü taraf çalışma zamanı kodu yok (ADR-0006).
- `lib/dom.js` güvenli eleman üretimi sağlar. Metin her zaman `textContent` ile yazılır; `innerHTML` yasaktır.
- Grafikler satır içi SVG ile çizilir: mimari harita, ilerleme çubukları, ileride ilişki grafiği.
- Durum yönetimi sayfa başına yereldir. Paylaşılan veriler `fetch` ve kısa ömürlü önbellek üzerinden gelir.
- Her ekran için Playwright duman testi yazılır. Konsol hatası ve CSP ihlali testi başarısız kılar.

## İçerik kuralları

- Etiketler sade ve Türkçe: "Konfigürasyon öğesi", "Eşleştirme kuyruğu", "Kaynak", "Uzlaştırma".
- ServiceNow terimleri parantez içinde ilk kullanımda verilir: "Tanımlama kuralı (IRE identification rule)".
- Tarihler `GG.AA.YYYY SS:dd`, sayılar Türkçe biçimde (`%12`, `1.250`).
