---
description: Yeni bir veri kaynağı / bağlayıcı ekle
argument-hint: <kaynak-adı>
---
`$ARGUMENTS` kaynağı için bağlayıcı ekle. Sıra:

1. **Kaynağı tanımla.** `metadata/data_sources.yaml`'a ekle: ad, `label_tr`, `role` (mevcut bir kaynak rolü; gerekirse yeni rol), `role_rank`, `lifecycle` (ilk devreye alımda `shadow`), `product`, `connector`, `mappings`, zamanlama. Mevcut bir ürünün yerini alıyorsa ADR-0009'daki ürün değişimi yordamını izle.
2. **Eşleme dosyası.** `connectors/<bağlayıcı>/<nesne>.mapping.yaml` (klasör adı ürünü adlandırır, ör. `bmc_discovery`):
   - Alanlar ServiceNow adlarıyla.
   - Sınıf kuralları (`class_rules`) ve kapsam (`include_when`).
   - `lookup` ve `external_ids`; ilişkiler.
3. **Örnek veri.** Gerçek API çıktısı biçiminde, **sentetik** bir örnek: `packages/evren-core/tests/fixtures/<kaynak>_*.json`. Gerçek hostname, IP veya kişi verisi kullanma.
4. **Testler.** `tests/test_mapping.py`'a ekle:
   - Eşleme sonucu doğru mu?
   - Kapsam dışı kayıt `None` dönüyor mu?
   - Diğer kaynaklarla uçtan uca tek CI oluşuyor mu?
5. **Uzlaştırma.** Kurallar **role** yazılır; ürün adı kurala girmez. Mevcut rolü dolduruyorsa `reconciliation_rules.yaml` değişmez. Yeni rol eklendiyse öncelikleri tanımla ve yönetişim alanlarına yazamadığını doğrula.
6. **Bağlayıcı kodu.** `evren_core.connectors.Connector` sözleşmesiyle:
   - Sayfalama tamamlanmadan `completed=True` verme.
   - Kimlik bilgisi dosyadan veya sır kasasından okunur.
   - Yalnızca salt okunur API çağrıları yapılır.
7. **Devre kesici.** `ingest.data_source.breaker_thresholds` için başlangıç değerlerini belirle.
8. **Harita ve yol haritası.** `architecture.yaml` ile `roadmap.json`'u güncelle; `python tools/build_architecture.py`.
9. **Belge.** `docs/04-kaynaklar.md`'ye kaynak bölümünü ekle: veri, API, tanımlayıcılar, tuzaklar.
10. Tüm testleri çalıştır; ardından `/durum`.
