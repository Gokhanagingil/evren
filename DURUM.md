# DURUM — Evren CMDB

**Son güncelleme:** 25.11.2026  ·  **Etkin faz:** F0 — Temel ve gölge mod  ·  **İlerleme:** 9/45 adım tamamlandı

Adım adım plan: [`docs/PROJE-PLANI.md`](docs/PROJE-PLANI.md) ve uygulamadaki **Yol Haritası** sayfası. Tek kaynak `apps/web/content/roadmap.json`; plan `python tools/build_plan.py` ile üretilir. Bu dosya ve o dosya birlikte güncellenir.

## Kararlar

| Tarih | Karar | Kaynak |
| --- | --- | --- |
| 25.11.2026 | Mimari öneriler proje sahibince onaylandı: ara katmanda Kafka, OpenSearch ve uzlaştırma motoru; kayıt sistemi PostgreSQL (hibrit şema); Evren ana kaynak, SN CMDB replika (IRE API); Redis/Valkey yalnızca önbellek ve kilit | ADR-0001 … 0005 |
| 25.11.2026 | "MCP" = kurumun kendi MCP uygulaması. ServiceNow'un yerleşik MCP'si kullanılmayacak | ADR-0007, docs/09 |
| 25.11.2026 | Güvenlik öncelikli, kurumsal standartta platform (ASVS L2 hedefi, en az yetki, salt ekleme denetim izi, tedarik zinciri kapıları) | ADR-0008, docs/06 |
| 25.11.2026 | Mimari çizim ve yol haritası uygulamanın içinde | ADR-0006 |
| 25.11.2026 | Arayüz derlemesiz ES modülleriyle, üçüncü taraf çalışma zamanı kodu olmadan yazılır | ADR-0006 |
| 25.11.2026 | Repo: `Gokhanagingil/evren`. Belgelerdeki GRC uygulaması `Gokhanagingil/tedarik` reposundaki **GRC Platformu**dur (KS GRC / `control` kullanılmıyor); arayüz onun görsel dilini kullanır | Proje sahibi |
| 25.11.2026 | **Kaynak sistemler değişebilir** (keşif ürünü bugün BMC, yarın başka): kurallar ürüne değil kaynak rolüne bağlanır; ürün değişimi gölge → devir → emeklilik yaşam döngüsüyle yapılır | ADR-0009 |
| 25.11.2026 | GRC Platformu'nun CMDB modülü, Evren hazır olunca CI verisini Evren'den alacak (bugün ServiceNow'dan senkron) | Proje sahibi, F2-08 |
| 25.11.2026 | BMC ürünü: BMC Helix Discovery (on-prem mi SaaS mı belirsiz; ikisi aynı REST API) | Proje sahibi |

## Varsayımlar

Doğrulanınca bu bölümden kaldırılacak.

- ServiceNow SaaS çalışıyor. SN'den Evren'e çağrılar MID Server veya API ağ geçidi gerektirecek.
- Kimlik doğrulama Entra ID ile, GRC Platformu'ndaki gibi.
- Çalışma ortamı Linux konteyner; Kubernetes mi Docker mı belirsiz.
- PostgreSQL 16 ve üzeri, Python 3.12.
- Takvim önerisi 12 ay; ekip kapasitesine göre güncellenecek.

## Yapılanlar

### Önceki oturum (Claude sohbeti, repoya ilk aktarım)

- Çekirdek kütüphane, `0001`–`0003` göçleri, metadata tohumları, Zabbix/vCenter/Defender/NNMi eşlemeleri, Mimari Harita ve Yol Haritası, araçlar.

### Bu oturum (25.11.2026, Claude Code)

- **Repo:** dosyalar `Gokhanagingil/evren` `main` dalına aktarıldı; geliştirme `claude/cool-knuth-r3lw30` dalında.
- **Kaynak rolleri (ADR-0009, F0-14):** `metadata/data_sources.yaml` → `source_roles` + veri kaynağına `role`, `role_rank`, `lifecycle`, `product`, `mappings`. Uzlaştırma ve tazeleme kuralları rol adıyla; doğrulayıcı ürün adını reddeder. `evren_core.sources.SourceRegistry`; `Reconciler` rol + rank sıralaması, gölge kaynak karşılaştırması. Göç `0004_source_roles.sql` (rol FK'leri, aynı rolde yazan kaynak için benzersiz sıra).
- **BMC Helix Discovery eşlemesi:** `connectors/bmc_discovery/host.mapping.yaml` (on-prem ve SaaS aynı API); BMC engeli kalktı (F1-01 planlandı).
- **ServiceNow ilk yükleme eşlemesi:** `connectors/servicenow_cmdb/server.mapping.yaml`; `IdentityEngine.import_existing` sys_id'yi korur.
- **Demo hattının ortaya çıkardığı ve düzeltilen hatalar:**
  - vCenter VM örneği ve misafir sunucu aynı `source_key`'i kullanıyordu (`(data_source, source_key)` çakışması) → eşlemede `prefix`.
  - NNMi `systemObjectId` harici kimlik sayılıyordu (modeli tanımlar; aynı modeldeki cihazlara +9 kanıt) → kaldırıldı.
  - Zabbix "Ağ cihazları" grubu Türkçe `ğ` nedeniyle ağ cihazı sınıfına düşmüyordu.
  - Yalnızca kaynak içi kimlik + IP taşıyan kayıt yeni CI açıyordu → artık "kimlik yetersiz".
  - Yetkili kaynak yokken reddedilen değer kayboluyordu → `Reconciler.reconcile_report().rejected`.
  - vCenter ve Defender CI adına FQDN yazıyordu → `short_hostname` dönüşümü.
- **Büyük resim ve plan (F0-15):** `tools/build_big_picture.py` → tek sayfalık çizim; `tools/build_plan.py` → `docs/PROJE-PLANI.md` (her adımda "sizden gereken"). Paylaşılabilir sayfa: claude.ai artifact (özel).
- **Arayüz (F0-16):** GRC Platformu (tedarik) görsel dili: beyaz gruplu kenar menü, üst çubukta Ctrl+K arama ve ? yardım, sağdan açılan detay paneli. Yeni ekranlar: Eşleştirme Kuyruğu, Konfigürasyon Öğeleri (CI 360°: öznitelik başına kaynak, kural gerekçesi, ezilen değerler, yetkisiz deneme), GRC Kontrolleri (provenance, inconclusive), Veri Kaynakları (rol, yaşam döngüsü, devre kesici, çalışma geçmişi), Kurallar (sınıf × rol öncelik ızgarası, IRE, tazeleme/eskime), Büyük Resim. Veri: `tools/build_demo.py` sentetik envanteri çekirdeğin gerçek hattından geçirir.
- **Belgeler:** "KS GRC" referansları GRC Platformu'na çevrildi; docs/03, 04, 10, CLAUDE.md, mimari harita güncellendi. CI'a `build_big_picture/plan/demo --check` eklendi.
- **F0-06 (tamamlandı):** `requirements/*.txt` hash'li ve 14 günlük tedarik zinciri bekleme süresiyle (uv, CPython 3.12); PostgreSQL imajı digest ile sabit; `.gitleaks.toml` yalnızca sentetik `source_key` değerleri için dar istisna + iş akışında olumsuz test (yol bazlı istisna gerçek bir GitHub belirtecini gizlediği için reddedildi); SN dışa aktarımında adres doğrulaması (bandit B310). GitHub'da **Evren CI** ve **güvenlik kapıları** yeşil.
- **Doğrulama (bu oturumda çalıştırıldı):** birim + PostgreSQL 16 testleri, 13 Chromium duman testi (tüm ekranlar, klavye, Esc, derin bağlantı, 390 px'te yatay taşma yok, konsol/CSP hatası yok), `ruff` temiz.
  - 150 birim ve PostgreSQL 16 entegrasyon testi geçti (rol FK'leri, aynı rolde yazan kaynağın benzersiz sırası, ürün değişimi senaryoları, kimlik hatalarının regresyon testleri dahil); 13 Chromium testi geçti.
  - Demo, büyük resim, plan ve mimari harita `--check` ile güncel; demo üretimi deterministik (eşit olasılıklı adaylar artık sys_id ile sıralanıyor).
  - gitleaks, bandit, pip-audit ve Trivy bu ortamda çalıştırılmadı; CI'da çalışacak.

## Bilinen eksikler ve sınırlar

- Ekranlar **sentetik demo verisiyle** çalışır; kuyruk kararları yalnızca oturum boyunca tutulur. Kalıcı kayıt API (F0-09) ve uzlaştırıcı (F0-10) ile gelecek.
- Gölge kaynağın CI açmaması `SourceRegistry.may_create_ci` ile tanımlı; uzlaştırıcı servisi henüz olmadığı için uçtan uca zorlanmıyor (F0-10'da).
- API, alım geçidi, uzlaştırıcı, sn-sync ve MCP servisleri yok (`services/*/README.md` tanımları var).
- Bağımlı CI kimliği (uygulama, ağ adaptörü) uygulanmadı; çözümleyici bilinçli olarak `insufficient` döner.
- Encoded query dot-walk desteklemiyor; açık hata döner.
- BMC Helix Discovery, NNMi, vCenter ve ServiceNow alan adları gerçek API çıktısıyla doğrulanmadı (`verify: true`).
- Skor ağırlıkları sentetik senaryolarla ayarlandı; gerçek kuyruk kararlarıyla kalibre edilmeli.
- Testler sentetik veriyle teknik kabuldür; canlı kaynak entegrasyonu veya kullanıcı kabulü sayılmaz.

## Sıradaki adımlar (sırayla)

2. **F0-07** — `tools/sn_metadata_export.py` ile kurum sözlüğü; `verify` işaretleri kapatılır. *Sizden: SN salt okunur hesap.*
3. **F0-09** — `services/api`: FastAPI, psycopg 3, Entra OIDC (`tedarik/vendor/entra_auth.py` deseni), rol eşlemesi, OpenAPI 3.1; ekranlar demo yerine API'den beslenir. *Sizden: Entra uygulama kaydı.*
4. **F0-08** — ServiceNow CMDB ilk yüklemesi (eşleme ve `import_existing` hazır).
5. **F0-10** — Alım geçidi, Kafka, uzlaştırıcı servisi (gölge kaynak kuralları dahil). *Sizden: hedef ortam kararı.*
6. **F0-11** — vCenter ve Zabbix bağlayıcıları.
7. **F0-12** — Eşleştirme kuyruğu kararlarının kalıcı kaydı (ekran hazır).
8. **F0-13** — Gölge mod raporları; GRC Platformu'na kanıt aktarımı.

## Açık sorular

- BMC Helix Discovery kurulumu on-prem appliance mı, Helix SaaS mı?
- ServiceNow SaaS mı; MID Server var mı?
- Hedef ortam Kubernetes/OpenShift mi, VM üzerinde Docker mı?
- Kurumun sır kasası ve SIEM ürünü hangisi?
- Yapay zekâ için onaylı model ve veri yerleşimi politikası nedir?
