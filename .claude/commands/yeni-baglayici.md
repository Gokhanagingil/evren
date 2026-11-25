---
description: Yeni bir veri kaynağı / bağlayıcı ekle
argument-hint: <kaynak-adı>
---
`$ARGUMENTS` kaynağı için bağlayıcı ekle. Sıra:

1. **Kaynağı tanımla.** `metadata/data_sources.yaml`'a ekle: ad, `label_tr`, `kind`, zamanlama, yetkili olduğu alan.
2. **Eşleme dosyası.** `connectors/<kaynak>/mapping.yaml`:
   - Alanlar ServiceNow adlarıyla.
   - Sınıf kuralları (`class_rules`) ve kapsam (`include_when`).
   - `lookup` ve `external_ids`; ilişkiler.
3. **Örnek veri.** Gerçek API çıktısı biçiminde, **sentetik** bir örnek: `packages/evren-core/tests/fixtures/<kaynak>_*.json`. Gerçek hostname, IP veya kişi verisi kullanma.
4. **Testler.** `tests/test_mapping.py`'a ekle:
   - Eşleme sonucu doğru mu?
   - Kapsam dışı kayıt `None` dönüyor mu?
   - Diğer kaynaklarla uçtan uca tek CI oluşuyor mu?
5. **Uzlaştırma.** `metadata/reconciliation_rules.yaml`'da kaynağın öncelikleri tanımlı mı? Yönetişim alanlarına yazamadığını doğrula.
6. **Bağlayıcı kodu.** `evren_core.connectors.Connector` sözleşmesiyle:
   - Sayfalama tamamlanmadan `completed=True` verme.
   - Kimlik bilgisi dosyadan veya sır kasasından okunur.
   - Yalnızca salt okunur API çağrıları yapılır.
7. **Devre kesici.** `ingest.data_source.breaker_thresholds` için başlangıç değerlerini belirle.
8. **Harita ve yol haritası.** `architecture.yaml` ile `roadmap.json`'u güncelle; `python tools/build_architecture.py`.
9. **Belge.** `docs/04-kaynaklar.md`'ye kaynak bölümünü ekle: veri, API, tanımlayıcılar, tuzaklar.
10. Tüm testleri çalıştır; ardından `/durum`.
