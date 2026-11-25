# DURUM — Evren CMDB

**Son güncelleme:** 25.11.2026  ·  **Etkin faz:** F0 — Temel ve gölge mod  ·  **İlerleme:** 5/41 iş kalemi tamamlandı

Ayrıntılı plan uygulamadaki **Yol Haritası** sayfasında. Kaynağı `apps/web/content/roadmap.json`. Bu dosya ve o dosya birlikte güncellenir.

## Kararlar

| Tarih | Karar | Kaynak |
| --- | --- | --- |
| 25.11.2026 | Mimari öneriler proje sahibince onaylandı: ara katmanda Kafka, OpenSearch ve uzlaştırma motoru; kayıt sistemi PostgreSQL (hibrit şema); Evren ana kaynak, SN CMDB replika (IRE API); Redis/Valkey yalnızca önbellek ve kilit | ADR-0001 … 0005 |
| 25.11.2026 | "MCP" = kurumun kendi MCP uygulaması. ServiceNow'un yerleşik MCP'si kullanılmayacak | ADR-0007, docs/09 |
| 25.11.2026 | Güvenlik öncelikli, kurumsal standartta platform (ASVS L2 hedefi, en az yetki, salt ekleme denetim izi, tedarik zinciri kapıları) | ADR-0008, docs/06 |
| 25.11.2026 | Mimari çizim ve yol haritası uygulamanın içinde, KS GRC'deki Mimari Harita düzeninde | ADR-0006 |
| 25.11.2026 | Arayüz derlemesiz ES modülleriyle, üçüncü taraf çalışma zamanı kodu olmadan yazılır (KS GRC ve tedarik portalıyla aynı yaklaşım) | ADR-0006 |

## Varsayımlar

Doğrulanınca bu bölümden kaldırılacak.

- ServiceNow SaaS çalışıyor. SN'den Evren'e çağrılar MID Server veya API ağ geçidi gerektirecek.
- Kimlik doğrulama Entra ID ile, KS GRC'deki gibi.
- Çalışma ortamı Linux konteyner; Kubernetes mi Docker mı belirsiz.
- PostgreSQL 16 ve üzeri, Python 3.12.
- Takvim önerisi 12 ay; ekip kapasitesine göre güncellenecek.

## Yapılanlar

- **Çekirdek kütüphane** (`packages/evren-core`): normalizasyon, kimlik çözümleme (IRE kuralları, olasılıksal skor, çelişki denetimi), uzlaştırma, devre kesici, encoded query derleyicisi, alım zarfı, YAML alan eşleme motoru, metadata yükleyici.
- **Veritabanı:** `0001_core` (sözlük, golden record, ilişkiler, alım, kimlik bağları, eşleştirme kuyruğu, SN eşleme durumu, denetim, içgörü, IAM), `0002_iam_seed` (13 rol, izinler, SoD), `0003_security` (en az yetkili roller, salt ekleme denetim izi, kısıtlı güvenlik tablosu, yetki değişikliği denetimi).
- **Metadata tohumları:** 36 sınıf, 81 alan, 14 ilişki tipi, 9 veri kaynağı, tanımlama/uzlaştırma/tazeleme/eskime kuralları. ServiceNow örneğinden doğrulanacak 18 öğe `verify: true` ile işaretli.
- **Eşleme dosyaları:** Zabbix, vCenter (VM örneği + misafir sunucu), Defender, NNMi, BMC (taslak).
- **Arayüz:** Genel Bakış, Mimari Harita ve Yol Haritası.
  - Harita: 6 bölüm, 57 bileşen; sığdır, yakınlaştır, arama, detay paneli, derin bağlantı, SVG indirme.
  - Yol haritası: 5 faz, filtre, arama, çıkış kriterleri, haritaya bağlantı.
- **Araçlar:** `load_metadata.py`, `build_architecture.py`, `export_schemas.py`, `sn_metadata_export.py` (salt okunur), `dev_server.py` (güvenlik başlıklı).
- **Doğrulama (bu oturumda çalıştırıldı):**
  - 127 birim ve entegrasyon testi geçti. PostgreSQL 16 testleri dahil: sözlük dışı alanın ve yanlış tipin reddi, denetim izinin değiştirilemezliği, SoD, 8 en az yetki sınırı, encoded query'nin gerçek şemada çalışması, tohumun iki kez uygulanabilmesi.
  - 6 Chromium duman testi geçti: güvenlik başlıkları, harita ve yol haritası etkileşimleri, mobil taşma, konsol/CSP hatası yok.
  - `ruff` temiz.
  - gitleaks, bandit, pip-audit ve Trivy bu ortamda çalıştırılmadı; CI'da çalışacak.

## Bilinen eksikler ve sınırlar

- **F0-06 — CI güvenlik kapıları** yazıldı ama GitHub'da henüz çalışmadı. Bağımlılık hash kilit dosyaları (`requirements/*.txt`) bu ortamda ağ erişimi olmadığı için üretilemedi; ilk iş olarak `pip-compile --generate-hashes` ile üretilmeli.
- **F1-01 — BMC bağlayıcısı engelli:** hangi BMC ürününün kullanıldığı bilinmiyor; eşleme dosyası taslak.
- API, alım geçidi, uzlaştırıcı, sn-sync ve MCP servisleri yok. `services/*/README.md` içinde sorumluluk ve arayüz tanımları var.
- Bağımlı CI kimliği (uygulama, ağ adaptörü) uygulanmadı. Çözümleyici bu sınıflarda bilinçli olarak `insufficient` döner; küresel ad eşleştirmesi yapmaz.
- Encoded query derleyicisi dot-walk (`support_group.name`) desteklemiyor; açık bir hata döner.
- NNMi ve vCenter alan adları gerçek API çıktısıyla doğrulanmadı (`verify: true`).
- Skor ağırlıkları sentetik senaryolarla ayarlandı. Gerçek veride eşleştirme kuyruğu kararlarıyla kalibre edilmeli.
- Testler sentetik veriyle teknik kabuldür; canlı kaynak entegrasyonu veya kullanıcı kabulü sayılmaz.

## Sıradaki adımlar (sırayla)

1. **F0-06** — Bağımlılık kilit dosyalarını hash'li üret (`requirements/runtime.in`, `requirements/dev.in` → `.txt`). Ardından `ci.yml` ve `security.yml` iş akışlarını GitHub'da çalıştır ve bulguları kapat.
2. **F0-07** — `tools/sn_metadata_export.py` ile kurum örneğinden sözlüğü al. `metadata/imported/diff.txt` farklarını kapat; doğrulanan öğelerden `verify` işaretini kaldır.
3. **F0-09** — `services/api`: FastAPI, psycopg 3 havuzu, Entra OIDC (KS GRC'deki `entra_auth.py` deseni), Entra grup → `iam` rol eşlemesi, statik arayüzün `evren_core.web.SECURITY_HEADERS` ile sunulması, OpenAPI 3.1. Yerel geliştirme sunucusunun yerini alır.
4. **F0-08** — ServiceNow CMDB ve referans verilerinin ilk yüklemesi, sys_id korunarak. `ingest.identity_link.method='import'`.
5. **F0-10** — Alım geçidi (şema doğrulama, kaynak başına yetki), Kafka konuları, uzlaştırıcı servisi (değişim süzgeci → kimlik → uzlaştırma → outbox).
6. **F0-11** — vCenter ve Zabbix bağlayıcıları (`evren_core.connectors.Connector` sözleşmesi).
7. **F0-12** — Eşleştirme kuyruğu ekranı.
8. **F0-13** — Gölge mod raporları: mükerrerler ve kapsam boşlukları. KS GRC'ye kanıt aktarımı.

## Açık sorular

- Hangi BMC ürünü kullanılıyor (Discovery/ADDM, Client Management, Helix)?
- ServiceNow SaaS mı; MID Server var mı?
- Hedef ortam Kubernetes/OpenShift mi, VM üzerinde Docker mı?
- Kurumun sır kasası ve SIEM ürünü hangisi?
- Yapay zekâ için onaylı model ve veri yerleşimi politikası nedir?
