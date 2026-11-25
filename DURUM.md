# DURUM — Evren CMDB

**Son güncelleme:** 25.11.2026  ·  **Etkin faz:** F0 — Temel ve gölge mod  ·  **İlerleme:** 12/45 adım tamamlandı, 3'ü sürüyor (~%30)

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
| 25.11.2026 | Metadata değişiklik setleri Evren'de hazırlanır ve onaylanır (hazırlayan onaylayamaz); yayın metadata/changes/ değişiklik dosyasıdır, Git'ten dağıtılır; YAML doğruluk kaynağı kalır | ADR-0015 |
| 25.11.2026 | Gölge mod raporu rol tabanlı; mükerrer sınıfı kimlik kurallarına uyar (kesin/güçlü/olası/çelişkili); GRC kanıtı salt ekleme ve SHA-256 | ADR-0014 |
| 25.11.2026 | İnsan istekleri tablolarda kalır; sürekli çalışan uzlaştırıcı PostgreSQL bildirimiyle uyanır, istek başına hata yalıtımı ve nabız | ADR-0013 |
| 25.11.2026 | Her büyük adımdan önce proje sahibine "büyük resmin ~%'si tamamlandı + sıradaki adım" raporu verilir | Proje sahibi |
| 25.11.2026 | Elle düzenleme istek tablosu üzerinden; CI başına tek Manual kaynak kaydı; rol önceliği geçerli, sonuç alan başına; API'nin kaynak kaydı yazma yetkisi geri alındı | ADR-0012 |
| 25.11.2026 | Birleştirmeyi ayırma: istek API'de, uygulama uzlaştırıcıda; kimlik profili yeniden kurulur; ayrılan kaynağın tek başına verdiği değerler temizlenir; ilk yükleme kaydı ve tek kayıt ayrılmaz | ADR-0011 |
| 25.11.2026 | API oturumu: Entra OIDC + PKCE + MFA bağlamı, şifreli `__Host-` çerez; rol = uygulama rolü + grup ataması, girişte SoD; API golden record'a yazmaz, kuyruk kararını uzlaştırıcı uygular | ADR-0010 |

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
- **F0-10 çekirdeği (devam ediyor):** `services/reconciler/evren_reconciler` — PostgreSQL deposu (`store.py`: çalışma başlat/bitir, kaynak kaydı, kimlik çözümleme, öznitelik uzlaştırma, `audit.ci_change`, outbox), aday dizini ve kimlik bağı (`pg_identity.py`, göç `0005_identity_index.sql`). Devre kesici yeni ve değişen kayıtları ayrı sayar (ilk yükleme karantinaya düşmez); yalnızca `OK` ve tam anlık görüntüde "görülmedi" işaretlenir. Uzlaştırıcı rolü denetim izini okuyamaz, CI ve kimlik anahtarı silemez (testli). `tools/load_demo_pg.py` sentetik envanteri aynı hattan PostgreSQL'e yükler; bellek içi hatla aynı golden record'u ürettiği test edilir.
- **F0-09 (kod ve testler tamam, canlı Entra kabulü bekliyor):** `services/api/evren_api` (FastAPI, ADR-0010).
  - Okuma: `/me`, `/summary`, `/sources`, `/rules`, `/controls` (provenance, inconclusive), `/meta/classes`, `/ci` (arama, süzgeç, imleçli sayfalama, kapsam boşluğu görünümleri), `/ci/{id}` (öznitelik başına kaynak), `/ci/{id}/history`.
  - Yazma: `/match-queue/{id}/decision` — CSRF başlığı + aynı köken, `idempotency_key`, 409 sözleşmesi; karar `match_candidate` + outbox'a; uzlaştırıcı `apply_decisions` ile kimlik bağını kurar (göç `0006_api_decisions.sql`, API'nin yetkisi sütun düzeyinde).
  - Oturum: `dev` (yalnızca 127.0.0.1) ve `entra` (PKCE, nonce, MFA bağlamı, tekrar koruması, uygulama rolü → IAM rolü, grup ataması, girişte SoD reddi).
  - Yetki: rol ve sınıf kalıtımı, `deny` kazanır, hassasiyeti aşan alanlar yanıta konmaz, ret denetime yazılır.
- **Arayüz ↔ API:** `lib/data.js` tek veri katmanı; `content/backend.json` ile API veya demo modu. Kuyruk kararları kalıcı, CI araması sunucuda, CI 360°'da değişim geçmişi, kimlik ve roller API'den.
- **Belgeler:** tehdit modeline API satırları (docs/06), ADR-0010, `services/api/README.md` durum tablosu, docs/08 karar uç noktası, mimari harita (`rest_api`, `entra`).
- **Bu oturumda düzeltilen CI hatası:** demo yükleyicisi referans verisini çağıranın rolüyle yazmaya çalışıyordu (uzlaştırıcı `ref.*` yazamaz) → her zaman `evren_app_sync` rolüne geçip geri dönüyor. GitHub'daki son üç CI kırmızısının nedeni buydu.
- **F0-12 (tamamlandı): birleştirmeyi ayırma** (ADR-0011, göç `0007_split.sql`).
  - API `POST /api/v1/ci/{id}/split` (202, idempotency, CSRF, `cmdb_ci/split` yetkisi, engelleyici durumlar için açık 409/422 kodları); CI yanıtında bağ başına `split` durumu ve ayırma geçmişi.
  - Uzlaştırıcı `apply_splits`: bağı sonlandırır, kaydı yeni CI'a kalıcı bağlar, kimlik profilini yeniden kurar (tek CI'lık `SECURITY DEFINER` işlev; genel silme yetkisi yok), iki CI'ı yeniden uzlaştırır, yalnızca ayrılan kaynağın tek başına verdiği değerleri temizler.
  - Arayüz: CI 360°'da kaynak kaydı başına "Ayır…", etkilenecek değerleri önceden gösteren onay penceresi (gerekçe, Enter/Esc, odak iadesi), "Ayırma bekliyor" durumu, ayırma geçmişi ve iki CI arasında bağlantı.
- **F0-09 devamı: elle düzenleme** (ADR-0012, göç `0008_manual_edit.sql`).
  - API `PATCH /api/v1/ci/{id}` (202): alanlar sözlükten çözülür (`evren_api/editing.py`), tip/seçim/uzunluk/referans doğrulaması, Manual'ın yetkili olmadığı alan 422, geçersiz alan adı yansıtılmaz; `GET /api/v1/ref/{tablo}` referans araması (sabit liste, LIKE kaçışı). CI yanıtında `editable` (governed / öncelik sırası / yedek değer) ve düzenleme geçmişi.
  - Uzlaştırıcı `apply_manual_edits`: CI başına tek Manual kaydına ekler, uzlaştırır (neden `manual`, actor = kişi), sonucu alan başına yazar; kaldırılan elle değer golden record'da sahipsiz kalmaz; Manual kayıt kimlik anahtarı eklemez.
  - Arayüz: CI panelinde "Düzenle…": yönetişim alanları önde ve iki sütunlu, kaynak sahipli alanlar katlanır bölümde öncelik sırasıyla; referanslar aranabilir seçim; yalnızca değişen alanlar gönderilir; hata ayrıntısı pencere içinde. "Elle düzenlemeler" geçmişi alan başına sonuçla.
  - Ayırmada elle giriş bağı ayrılmaz ve "tek kaynak" sayımına girmez (API + uzlaştırıcı).
- **F0-10: sürekli çalışan uzlaştırıcı** (ADR-0013, göç `0009_worker.sql`).
  - `python -m evren_reconciler.worker`: `LISTEN evren_work` ile hemen uyanır (tetikleyiciler yeni istekte bildirir), kaçan bildirime karşı düzenli tarama; SIGTERM'de turu bitirip durur; bağlantı koparsa üstel beklemeyle yeniden bağlanır; bağlantı adresi günlüğe/`repr`'a girmez. `--once` bakım modu.
  - `ReconcilerStore._drain`: her istek kendi kayıt noktasında; beklenmeyen hata yalnızca o isteği hata kimliğiyle reddeder (kuyruk kilitlenmez), geçici veritabanı hatası isteği beklemede bırakır. `apply_requests` üç türü bir turda uygular.
  - Nabız `ingest.worker_heartbeat`; `/api/v1/summary` → `worker` (durum, bekleyen istekler, başarısız sayısı). Genel Bakış'ta "Uzlaştırıcı çalışıyor / çalışmıyor" göstergesi; kuyrukta uygulanamayan karar kırmızı ve nedeniyle.
  - Test sırası: kalıcı veri yazan çalışan testi demo yüklemesinden sonra çalışan API takımına taşındı (demo yükleyici boş veritabanı ister).
- **F0-13: gölge mod raporu** (ADR-0014, göç `0010_shadow_report.sql`; adım "sürüyor": gerçek ServiceNow verisi ve CMDB sahibi değerlendirmesi bekleniyor).
  - API `GET /api/v1/reports/shadow`: kapsam (mevcut CMDB'de olup keşfin görmediği / keşifte olup CMDB'de olmayan, sınıf × rol tablosu), mükerrer adayları (kimlik dizininden; kesin/güçlü/olası/çelişkili, keşfin görmediği kayıt için emekliye ayırma önerisi), değer farkları (gerçek / yalnız yazım; referanslar adıyla), gölge kaynak uyumu; bağımlı kaynak sağlıksızsa `inconclusive`.
  - Kanıt: `POST /api/v1/reports/shadow/snapshots` → `insight.evidence_snapshot` (salt ekleme, sahibi dahil değiştirilemez), kararlı JSON + SHA-256; indirme `X-Evren-Content-SHA256` ile doğrulanır. İzin: Veri sorumlusu, Denetçi.
  - Arayüz: "Gölge Mod Raporu" sayfası (CMDB grubunda): kesinlik uyarısı, özet kartları, kapsam tablosu, boşluk sekmeleri, mükerrer grupları ve önerileri, değer farkları (yazım farkları isteğe bağlı), kanıt kaydı ve JSON indirme; 390 px'te taşmasız.
  - Demo verisinde bulduğu gerçekçi durum: iki `ist-db-01` ServiceNow kaydı aynı MAC/IP, farklı seri (fiziksel → sanal taşıma); keşfin görmediği kayıt emekliye ayırma adayı olarak öneriliyor.
- **Öncelik değişikliği önerisi (25.11.2026, proje sahibinin sorusu üzerine: "CMDB hazır mı?"):** hayır — motor (model, kimlik, uzlaştırma, yönetişim, API, arayüz) hazır ve sentetik veriyle uçtan uca çalışıyor, ama veri girişi yok (bağlayıcılar yalnız eşleme dosyası, alım geçidi yok), ServiceNow ilk yüklemesi yapılmadı, eskime işletilmiyor, uygulama/hizmet katmanı ve bağımlı CI kimliği yok. Önerilen sıra: alım geçidi (veritabanı kuyruğu; Kafka sonra) → bağlayıcı istemcileri (kaydedilmiş örnek yanıtlarla) → eskime (F1-06) → bağımlı CI ve ilişkiler (F1-07) → sağlık panosu (F1-09). Proje sahibinin onayı bekleniyor. Sağlık panosunun zemini commit'lendi: `metadata/health_rules.yaml` (doğrulayıcıda) + `evren_core.health` puanlama (testli).
- **Dış girdiler (Entra kaydı, ServiceNow hesabı, hedef ortam, kaynak hesapları) proje sahibinin kararıyla en sona bırakıldı;** önce yapı ana hatlarıyla tamamlanacak.
- **F1-11: şema değişiklik setleri (tamamlandı)** (ADR-0015, göç `0011_change_sets.sql`).
  - Çekirdek `evren_core.changeset`: 7 işlem (sınıf ekle, alan ekle — `u_` önekli JSONB —, alan değiştir, kullanımdan kaldır, seçim değeri, veri kaynağı ekle — gölge —, yaşam döngüsü — izinli geçişler), tüm hatalar Türkçe ve birlikte; sonuç tam metadata doğrulamasından geçer. `load_metadata` temel dosyalardan sonra `metadata/changes/*.yaml` dosyalarını ad sırasıyla uygular.
  - API `/api/v1/schema/*`: katalog, liste/ayrıntı, oluştur (idempotency), onaya gönder (yeniden doğrulama + etki analizi), onayla/reddet/yayınla; çakışma önleme için yayınlanmış ama dağıtılmamış setler doğrulamaya katılır; değişiklik dosyası indirme (SHA-256).
  - Görevler ayrılığı: API 403, veritabanı kısıtları ve tetikleyici (izinli geçişler; yayınlanan/reddedilen set ve işlemleri değişmez/silinmez; işlem yalnız taslağa); API güncellemesi sütun düzeyinde.
  - Arayüz: "Şema ve Değişiklikler" sayfası (akış şeridi, set listesi, ayrıntı panelinde doğrulama, etki analizi, süreç ve duruma/yetkiye göre eylemler, dağıtım talimatı), işlem türüne göre değişen formla oluşturma penceresi.
  - Çalışan: `python -m evren_reconciler.worker --rereconcile <kaynak>` (yaşam döngüsü dağıtıldıktan sonra).
- **F1-13: ürün değişimi tatbikatı (tamamlandı, sentetik).** `tools/build_drill.py` → `apps/web/content/drill.json` (CI `--check`), Veri Kaynakları sayfasında tatbikat paneli.
  - BMC Helix Discovery → "Keşif-B" (tatbikat ürünü; yalnız kendi eşleme dosyası `tools/drill/kesif_b_host.mapping.yaml`), Keşif rolünde: Başlangıç → Gölge → Etkin + devir → Emeklilik.
  - 9 denetim geçti: gölgede golden record'da değer/kaynak değişimi 0 ve CI açılmadı; etkin olunca 32 değer + 178 kaynak değişimi yalnız Keşif-B lehine, gölgedeyken eşleşmeyen 2 kayıt CI açtı; devredilen BMC CI açamadı; emeklilik önizlemesi sahipsiz kalacak 180 özniteliği önceden ve birebir gösterdi; kural dosyalarının SHA-256'sı hiç değişmedi.
  - Bulgular (gerçek değişimde kontrol listesi): Keşif-B eşlemesi 5 alanı üretmiyor; işletim sistemi adlandırması farklı (gölge uyumu %0); 1 CI'ı yalnız BMC görüyordu.
  - Gerçek veritabanı: `ReconcilerStore.rereconcile_source` (yaşam döngüsü/rol sırası değişince bağlı CI'ları yeniden uzlaştırır; emekli kaynağın tek başına verdiği alanlar silinmez, sayılır), `tests/test_product_swap_pg.py`.
- **Testlerin yakaladığı güvenlik hatası (düzeltildi):** yetki reddi bir veritabanı işleminin içinde yazıldığında işlem geri alınınca denetim kaydı da kayboluyordu. Ret olayı artık işlem içindeyse ayrı bağlantıyla yazılır (`Ctx.security_event`) ve yetki denetimleri işlem dışında yapılır.
- **Arayüz düzeltmesi:** tarih-saat öznitelikleri (ilk/son keşif) ham ISO yerine yerel biçimde.
- **Doğrulama (bu oturumda çalıştırıldı, PostgreSQL 16, CI ile aynı sırada):** çekirdek 176 (değişiklik setleri dahil), uzlaştırıcı 38, API 67 (Entra olumsuz senaryoları, çalışan, gölge mod raporu, kanıt bütünlüğü, değişiklik setleri ve görevler ayrılığı dahil), tarayıcı 22 (14 demo modu + 8 API modu: gerçek uvicorn + PostgreSQL, sıkı CSP altında; ayırma, elle düzenleme, gölge mod raporu ve kanıt, değişiklik seti akışı uçtan uca; tüm sayfalar 390 px'te taşmasız) — hepsi geçti; `ruff` temiz. bandit, pip-audit, gitleaks ve Trivy GitHub güvenlik iş akışında çalışır.

## Bilinen eksikler ve sınırlar

- Veritabanındaki veri **sentetiktir** (`tools/load_demo_pg.py`); arayüz bunu üst çubukta işaretler. Statik sunucuda (demo modu) kuyruk kararları yalnızca oturumluktur.
- Entra akışı sahte kimlik sağlayıcıyla test edildi; **kurumun Entra kaydıyla canlı kabul yapılmadı.**
- Ürün değişimi tatbikatı sentetiktir; gerçek ikinci ürün geldiğinde onunla tekrarlanmalı.
- Değişiklik seti yayını anında etkili değildir: dosya PR ile repoya girip dağıtılınca uygulanır (ADR-0015); PR açma otomatik değil. Alan silme (`drop_field`), fiziksel kolona taşıma ve ilişki tipi ekleme arayüzde yok.
- Gölge mod raporu canlı SQL ile üretilir; milyonlarca CI'da özet tabloları gerekebilir (F1-09). Demo modunda (statik sunucu) rapor yok. Yerel veritabanında testlerin bitirmediği çalışmalar raporu "kesin değil" yapar (doğru davranış).
- Ayırma sonrası asıl CI'da ayrılan kayıttan türemiş ilişki satırları silinmez (eskimeye bırakılır). Ayrılan kaydı başka mevcut CI'a taşıma API'de var, arayüzde yok.
- Elle düzenlemeyi yalnızca Konfigürasyon analisti yapabilir; "CI sahibi" rolünün koşullu izni (kendi grubunun CI'ları) ABAC değerlendirmesi gelene kadar verilmez. İlişki düzenleme yok.
- API'de henüz yok: makine hesapları, ilişki düzenleme, ServiceNow uyumlu uç noktalar (F2-05), ABAC koşul değerlendirmesi (koşullu izinler şimdilik verilmez).
- Sürekli çalışan uzlaştırıcı yalnızca insan isteklerini uygular; kaynak kayıtlarının alımı (alım geçidi + Kafka tüketicisi) hedef ortam kararını bekliyor. Çalışan için konteyner imajı ve OpenTelemetry metrikleri yok. sn-sync ve MCP servisleri yok.
- Oturum iptali çerez süresine bağlı (8 saat); anında iptal için sunucu tarafı iptal listesi gerekir (ADR-0010).
- Bağımlı CI kimliği (uygulama, ağ adaptörü) uygulanmadı; çözümleyici bilinçli olarak `insufficient` döner.
- Encoded query dot-walk desteklemiyor; açık hata döner.
- BMC Helix Discovery, NNMi, vCenter ve ServiceNow alan adları gerçek API çıktısıyla doğrulanmadı (`verify: true`).
- Skor ağırlıkları sentetik senaryolarla ayarlandı; gerçek kuyruk kararlarıyla kalibre edilmeli.
- Testler sentetik veriyle teknik kabuldür; canlı kaynak entegrasyonu veya kullanıcı kabulü sayılmaz.

## Sıradaki adımlar (sırayla)

1. **F0-09 devamı** — canlı Entra kabulü; makine hesapları (GRC Platformu için); ABAC koşul değerlendirmesi (CI sahibi). *Sizden: Entra uygulama kaydı (kiracı, istemci, `Evren.*` uygulama rolleri, MFA bağlamı).*
2. **F0-07** — `tools/sn_metadata_export.py` ile kurum sözlüğü; `verify` işaretleri kapatılır. *Sizden: SN salt okunur hesap.*
3. **F0-08** — ServiceNow CMDB ilk yüklemesi (eşleme ve `import_existing` hazır).
4. **F0-10 devamı** — Alım geçidi ve Kafka tüketicisi (uzlaştırıcı çalışanına eklenir), konteyner imajları. *Sizden: hedef ortam kararı.*
5. **F0-11** — vCenter ve Zabbix bağlayıcıları. *Sizden: salt okunur servis hesapları.*
6. **F0-13 kabulü** — Gerçek ServiceNow verisiyle ilk gölge mod raporu (F0-08'den sonra), CMDB sahibinin değerlendirmesi, ilk kanıtın GRC Platformu'na aktarımı (makine hesabı ile).

## Açık sorular

- BMC Helix Discovery kurulumu on-prem appliance mı, Helix SaaS mı?
- ServiceNow SaaS mı; MID Server var mı?
- Hedef ortam Kubernetes/OpenShift mi, VM üzerinde Docker mı?
- Entra uygulama kaydını kim açacak; GRC Platformu'nun kaydı mı genişletilecek, ayrı kayıt mı?
- Kurumun sır kasası ve SIEM ürünü hangisi?
- Yapay zekâ için onaylı model ve veri yerleşimi politikası nedir?
