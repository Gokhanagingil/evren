# DURUM — Evren CMDB

**Son güncelleme:** 26.11.2026  ·  **Etkin faz:** F0 — Temel ve gölge mod  ·  **İlerleme:** 13/54 plan adımı tamamlandı, 4'ü sürüyor (~%28; 26.11'de 9 eksik madde eklendi) · **Bileşen ortalaması (büyük resim):** %48 (CMDB %68) · **Uçtan uca izlek:** 3/10 tamam, U4 sürüyor (U4.1 sınıf kütüphanesi, U4.2 kimlik kuralları, U4.3 sahiplik alanları tamam; sırada U4.4 sınıf gezgini)

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
| 26.11.2026 | Büyük resimde her kutu tamamlanma yüzdesi taşır (tek kaynak `apps/web/content/components.json`: yüzde, var olan, eksik, plan bağı); %100'ün altındaki her bileşen bir plan maddesine bağlı olmak zorunda (test). Önce temel: CMDB veri modeli (ServiceNow uyumlu ve daha iyisi), akıl katmanı, veri akış ortamı; CMDB oturmadan sağlık panosu gibi çevre işlere girilmez | Proje sahibi |
| 26.11.2026 | CMDB veri modeli: 82 sınıflık ServiceNow uyumlu kütüphane (belirsiz adlar `verify: true`); kimlik ailesi bulunamazsa cmdb_ci'ın ilk alt dalı; CSDM 5 yaşam döngüsü bağımlı değer listesi + eski alan eşlemesi; izinli ilişki kuralları kalıtımlı, ters yön ayrıca tanınır, zorlama kademeli (bugün `warn`, yazımda uygulama U7); sınıfa özgü kimlik anahtarı `attr:<alan>` (hesap, küme kimliği, alt ağ: eşleşme güçlü, farklı değer çelişki), bağlam öznitelikleri küresel dizine girmez | ADR-0019 |
| 26.11.2026 | Uçtan uca izlek yeniden sıralandı: U4 CMDB veri modeli (eski U6 "veri modeli yönetimi" buna katıldı) → U5 bağlayıcılar → U6 elle girdi; bağlayıcılar yazacakları hedef sınıflar hazır olunca gelir | Proje sahibi |
| 25.11.2026 | CI araması OpenSearch'te: dizinleyici olaydan güncel CI'ı yazar (sürümlü dizin + takma ad), yetki API'de (okunabilir sınıf süzgeci, görülebilir alan, yanıt yeniden süzülür), OpenSearch yoksa PostgreSQL yedeği; standart kütüphaneyle istemci, ek bağımlılık yok | ADR-0018 |
| 25.11.2026 | Olay omurgası: bağlayıcılar yalnızca alım geçidine gönderir (belirteç tek kaynağa bağlı, yalnızca SHA-256 özeti saklanır); Kafka en az bir kez + kalıcı sayaçlar (etkisi bir kez); zehirli mesaj DLQ'ya; çalışmayı geçit açar, uzlaştırıcı kapatır; delta çalışmasında hacim denetlenmez; outbox → Kafka, gizli alan değeri olaya konmaz; Kafka istemcisi confluent-kafka | ADR-0017 |
| 25.11.2026 | Tek konteyner imajı (servis komutla seçilir), izlenen göçler (SHA-256 defteri; değişmiş göçte durur), servis başına giriş rolü, sırlar yalnızca dosyadan; compose geliştirme yığını CI'da uçtan uca sınanır. Üretim dağıtım biçimi hedef ortam kararıyla yazılacak | ADR-0016 |
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
- **Yeni çalışma düzeni: uçtan uca izlek (25.11.2026, proje sahibinin kararı).** Beklenti: PostgreSQL, Kafka ve OpenSearch altyapısı; kaynak verisini anlamlandıran akıl katmanı (elle girdi dahil); veri modeli; büyük resim uçtan uca ve tek arayüzden yönetim. Sıra (yol haritasında `track`, Yol Haritası sayfasının en üstünde): U1 altyapı yığını → U2 olay omurgası (alım geçidi, Kafka, tüketici, outbox) → U3 OpenSearch arama → U4 bağlayıcılar ve kaynak yönetimi → U5 elle girdi → U6 veri modeli yönetimi → U7 yaşam döngüsü ve ilişkiler → U8 yönetim ekranları → U9 ServiceNow replika ve MCP yapısı → U10 canlı kabul (dış girdilerle, en sonda). Sağlık panosunun zemini commit'lendi; pano U8'de.
- **Dış girdiler (Entra kaydı, ServiceNow hesabı, hedef ortam, kaynak hesapları) proje sahibinin kararıyla en sona bırakıldı;** önce yapı ana hatlarıyla tamamlanacak.
- **Büyük resim bileşen durumu (26.11.2026).** Proje sahibinin isteğiyle büyük resim çiziminde her kutuya kanıta dayalı tamamlanma yüzdesi eklendi; panel ortalamaları ve ana sütun özeti (CMDB %64, akıl katmanı %54, veri akış ortamı %38, tüketim %28, ServiceNow geçişi %7, güvenlik %74; genel %48). Büyük Resim sayfasında her bileşenin var olanı, eksiği ve plan maddesi tablo olarak görünür ("yalnızca eksiği olanlar" süzgeciyle).
  - İnceleme planda karşılığı olmayan boşlukları ortaya çıkardı ve plana eklendi: **F0-17 CMDB veri modeli** (ServiceNow temel sınıf kütüphanesinin genişliği, CSDM katmanları, yaşam döngüsü durumları; bugün 36 sınıf, 81 alan, 14 ilişki tipi), **F1-14 ham yük arşivi**, **F1-15 gözlemlenebilirlik**, **F1-16 SIEM aktarımı**, **F2-09 yedekleme, felaket kurtarma ve yüksek erişilebilirlik**.
  - Bir test artık %100'ün altındaki her bileşenin var olan bir plan maddesine bağlı olduğunu denetler; böyle bir boşluk bir daha sessizce kalamaz.
  - Çizimdeki Türkçe büyük harf hatası düzeltildi ("KAYIT SISTEMI" → "SİSTEMİ"; ürün adları Latin kurallarıyla: "SERVICENOW").
- **CMDB evreni yetenek kataloğu (26.11.2026)** — `docs/13-cmdb-evreni.md`: mimar ve konfigürasyon yönetimi uzmanı gözüyle A veri modeli, B akıl katmanı, C veri akışı, D tüketim, E ServiceNow geçişi, F platform; her maddenin bugünkü durumu ve plan bağı. Plana F1-17 sahiplik sertifikasyonu, F1-18 uyum denetimi (beklenen–gerçek), F1-19 zaman içinde sorgu, F1-20 ölçek ve yük testi eklendi.
- **U4.1 CMDB sınıf kütüphanesi (tamamlandı)** (ADR-0019, göç `0014_data_model.sql`).
  - `metadata/classes.yaml` 36 → **82 sınıf**, 81 → 109 alan. Yeni dallar: uç nokta (kişisel bilgisayar, yazıcı), kabin, depolama (cihaz, disk, birim, NAS, dosya sistemi), mantıksal ağ (IP ağı, VLAN, DNS adı), küme ve düğüm, konteyner ve Kubernetes (küme, ad alanı, düğüm, dağıtım, pod, servis), bulut (hizmet hesabı, mantıksal veri merkezi, Azure/AWS bölgesi), ara katman (Tomcat, JBoss, WebLogic, WebSphere; Apache, IIS, Nginx), veritabanı (MySQL, PostgreSQL, DB2, katalog), CSDM (iş yeteneği, iş süreci, bilgi nesnesi, dinamik CI grubu, hesaplanan/etiket tabanlı uygulama servisi; iş uygulaması alanları). DDL yazılmadı; `sn.<sınıf>` görünümleri kendiliğinden üretildi.
  - Kimlik ailesi: aile listesinde olmayan dal `cmdb_ci`'ın ilk alt sınıfıyla kendi ailesini kurar (önceden hepsi ortak "ailesiz" havuzdaydı; "prod" ad alanı ile "prod" IP ağı birbirinin adayı oluyordu).
  - Yaşam döngüsü: `life_cycle_stage` (6 aşama) ve `life_cycle_stage_status` (11 durum) değer listesi oldu; durum aşamaya bağımlı (`sys_choice.dependent_value`), her durum `operational_status`/`install_status` karşılığını taşır (`legacy`). Doğrulayıcı tanımsız üst değeri, `depends_on`'suz bağımlı değeri ve listede olmayan eski değeri reddeder.
  - İzinli ilişki kuralları: `metadata/relationship_rules.yaml` (36 kural, ürün adı yok) → `meta.cmdb_rel_rule`; `evren_core.relations.RelationRules` kalıtımlı denetim (izinli / yön ters / izinsiz), Türkçe ipucu, iki yönlü öneri; zorlama kipi `warn`. Yeni ilişki tipi `Provided by::Provides` (verify).
  - `verification_backlog()`: ServiceNow'dan doğrulanacak 113 öğe türüne göre (sınıf, alan, seçim listesi, ilişki tipi/kuralı, kimlik kuralı); `load_metadata --check` özetinde görünür.
  - **Bulunup düzeltilen:** demo üreticisi ters yönlü ilişkileri eklerken eklediği kaydı yeniden çeviriyordu; sunucu aynı VM ile hem ebeveyn hem çocuk görünüyordu. Bağlayıcı eşlemelerindeki ve demo verisindeki her ilişki artık kurallarla test ediliyor.
- **U4.3 sahiplik ve yönetişim kök alanları (tamamlandı)** (ADR-0019 §6, göç `0016_ownership.sql`).
  - `cmdb_ci` kök kolonları: `cost_center` (→ yeni `ref.cmn_cost_center`), `attested`, `attested_by`, `attested_date`. `CORE_COLUMNS` 35 → 39; DDL testi artık sonraki göçlerdeki `ADD COLUMN` satırlarını da sırayla okuyor.
  - Uzlaştırma: maliyet merkezi yönetişim alanı (Manual, ITSM); onay alanlarını yalnızca Manual yazar. Gerçek PostgreSQL testi: keşif aracının maliyet merkezi denemesi kazanan kararda "yetkisiz", onay denemeleri çakışma olarak raporlanıyor, golden record değişmiyor.
  - API: maliyet merkezi referans araması ve düzenleme formunda (yönetişimli); onay alanları formda yok (onay akışı U6). Yaşam döngüsü durumu değer listesinden doğrulanıyor (etiket gönderilirse 422).
  - Demo: dört maliyet merkezi (destek grubuna bağlı; kimlikleri ayrı ve deterministik, var olan demo sys_id'leri değişmedi), ServiceNow eşlemesine `cost_center`.
- **U4.2 yeni sınıfların kimlik kuralları (tamamlandı)** (ADR-0019 §5, göç `0015_class_identity_keys.sql`).
  - Sınıfa özgü kimlik anahtarı: `IdentityKeys.class_keys` → aday dizininde `attr:account_id`, `attr:cluster_id`, `attr:subnet`. Eşleşme +8, aynı alanda farklı değer −5 ve çelişki (kural eşleşse bile otomatik birleştirme yok). Alt ağ CIDR'ye çevrilir (`10.1.2.5/24` → `10.1.2.0/24`), geçersiz değer anahtar üretmez.
  - Bağlam öznitelikleri (ad alanı, bağlama noktası, bölge, VLAN) yalnızca bağımlı kurallarda; küresel dizine girmez.
  - Yeni kurallar: bulut hizmet hesabı, küme (Kubernetes dahil; ad tek başına yetmez), IP ağı, konteyner, servis ve CSDM kayıtları bağımsız; Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu bağımlı (U7'ye kadar güvenli varsayılanla "kimlik yetersiz").
  - Doğrulayıcı: sınıfta olmayan sınıf anahtarı, üst bağlamsız bağımlı kural, referans olmayan `parent_reference`, tanımsız üst sınıf reddedilir.
  - Gerçek PostgreSQL'de uzlaştırıcıyla: iki kaynaktan gelen bulut hesabı `account_id` ile aynı CI'a bağlanıyor, anahtar dizinde `attr:account_id`; tanımsız anahtar türü veritabanı kısıtıyla reddediliyor.
- **U3 arama (tamamlandı)** (ADR-0018, göç `0013_search_indexer.sql`). Yol haritasında F1-08'in arama kısmı tamam; CI 360° ekranı (ilişkiler, etki) U7'de, bu yüzden F1-08 sürüyor.
  - `evren_core/search.py`: katı eşleme; parçalama + Türkçe küçük harf + ASCII katlama; `prefix`/`raw`/`compact` alt alanları; sorgu sırası: tam kimlik → ayırıcısız → önek → bulanık (hepsi gerekli). IP biçimindeki sorgu IP önekiyle aranır. Sürümlü dizin (`MAPPING_VERSION`); gizli alanlar dizine girmez (`hidden_fields`, outbox ile ortak).
  - `evren_core/opensearch_client.py`: standart kütüphaneyle istemci. HTTPS ve doğrulama varsayılan; düz HTTP yalnızca açık izinle; parola yalnızca dosyadan; hata iletisi sunucu gövdesini yansıtmaz. Yeni bağımlılık eklenmedi.
  - Dizinleyici `services/indexer` (`evren indexer`): `evren.cmdb.ci.changed.v1` → güncel CI → OpenSearch. Açılışta dizini doğrular/kurar; `--reindex` kesintisiz çalışır ve tarama sırasında değişenleri ikinci kez yazar. Ofset ancak yazım başarılıysa saklanır. Rolü `evren_app_indexer` salt okurdur (testli).
  - `GET /api/v1/search`: okunabilir sınıflar süzgeç, görülebilir alanlar arama alanı; yanıt sınıf bazında yeniden süzülür (motor atlansa bile). OpenSearch yoksa veya ulaşılamazsa PostgreSQL yedeği (`ascii_fold` + SQL'de aynı katlama, joker karakter kaçırma), `engine`/`degraded`.
  - Arayüz: Ctrl+K paleti arama ucunu kullanır; motorun bulduğu öğeler istemcide yeniden süzülmez; yedeğe düşüş grup başlığında görünür.
  - **Bulunup düzeltilen:** Python'da `"İ".lower()` "i̇" üretiyordu, bu yüzden PostgreSQL araması "İST-DB"yi bulamıyordu (`ascii_fold` ile düzeltildi). İlk eşlemede tam IP araması benzer IP'leri de getiriyordu (IP önek dalı ve "ve" işleciyle düzeltildi).
  - Doğrulama: gerçek OpenSearch'e karşı 15 alaka ve yetki testi (CI "Yığın" işinde de); duman testi yeni CI'ı olaydan dizinlenmiş olarak büyük harf, yazım hatası ve ayırıcısız yazımla buluyor.
- **U2 olay omurgası (tamamlandı)** (ADR-0017, göç `0012_event_backbone.sql`).
  - Bağımlılık: `confluent-kafka` 2.16.1 (gerekçe ADR-0017; hash kilitli, pip-audit temiz). Kafka'ya dokunan tek modül `evren_core/kafka_client.py`; sözleşme `evren_core/messaging.py` (konular, anahtar `<kaynak>:<source_key>`, CloudEvents, DLQ başlıkları, `KafkaSettings`: varsayılan SASL_SSL, PLAINTEXT yalnızca açık izinle, parola yalnızca dosyadan).
  - Alım geçidi `services/ingest-gateway` (`evren gateway`, :8089): bağlayıcı belirteci (`evr_…`, `ingest.connector_credential`, süre/iptal/son kullanım, kimlik başına hız sınırı), kaynak yetkisi (başka kaynak/emekli kaynak 403 + denetim), şema, gelecek tarih, boyut sınırları, ya hep ya hiç toplu gönderim, Kafka onayından sonra 202. Çalışmayı açar, teslim edilenleri sayar, kapanış ister (bildirilen ≤ teslim edilen). TLS/mTLS, ağa TLS'siz açılmaz. Kaynak kaydına erişimi yok.
  - Bağlayıcı kimlikleri: `tools/connector_credential.py issue|list|revoke` (konteynerde `evren connector`); belirteç bir kez gösterilir veya 0600 dosyaya yazılır; işlemler denetim izinde.
  - Alım tüketicisi (`evren ingest`, `evren_ingest_svc`): DB işleminden sonra ofset, zehirli/geçit dışı/başka kaynağın çalışmasına yazan kayıt DLQ'ya, geçici hatada geri sarma; nabız `kind='ingest'`; Genel Bakış'ta durumu.
  - Çalışma sayaçları artık kalıcı (`source_record.first_run/changed_run/last_run`); `finalize_ready_runs`: tamam / 15 dk sonra eksik / 24 saat sonra terk edilmiş. Bellekteki sayaçlar kaldırıldı.
  - Outbox yayını çalışanda (`NOTIFY` ile uyanır): `cmdb.ci.*` → `evren.cmdb.ci.changed.v1`, insan istekleri → `evren.cmdb.activity.v1`; gizli hassasiyetteki alan değerleri maskelenir; Kafka yoksa olaylar beklemede kalır.
  - **Duman testinin yakaladığı hata (düzeltildi):** devre kesici delta çalışmasını tam çalışma geçmişiyle kıyaslıyor, tek kayıtlı delta çalışmasını "hacim düştü" diye karantinaya alıyordu. `evaluate_run(mode=…)`: delta'da yalnızca tamamlanma ve hata oranı; taban çizgisi yalnızca snapshot çalışmalardan.
  - Göç aracı: `--baseline` artık sürüm ister (`--baseline 0011_change_sets`); önceki hâli depodan geride kalan veritabanında uygulanmamış göçleri de uygulanmış sayabilirdi.
  - Yığın: `gateway` ve `ingest` servisleri, yeni servis giriş rolleri, `evren.cmdb.activity.v1` konusu; `tools/dev_stack.py smoke` olay omurgasını uçtan uca sınar (geçici kimlik → 401/403 olumsuz denetimler → çalışma → kayıt → tüketici → CI → "tamam" kapanış → Kafka CI olayı → kimlik iptali). CI'a alım geçidi testleri eklendi.
- **U1 altyapı yığını (tamamlandı)** (ADR-0016).
  - Tek imaj `deploy/Dockerfile` + giriş noktası `deploy/evren.py` (`api`, `worker`, `migrate`, `demo`, `healthcheck`). Bağımlılıklar hash'li kilitten, temel görüntü özetle sabit, uid 10001, ≈280 MB. Kurumsal TLS vekil sunucusu için isteğe bağlı `build_ca` derleme sırrı, imaja yazılmaz.
  - `tools/migrate.py`: izlenen göçler (`meta.schema_migration`, sürüm + SHA-256). Değişmiş göçte ya da depoda olmayan sürümde durur. `--baseline SÜRÜM` (psql ile kurulmuş veritabanı; yalnızca o sürüme kadar), `--status`, metadata tohumu, servis giriş rolleri (`evren_api_svc` … `evren_mcp_svc`; NOINHERIT, yalnız kendi grup rolü, parola dosyadan, ≥24 karakter).
  - `deploy/compose.dev.yaml`: PostgreSQL 16, Kafka 4 (KRaft; otomatik konu açma kapalı, konular `deploy/kafka/topics.txt`), OpenSearch 2.19, migrate, api, worker, demo. Dış görüntüler özetle sabit. Portlar yalnızca 127.0.0.1. Evren servisleri salt okunur kök, `cap_drop: ALL`, `no-new-privileges`. Sırlar compose `secrets` ile, `tools/dev_secrets.py` üretir (git dışı).
  - `tools/dev_stack.py`: `up [--demo]`, `smoke`, `status`, `logs`, `digests [--pull]`, `down [--volumes]`. Duman testi API'den elle düzenleme gönderir, konteynerdeki uzlaştırıcının golden record'a uyguladığını doğrular, ardından Kafka konularını ve OpenSearch sağlığını denetler.
  - API: konteynerde dev oturumu için açık `EVREN_DEV_CONTAINER=1`. Bu modda yalnızca loopback ve özel ağdan istek kabul edilir, diğerlerine 403 döner (testli). Çalışan: nabız dosyası (`EVREN_WORKER_HEALTH_FILE`).
  - CI: yeni "Yığın" işi geliştiricinin komutlarıyla aynı akışı çalıştırır: imaj, demo verisiyle yığın, göçü ikinci kez çalıştırma (bekleyen kalmaz), duman testi, sıkılaştırma denetimi (uid, salt okunur kök, yetenekler, port bağlama). Trivy imaj kapısı (düzeltmesi olan YÜKSEK/KRİTİK zafiyet veya sır) ve imaj SBOM'u bu işte üretilir. Göç aracı testleri PostgreSQL işinde çalışır. ruff ve bandit kapsamına `deploy/` eklendi.
  - Yerelde doğrulandı: yığın açıldı, tüm servisler sağlıklı, duman testi geçti (75 CI, uzlaştırıcı düzenlemeyi uyguladı, 4 Kafka konusu, OpenSearch green). Arayüz konteynerden API modunda açıldı. Sırasında bulunan ve düzeltilen hatalar: demo yükleyici sır dosyasını okumuyordu (`_bootstrap.database_url`); OpenSearch `nofile` sınırı bazı ortamlarda konteyneri başlatmıyordu, gereksiz olduğu için kaldırıldı; ilk imaj taraması temel görüntüde düzeltmesi yayınlanmış bir YÜKSEK bulgu (libzstd1) gösterdi, bu yüzden derleme anında Debian güvenlik güncellemesi zorunlu yapıldı (`apt-get update` erişim hatasında sessizce devam etmesin diye `--error-on=any`).
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
- **Doğrulama (26.11.2026, U4.3 sonrası, PostgreSQL 16, CI ile aynı sırada):** çekirdek birim 225 (yeni: veri modeli 31, sınıf kimliği 24) + PostgreSQL 64 (yeni: yetki sınırı 6, tohum ve yabancı anahtar 1), uzlaştırıcı 53 (yeni: sınıf anahtarı uçtan uca, tür kısıtı, sahiplik yönetişimi), API 78 (yeni: maliyet merkezi, yaşam döngüsü değer doğrulaması), demo yükleme, alım geçidi + dizinleyici 29, tarayıcı 14 demo + 9 API modu — hepsi geçti; `ruff` temiz.
- **Doğrulama (25.11.2026, U3 sonrası):** çekirdek 225 (+ gerçek OpenSearch'le 15 alaka testi), uzlaştırıcı 50, API 77 (arama dahil), alım geçidi 20, dizinleyici 9 (Entra olumsuz senaryoları, çalışan, gölge mod raporu, kanıt bütünlüğü, değişiklik setleri ve görevler ayrılığı dahil), tarayıcı 23 (14 demo modu + 9 API modu; Ctrl+K Türkçe arama dahil: gerçek uvicorn + PostgreSQL, sıkı CSP altında; ayırma, elle düzenleme, gölge mod raporu ve kanıt, değişiklik seti akışı uçtan uca; tüm sayfalar 390 px'te taşmasız) — hepsi geçti; `ruff` ve bandit eşiği temiz; konteyner yığını duman testi geçti. bandit, pip-audit, gitleaks ve Trivy GitHub güvenlik iş akışında çalışır.

## Bilinen eksikler ve sınırlar

- Veritabanındaki veri **sentetiktir** (`tools/load_demo_pg.py`); arayüz bunu üst çubukta işaretler. Statik sunucuda (demo modu) kuyruk kararları yalnızca oturumluktur.
- Entra akışı sahte kimlik sağlayıcıyla test edildi; **kurumun Entra kaydıyla canlı kabul yapılmadı.**
- Ürün değişimi tatbikatı sentetiktir; gerçek ikinci ürün geldiğinde onunla tekrarlanmalı.
- Değişiklik seti yayını anında etkili değildir: dosya PR ile repoya girip dağıtılınca uygulanır (ADR-0015); PR açma otomatik değil. Alan silme (`drop_field`), fiziksel kolona taşıma ve ilişki tipi ekleme arayüzde yok.
- Gölge mod raporu canlı SQL ile üretilir; milyonlarca CI'da özet tabloları gerekebilir (F1-09). Demo modunda (statik sunucu) rapor yok. Yerel veritabanında testlerin bitirmediği çalışmalar raporu "kesin değil" yapar (doğru davranış).
- Ayırma sonrası asıl CI'da ayrılan kayıttan türemiş ilişki satırları silinmez (eskimeye bırakılır). Ayrılan kaydı başka mevcut CI'a taşıma API'de var, arayüzde yok.
- Elle düzenlemeyi yalnızca Konfigürasyon analisti yapabilir; "CI sahibi" rolünün koşullu izni (kendi grubunun CI'ları) ABAC değerlendirmesi gelene kadar verilmez. İlişki düzenleme yok.
- API'de henüz yok: makine hesapları, ilişki düzenleme, ServiceNow uyumlu uç noktalar (F2-05), ABAC koşul değerlendirmesi (koşullu izinler şimdilik verilmez).
- Olay omurgası çalışıyor ama ona gönderen gerçek bağlayıcı yok: vCenter, Zabbix vb. istemciler U5'te. Bağlayıcı kimlikleri yalnızca komut satırından yönetiliyor (arayüz U4). Geçidin hız sınırı örnek başına (süreç içi). Bir çalışmada aynı `source_key` iki kez gönderilirse çalışma süre sonunda eksik kapanır (güvenli taraf; sözleşmede yazılı). Geliştirme yığınında Kafka SASL/ACL ve OpenSearch güvenlik eklentisi yok. CI listesi sayfası hâlâ PostgreSQL'de (küresel arama OpenSearch'te); 1–2 karakterlik parçalarda yazım hatası toleransı yok (bilinçli). OpenTelemetry metrikleri yok. sn-sync ve MCP servisleri yok.
- Üretim dağıtım tanımı (Kubernetes manifesti, Helm, VM betiği) yok: hedef ortam kararını bekliyor (U10). Geliştirme yığınında Kafka ve OpenSearch şifresiz, düz metin (yalnızca 127.0.0.1). İmaj imzalama (cosign) ve imaj kayıt defteri yok (hedef ortamla). Bu sanal geliştirme ortamında Debian deposuna erişim olmadığı için yerel imaj `EVREN_BUILD_OS_UPDATES=0` ile derlendi; güvenlik güncellemeli derleme ve Trivy imaj kapısı yalnızca CI'da doğrulanır.
- Oturum iptali çerez süresine bağlı (8 saat); anında iptal için sunucu tarafı iptal listesi gerekir (ADR-0010).
- Bağımlı CI kimliği (uygulama, ağ adaptörü) uygulanmadı; çözümleyici bilinçli olarak `insufficient` döner.
- U4'te eklenen bağımlı sınıflar (Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu) üst bağlamda çözümlenmiyor; "kimlik yetersiz" döner (U7). IP ağı kuralı alt ağı kurum genelinde tekil sayar; VRF çakışması olan kurumda bağımlı kurala çevrilmeli. İzinli ilişki kuralları tanımlı ve testli ama golden record'a yazımda henüz uygulanmıyor (U7). Yaşam döngüsü ile eski durum alanlarının tutarlılık denetimi U7'de. Elle düzenleme formunda aşama durumu, seçilen aşamaya göre süzülmüyor (U6). Sınıf ağacı/alan gezgini yok (U4.4). Yeni sınıf adları, kurallar ve onay alanlarıyla birlikte 130 öğe ServiceNow örneğinden doğrulanmadı (F0-07).
- Encoded query dot-walk desteklemiyor; açık hata döner.
- BMC Helix Discovery, NNMi, vCenter ve ServiceNow alan adları gerçek API çıktısıyla doğrulanmadı (`verify: true`).
- Skor ağırlıkları sentetik senaryolarla ayarlandı; gerçek kuyruk kararlarıyla kalibre edilmeli.
- Testler sentetik veriyle teknik kabuldür; canlı kaynak entegrasyonu veya kullanıcı kabulü sayılmaz.

## Sıradaki adımlar (uçtan uca izlek)

1. ~~**U1 Altyapı yığını**~~ — tamamlandı (ADR-0016).
2. ~~**U2 Olay omurgası**~~ — tamamlandı (ADR-0017).
3. ~~**U3 Arama**~~ — tamamlandı (ADR-0018).
4. **U4 CMDB veri modeli** *(sürüyor)* — ~~U4.1 sınıf kütüphanesi, CSDM, yaşam döngüsü, ilişki kuralları~~, ~~U4.2 yeni sınıfların kimlik kuralları~~ (ADR-0019). ~~U4.3 kök sahiplik alanları~~. Sırada: **U4.4** sınıf ağacı ve alan gezgini sayfası (değişiklik setleriyle; ilişki kuralları ve doğrulama listesi görünür). (F0-17, F0-07)
5. **U5 Bağlayıcılar ve kaynak yönetimi** — 6 kaynak istemcisi (kaydedilmiş örneklerle), arayüzden çalıştırma/zamanlama, bağlayıcı kimliği yönetimi, ham yük arşivi (F1-14).
6. **U6 Elle girdi** — CI oluşturma, ilişki, CSV içe aktarma.
7. **U7 yaşam döngüsü ve ilişkiler**, **U8 yönetim ekranları** (+ gözlemlenebilirlik F1-15, SIEM F1-16), **U9 ServiceNow replika ve MCP yapısı**.
8. **U10 Canlı kabul (en son, dış girdilerle)** — Entra kaydı, ServiceNow salt okunur hesabı, kaynak servis hesapları, hedef ortam; yedekleme ve felaket kurtarma (F2-09).

## Açık sorular

- BMC Helix Discovery kurulumu on-prem appliance mı, Helix SaaS mı?
- ServiceNow SaaS mı; MID Server var mı?
- Hedef ortam Kubernetes/OpenShift mi, VM üzerinde Docker mı?
- Entra uygulama kaydını kim açacak; GRC Platformu'nun kaydı mı genişletilecek, ayrı kayıt mı?
- Kurumun sır kasası ve SIEM ürünü hangisi?
- Yapay zekâ için onaylı model ve veri yerleşimi politikası nedir?
