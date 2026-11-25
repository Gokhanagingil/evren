# ADR-0006: Derlemesiz arayüz; mimari harita ve yol haritası uygulama içinde

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

- Proje sahibi mimari çizimin ve yol haritasının uygulamanın içinde olmasını istedi; KS GRC'deki Mimari Harita düzeninde.
- Platform güvenlik öncelikli olacak.
- KS GRC ve tedarik portalı derlemesiz tarayıcı istemcisi kullanıyor.

## Karar

1. Arayüz derleme adımı olmadan yazılır: ES modülleri ve Web API'leri. Üçüncü taraf çalışma zamanı JavaScript'i yoktur.
2. CSP: `default-src 'none'; script-src 'self'; style-src 'self'`. `innerHTML`, `eval`, satır içi betik ve stil yasaktır; testlerle ve CI'da denetlenir.
3. Mimari harita tek kaynaktan (`architecture.yaml`) SVG ve JSON olarak üretilir. Metin taşması, çakışan kutu ve tanımsız ok derlemeyi durdurur.
4. Yol haritasının tek kaynağı `roadmap.json`'dur. `DURUM.md` ile birlikte güncellenir; testler tutarlılığı denetler.
5. Görsel dil KS GRC ile aynıdır.

## Değerlendirilen seçenekler

**React/TypeScript + Vite:** zengin bileşen ekosistemi sunar. Ancak npm tedarik zinciri ve derleme hattı ekler; KS GRC ile ayrışır. Şu ölçütlerden biri aşılırsa yeniden değerlendirilir:

- İlişki grafiği 5.000 düğümün üzerinde akıcı çizilemezse.
- Bileşen karmaşıklığı ekip verimini belirgin düşürürse.

## Sonuçlar

- **Olumlu:** Tedarik zinciri yüzeyi minimum; CSP sıkı; KS GRC ile tek uygulama görünümü kolay.
- **Olumsuz:** Karmaşık bileşenler (sanal liste, grafik düzeni) kurum içinde yazılır ve test edilir.
