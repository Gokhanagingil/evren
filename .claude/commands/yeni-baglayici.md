---
description: Yeni bir veri kaynağı / bağlayıcı ekle
argument-hint: <kaynak-adı>
---
`$ARGUMENTS` kaynağı için bağlayıcı ekle. Sıra:

1. **Kaynağı tanımla.** `metadata/data_sources.yaml`'a ekle: ad, `label_tr`, `role` (mevcut bir kaynak rolü; gerekirse yeni rol), `role_rank`, `lifecycle` (ilk devreye alımda `shadow`), `product`, `connector`, `mappings`, zamanlama. Mevcut bir ürünün yerini alıyorsa ADR-0009'daki ürün değişimi yordamını izle.
2. **Eşleme dosyası.** `connectors/<bağlayıcı>/<nesne>.mapping.yaml` (klasör adı ürünü adlandırır, ör. `bmc_discovery`):
   - Alanlar ServiceNow adlarıyla.
   - Sınıf kuralları (`class_rules`) ve kapsam: `require` (hepsi sağlanmalı; ör. nesne türü) ve `include_when` (en az biri).
   - `lookup` ve `external_ids`; ilişkiler.
3. **Örnek veri.** Gerçek API çıktısı biçiminde, **sentetik** bir örnek: `packages/evren-core/tests/fixtures/<kaynak>_*.json`. Gerçek hostname, IP veya kişi verisi kullanma.
4. **Testler.** `tests/test_mapping.py`'a ekle:
   - Eşleme sonucu doğru mu?
   - Kapsam dışı kayıt `None` dönüyor mu?
   - Diğer kaynaklarla uçtan uca tek CI oluşuyor mu?
5. **Uzlaştırma.** Kurallar **role** yazılır; ürün adı kurala girmez. Mevcut rolü dolduruyorsa `reconciliation_rules.yaml` değişmez. Yeni rol eklendiyse öncelikleri tanımla ve yönetişim alanlarına yazamadığını doğrula.
6. **Bağlayıcı kodu (ADR-0020).** `packages/evren-core/src/evren_core/connectors/sources/<kaynak>.py`:
   - `SourceClient`: `data_source` + `fetch(mode, since)`; ham nesneleri sayfa sayfa üretir. HTTP için `HttpClient` (standart kütüphane, TLS, yeniden deneme); yeni bağımlılık yok.
   - Sayfalamayı sonuna kadar götür; yarıda kalan çekme hata fırlatır (çalışma "tamamlanmadı" kapanır).
   - Kimlik bilgisi yalnızca dosyadan (`read_secret`); hata iletisinde sır, başlık veya sunucu gövdesi yok.
   - Yalnızca salt okunur API çağrıları; oturum açan kaynakta oturumu `finally` içinde kapat.
   - Aynı API alt kümesini sentetik örnek veriyle yanıtlayan bir **simülatör** (`Transport`) ve `connectors/<kaynak>/samples/` (içinde "SENTETİK" açıklaması).
   - `sources/__init__.py` → `SOURCES` kaydı (kimlik türü, eşleme klasörü, simülatör, seçenekler).
   - Testler: sayfalama, hata (sızıntısız), yarıda kalan çekme, kayıt/oynatma (kayıtta sır yok), eşleme; uzlaştırıcıyla PostgreSQL testi (ilişkiler, diğer kaynaklarla tek CI).
   - Dene: `python tools/connector_run.py --connector <kaynak> --simulate connectors/<kaynak>/samples --dry-run`.
   - Çalıştırıcı servisinde örnek (ADR-0021): `EVREN_CONNECTORS_FILE`'a `name`, `connector`, `url` ve sırların yalnızca `*_file` yolları; zamanlama `data_sources.yaml` `schedule` (5 alanlı cron) veya örnekte. Geçit belirteci arayüzden (Veri Kaynakları → Kimlik ver) veya `evren connector issue` ile.
7. **Devre kesici.** `ingest.data_source.breaker_thresholds` için başlangıç değerlerini belirle.
8. **Harita ve yol haritası.** `architecture.yaml` ile `roadmap.json`'u güncelle; `python tools/build_architecture.py`.
9. **Belge.** `docs/04-kaynaklar.md`'ye kaynak bölümünü ekle: veri, API, tanımlayıcılar, tuzaklar.
10. Tüm testleri çalıştır; ardından `/durum`.
