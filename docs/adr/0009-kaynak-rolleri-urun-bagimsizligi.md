# ADR-0009: Kaynak rolleri — ürün bağımsızlığı

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

- Proje sahibi: "Kaynak sistemler değişebilir. Kullandığım discovery ürünü değişebilir. Şu an BMC'nin discovery ürünü var ama ileride değişebilir. vCenter, NNMi gibi sistemlerden veri alacağız ama sistemler bunlarla sınırlı değil."
- İlk tasarımda uzlaştırma ve veri tazeleme kuralları ürün adlarını içeriyordu (`BMC: 100, vCenter: 150`). Keşif ürünü değişince her kural elden geçirilmek zorundaydı; geçiş sırasında iki ürünün birlikte çalışması tanımlı değildi.

## Karar

1. **Kaynak rolü** kavramı eklenir (`metadata/data_sources.yaml → source_roles`): `Discovery`, `Virtualization`, `Network`, `Monitoring`, `EndpointSecurity`, `ITSM`, `ChangeManagement`, `Manual`, `Platform`. Rol, kaynağın CMDB'deki görevidir ve türünü (`kind`) taşır.
2. **Uzlaştırma ve veri tazeleme kuralları yalnızca rol adı kullanır.** Doğrulayıcı ürün/veri kaynağı adını kuralda görürse hata verir. Veritabanında `meta.cmdb_reconciliation_definition.role` ve `meta.cmdb_datasource_refresh.role` `meta.source_role`'a yabancı anahtarla bağlıdır.
3. **Veri kaynağı** bir rolü dolduran somut ürün kurulumudur: `role`, `role_rank`, `lifecycle`, `product` (üretici, ürün, arayüz), `connector`, `mappings`. Kaynak kayıtları, kimlik bağları ve `discovery_source` veri kaynağı adıyla tutulur; böylece kanıt zinciri ürün düzeyinde kalır.
4. **Yaşam döngüsü** ürün değişimini güvenli kılar:

   | Durum | Öznitelik yazar | CI açar | Eskimede sayılır |
   | --- | --- | --- | --- |
   | `active` | evet | evet | evet |
   | `shadow` | hayır (değerleri karşılaştırma için raporlanır) | hayır | hayır |
   | `retiring` | evet | hayır | evet |
   | `retired` | hayır | hayır | hayır |

5. **Aynı rolde yazan iki kaynak aynı `role_rank`'a sahip olamaz** (doğrulayıcı + `data_source_role_rank_writer` benzersiz indeksi).
6. Bağlayıcı klasörü ürünü adlandırır (`connectors/bmc_discovery/`); data source kaydı hangi eşleme dosyalarını kullandığını listeler ve test, sahipsiz eşleme dosyası kalmadığını denetler.

## Ürün değişimi yordamı (örnek: BMC Helix Discovery → başka keşif ürünü)

1. `connectors/<yeni>/` altında bağlayıcı ve eşleme dosyası; sentetik örnek ve test (`/yeni-baglayici`).
2. `data_sources.yaml`'a yeni kaynak: `role: Discovery`, `lifecycle: shadow`. Gölge modda kimlik çözümlenir, kapsam ve değer farkları ölçülür; golden record değişmez.
3. Terfi: yeni kaynak `active` + `role_rank: 1`, eski kaynak `retiring` + `role_rank: 2`. Mevcut CI'lar seri/UUID/FQDN kurallarıyla yeni ürünün kayıtlarına bağlanır; sys_id değişmez.
4. Emeklilik: eski kaynak `retired`. Kayıtları ve kimlik bağları denetim izi olarak kalır.

Bu yordamda `reconciliation_rules.yaml`, sınıflar, kimlik kuralları ve tüketiciler (API, ServiceNow replikası, GRC Platformu) değişmez.

## Değerlendirilen seçenekler

- **Ürün adıyla kurallar (eski tasarım):** basit; ancak her ürün değişimi kural değişikliği ve yeniden doğrulama gerektirir, iki ürünün birlikte çalışması tanımsızdır.
- **Kaynak başına "şablon" kopyalama:** yeni ürün eski ürünün kurallarını kopyalar. Kural sayısı ürün sayısıyla büyür; tutarlılık zamanla bozulur.

## Sonuçlar

- **Olumlu:** Keşif ürünü değişimi yalnızca bağlayıcı, eşleme ve kaynak kaydı değişikliğidir. Yeni kaynak türleri (bulut hesapları, Kubernetes, yedekleme…) yeni bir rol ya da mevcut bir rolle eklenir. Gölge mod ürün seçiminde kanıta dayalı karşılaştırma sağlar.
- **Olumsuz:** Aynı rolde iki üründen hangisinin üstün olduğu tek bir `role_rank` ile belirlenir; öznitelik bazında farklı üstünlük gerekirse ayrı rol tanımlanmalıdır.
- Göç: `db/migrations/0004_source_roles.sql`.
