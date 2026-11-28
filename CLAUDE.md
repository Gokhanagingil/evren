# CLAUDE.md — Evren CMDB

Bu dosya Claude Code için proje yönergesidir. Her oturumun başında bunu ve `DURUM.md`'yi oku.

## Proje

Evren, kurum içi ve on-prem çalışan bir CMDB platformudur. Amaç ServiceNow'dan kademeli çıkıştır: önce CMDB, sonra task tarafı.

- Kaynaklar: vCenter, BMC Helix Discovery, NNMi, Zabbix, Defender ve ServiceNow (ilk yükleme ve Change kayıtları). Kaynaklar değişebilir ve bunlarla sınırlı değildir: kurallar ürüne değil **kaynak rolüne** bağlıdır (ADR-0009).
- Ara katmanda kaynak verisi anlamlandırılır, kimliği çözülür ve uzlaştırılır.
- Veri deseni ServiceNow CMDB ile aynıdır: `cmdb_ci` soyağacı, `sys_id`, `cmdb_rel_ci`, IRE semantiği.
- Geçiş döneminde Evren ana kaynaktır, ServiceNow CMDB replikadır.

Mimarinin tamamı uygulamanın içindedir: **Mimari Harita** sayfası. İş planı da aynı şekilde: **Yol Haritası** sayfası. İkisinin tek kaynağı:

- `apps/web/content/architecture.yaml` → `python tools/build_architecture.py`
- `apps/web/content/roadmap.json` → `python tools/build_plan.py` (docs/PROJE-PLANI.md) ve `python tools/build_big_picture.py`
- `apps/web/content/components.json` → büyük resimdeki her kutunun tek cümlelik görevi (`purpose`), teknolojisi (`tech`), tamamlanma yüzdesi, var olanı, eksiği ve plan bağı (`python tools/build_big_picture.py`; çizim A3 yatay, 420 × 297 mm). %100'ün altındaki her bileşen bir plan maddesine bağlıdır; bağlı değilse test kırmızıdır.

## Çalışma biçimi (proje sahibinin beklentisi)

- İstenen işi yürüt; yalnızca plan veya öneri sunma. Yetki ve erişim kapsamındaki adımları tamamla.
- Verilmiş onayları dikkate al; aynı işlem için tekrar onay isteme.
- Gerçek bir engelde üç şeyi açıkça yaz: neyin eksik olduğu, etkisi ve uygulanabilir alternatifler.
- Uzun işlerde kısa ve anlamlı ilerleme bilgisi ver.
- **Her büyük adımdan önce** büyük resmin yaklaşık yüzde kaçının tamamlandığını (`roadmap.json`'dan; faz bazında da) ve sıradaki adımın ne olduğunu kısaca söyle, sonra çalışmaya başla.
- **Her oturumun sonunda** `DURUM.md`'yi güncelle (kararlar, yapılanlar, bilinen eksikler, sıradaki adımlar). `roadmap.json` durumlarını ve `components.json` yüzdelerini eşitle, `updated_at` alanlarını güncelle. Kesinti sonrası kaldığın yerden buradan devam edilir.
- **Önce temel:** CMDB (ServiceNow uyumlu ve daha iyisi veri modeli), yanında akıl katmanı ve kaynak verisinin akacağı ortam. Bunlar oturmadan çevre işlere (ör. CMDB sağlık panosu) girme; büyük resimde boşluk görürsen plana ekle.
- Kalite yalnızca kodla ölçülmez: kullanım kolaylığı, görsel kalite, içerik, mantık bütünlüğü, testler ve tamamlanmış ürün davranışı birlikte değerlendirilir.

## Dil

- Arayüz metinleri, dokümanlar, hata mesajları ve commit açıklamaları **Türkçe**.
- Kod tanımlayıcıları İngilizce.
- ServiceNow tablo ve alan adları değiştirilmeden kullanılır: `cmdb_ci`, `sys_id`, `sys_class_name`. SN'deki yazım hataları da korunur, örneğin `busines_criticality`.
- Türkçe büyük/küçük harf dönüşümünde `toLocaleLowerCase('tr')` kullan. Python'da kimlik anahtarları için `evren_core.normalize.ascii_fold`.

## Değişmez mimari kurallar

1. **Golden record'a tek yazan uzlaştırıcıdır.** API ve arayüzdeki elle düzenlemeler `Manual` kaynak kaydı olarak yazılır ve uzlaştırmadan geçer. Veritabanı rolleri bunu zorlar (`db/migrations/0003_security.sql`).
2. **Bağlayıcılar** yalnızca veri çeker ve eşleme dosyasıyla `IngestRecord` üretir. CMDB'ye yazmaz, eşleştirme yapmaz, CI silmez.
3. **Birleştirme geri alınabilir.** Kaynak → CI bağı `ingest.identity_link` tablosundadır. CI fiziksel olarak silinmez; yaşam döngüsü durumuyla emekliye ayrılır.
4. **Devre kesici** `OK` değilse eskime, emekliye ayırma ve ilişki silme gibi yıkıcı işlemler yapılmaz.
5. **Keşif araçları yönetişim alanlarını ezemez:** sahiplik, destek grubu, yaşam döngüsü. Bu `metadata/reconciliation_rules.yaml` ile tanımlıdır.
6. **Şema değişikliği metadata ile yapılır.** Yeni sınıf veya alan `metadata/*.yaml`'a eklenir ve `tools/load_metadata.py` ile yüklenir. Sınıf alanı için DDL yazılmaz. Üretimde değişiklik setiyle yayınlanır: taslak → onay → yayın; hazırlayan onaylayamaz.
7. **Ad tek başına kimlik değildir.** Kural eşleşse bile seri, UUID, işletim sistemi, sınıf veya FQDN çelişkisi varsa kayıt eşleştirme kuyruğuna gider.
8. **Kurallar ürüne değil kaynak rolüne yazılır.** `reconciliation_rules.yaml` ürün adı (BMC, vCenter) içermez; doğrulayıcı reddeder. Yeni ürün aynı rolde yeni veri kaynağıdır: önce `shadow`, sonra `active`; eskisi `retiring` → `retired` (ADR-0009).
9. **MCP.** Kurumun kendi MCP uygulaması Evren'e Evren MCP sunucusu ve REST sözleşmesiyle bağlanır (`docs/09-mcp-sozlesmesi.md`). ServiceNow'un yerleşik MCP'si kullanılmaz. MCP veri replikasyonu için kullanılmaz; replikasyon REST ve IRE API ile yapılır.
10. **Yapay zekâ önerir, insan karar verir.** Yapay zekâ çıktısı doğrudan golden record'a yazılmaz. Her öneri model, girdi ve gerekçesiyle kaydedilir (ISO/IEC 42001).
11. **ServiceNow'da business rule, ACL veya task alanı değişikliği gerektiren bir çözüm önerme.** Replika deseni bunun için var (`docs/05-servicenow-gecisi.md`).

## Güvenlik kuralları (kurumsal standart)

- Repoya, loglara, test verisine ve hata mesajlarına **sır yazma**. Kimlik bilgileri dosya yolundan veya kurum sır kasasından okunur.
- SQL her zaman parametrelidir. Kullanıcıdan gelen alan adları yalnızca metadata sözlüğünden çözülür (`metadata_field_resolver`).
- Arayüzde `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `eval`, satır içi `<script>`/`style=` kullanılmaz. CSP `script-src 'self'`; dış kaynak (CDN, yazı tipi) yüklenmez. Testler bunu denetler.
- Yeni bağımlılık eklemeden önce gerekçeyi yaz ve ADR aç. Bağımlılıklar hash ile kilitlenir (`pip --require-hashes`). GitHub eylemleri commit SHA ile sabitlenir.
- Yeni tablo veya servis eklerken `0003_security.sql` rollerini ve yetki testlerini (`tests/test_postgres.py`) güncelle.
- Kısıtlı veri (Defender maruziyet ve zafiyet) yalnızca `cmdb.ci_security_posture` tablosunda tutulur.
- Hedef: OWASP ASVS Seviye 2. Tehdit modeli `docs/06-guvenlik.md`; yeni bir dış arayüz eklerken güncelle.

## Komutlar

```bash
# Birim testleri (çekirdek kütüphane)
cd packages/evren-core && python -m pytest

# PostgreSQL entegrasyon testleri (migrasyonlar + tohum uygulanmış boş veritabanı gerekir)
for f in db/migrations/*.sql; do psql -v ON_ERROR_STOP=1 -f "$f"; done
python tools/load_metadata.py --sql build/seed.sql && psql -v ON_ERROR_STOP=1 -f build/seed.sql
cd packages/evren-core && EVREN_TEST_PG=1 python -m pytest

# Tüm yığın konteynerde (PostgreSQL, Kafka, OpenSearch, api, gateway, worker, ingest, indexer, governance, sn-sync; connector-runner profili) — ADR-0016…0021, ADR-0035, ADR-0042
python tools/dev_stack.py up --demo && python tools/dev_stack.py smoke
python tools/migrate.py --status            # izlenen göçler (EVREN_DATABASE_URL_FILE veya libpq ortamı)
python tools/connector_credential.py issue --name zabbix-dc1 --data-source Zabbix --by <kişi>   # bağlayıcı belirteci (ADR-0017)
python tools/connector_run.py --connector zabbix --simulate connectors/zabbix/samples --dry-run   # bağlayıcı çalıştırıcı (ADR-0020)
python tools/connector_archive.py list      # ham yük arşivi; yeniden işleme: connector_run.py --from-archive <çalışma> (ADR-0024)
EVREN_TEST_PG=1 python -m pytest services/connector-runner/tests   # çalıştırıcı servisi: istekler, zamanlama (ADR-0021)
EVREN_TEST_PG=1 python -m pytest services/ingest-gateway/tests services/indexer/tests   # alım geçidi, dizinleyici
EVREN_TEST_PG=1 python -m pytest services/governance/tests   # görev çalışanı: ServiceNow simülatörüyle (ADR-0035)
EVREN_TEST_PG=1 python -m pytest services/sn-sync/tests      # ServiceNow replikası: IRE simülatörüyle (ADR-0042)
EVREN_TEST_OPENSEARCH_URL=http://127.0.0.1:59200 python -m pytest packages/evren-core/tests/test_search_os.py   # arama alakası

# Metadata doğrula / tohum SQL üret
python tools/load_metadata.py --check

# Mimari harita (YAML → SVG + JSON), büyük resim, proje planı, arayüz demo verisi
python tools/build_architecture.py
python tools/build_big_picture.py
python tools/build_plan.py          # roadmap.json → docs/PROJE-PLANI.md
python tools/build_demo.py          # evren-core hattından sentetik demo.json
python tools/build_model.py         # metadata'dan Veri Modeli sayfasının demo verisi (model.json)
python tools/build_guide.py         # kullanım kılavuzu: docs/kilavuz → apps/web/content/guide.json (--check: sözcük sınırı, bağlantılar; ADR-0033)
python tools/export_schemas.py

# Arayüz (yerel, 127.0.0.1) ve tarayıcı duman testi
python tools/dev_server.py --port 8088
python -m evren_reconciler.worker            # uzlaştırıcı çalışanı (PYTHONPATH: packages/evren-core/src:services/reconciler)
python -m pytest apps/web/tests          # Playwright + Chromium

# ServiceNow sözlüğünü salt okunur dışa aktar ve karşılaştır
python tools/sn_metadata_export.py --out metadata/imported
```

## Tamamlanma tanımı

Bir iş ancak şu koşullar sağlanınca "tamamlandı" sayılır:

1. Birim, PostgreSQL ve tarayıcı testleri yeşil. Yeni davranışın testi var; olumsuz senaryolar da test ediliyor.
2. Güvenlik iş akışı yeşil: gitleaks, bandit, pip-audit, Trivy. Yüksek veya kritik bulgu yok.
3. Arayüz Türkçe, klavyeyle kullanılabilir (odak, Enter/Esc, `aria-*`) ve mobilde yatay taşma yok.
4. Mimari etkilendiyse `architecture.yaml` güncellendi ve yeniden üretildi.
5. `roadmap.json` ve `DURUM.md` güncel.
6. Kalıcı bir karar alındıysa `docs/adr/` altına ADR yazıldı.
7. Bilinen sınırlar dürüstçe yazıldı. Sentetik veriyle yapılan testler canlı entegrasyon kabulü sayılmaz.

## Dosya haritası

```
apps/web/                 Arayüz (derlemesiz ES modülleri), içerik: architecture.yaml, roadmap.json
packages/evren-core/      Saf alan mantığı + testler
  normalize.py            hostname/seri/MAC/UUID/IP normalizasyonu
  identity/               IRE kuralları, olasılıksal skor, çözümleyici; dependent.py bağımlı CI yerel anahtarı (ADR-0029)
  reconcile.py            öznitelik uzlaştırma (öncelik, veri tazeleme)
  circuit_breaker.py      kaynak sağlığı ve karantina
  encoded_query.py        ServiceNow sysparm_query → güvenli SQL
  envelope.py             alım zarfı (CloudEvents) → schemas/
  messaging.py            olay omurgası sözleşmesi: konular, anahtarlar, olay biçimi, Kafka ayarları (ADR-0017)
  kafka_client.py         Kafka G/Ç adaptörü (çekirdekte Kafka'ya dokunan tek modül)
  credentials.py          bağlayıcı belirteçleri (yalnızca SHA-256 özeti saklanır)
  search.py               CI arama dizini: eşleme, belge, sorgu (ADR-0018)
  opensearch_client.py    OpenSearch G/Ç adaptörü (standart kütüphane; çekirdekte OpenSearch'e dokunan tek modül)
  connectors/             bağlayıcı sözleşmesi, YAML alan eşleme motoru; örnek tanımı doğrulaması (instances; çalıştırıcı ve
                          entegrasyon sihirbazı ortak, ADR-0038); çalıştırıcı (http, gateway, runtime), şifreli
                          ham yük arşivi (archive, ADR-0024) ve kaynak istemcileri sources/ (ADR-0020; sırlar yalnızca dosyadan)
  metadata.py             sözlük yükleme/doğrulama, tohum SQL, sn.* görünümler
  relations.py            izinli ilişki kuralları (kalıtım, ters yön tespiti; ADR-0019)
  reference.py            ServiceNow referans ve değişiklik tabloları: izinli sütunlar, değer denetimi, değişiklik
                          sözlüğü (ADR-0022, ADR-0025)
  change_control.py       yetkisiz değişiklik: kural dosyası (metadata/change_control.yaml), onaylı pencere,
                          değerlendirme (ADR-0025)
  attestation.py          sahiplik onayı: politika (metadata/attestation.yaml), durum türetme, kapsam SQL'i (ADR-0027)
  staleness.py            eskime: kural seçimi (staleness), gözlemci kaynaklarla karar, emeklilik değerleri (ADR-0028)
  governance.py           yönetişim görevleri: sorumlu seçimi, kanal ayarı, ServiceNow görev kanalı (yalnızca standart
                          alanlar) ve simülatörü (ADR-0035); kampanya kapsamı ve sonraki çalışma (ADR-0036)
  quality.py              kalite ve uyum kuralları: tür kataloğu, şablonlar, kural → güvenli SQL planı, ilişki denetimi (ADR-0037);
                          kapsam politikası (coverage_gap: zorunlu roller, tazelik, çalışıyor kanıtı; ADR-0046)
  health.py               CMDB sağlık puanlaması (boyut ve genel puan; ölçümü görev çalışanı yapar)
  audit.py                denetim izi: olay kataloğu (Türkçe ad, kategori), mühür biçimi, özet ve zincir, dönemler,
                          doğrulama, CSV hücre koruması (ADR-0039; mühürleyen görev çalışanı, doğrulayan API ile ortak)
  ops.py                  platform uyarıları: kural doğrulama (metadata/alerts.yaml), anlık görüntü, sinyal
                          değerlendirmesi, uyarı yaşam döngüsü (ADR-0040)
  notify.py               bildirim merkezi: tür kataloğu, görev/uyarı bildirim taslağı (tırmanma), teslim hedefleri,
                          e-posta ve Teams ileti metni (ADR-0040)
  metrics.py              Prometheus metin biçimi (sayaç, gösterge, histogram; bağımlılıksız, ADR-0040)
  replica.py              ServiceNow replikası: kapsam doğrulaması (metadata/sn_sync.yaml), IRE yükü ve özeti, yanıt
                          yorumu (önce doğrula: çelişki kararı), IRE istemcisi ve simülatörü (ADR-0042)
  ire_inbound.py          IRE uyumlu alım: ServiceNow IRE yükü → alım kayıtları (doğrulama, bağımlı sınıfın ana CI'ı),
                          IRE biçiminde yanıt; geçit kullanır, golden record'a yazmaz (ADR-0045)
  timeline.py             zaman içinde sorgu: geçmiş bir andaki öznitelik ve ilişkiler, zaman çizelgesi anları (ADR-0031)
  model_view.py           Veri Modeli sayfasının görünümü (API ve model.json aynı kaynaktan)
  schedule.py             5 alanlı cron zamanlaması (çalıştırıcı servisi, metadata doğrulaması; ADR-0021)
  sources.py              kaynak rolleri, veri kaynağı yaşam döngüsü (ADR-0009)
  web.py                  güvenlik başlıkları (tek kaynak)
connectors/<kaynak>/      kaynak başına eşleme dosyaları; samples/ sentetik örnek veri (simülatör)
metadata/                 ServiceNow uyumlu sözlük tohumları (doğruluk kaynağı)
  changes/                yayınlanmış değişiklik setleri; yükleyici ad sırasıyla uygular (ADR-0015)
db/migrations/            0001 çekirdek · 0002 rol/izin tohumu · 0003 güvenlik · 0004 kaynak rolleri … 0019 çalıştırma istekleri · 0020 referans alımı · 0021 değişiklik denetimi · 0022 elle oluşturma · 0023 elle ilişki · 0024 toplu içe aktarma · 0025 sahiplik onayı · 0026 eskime · 0027 bağımlı kimlik · 0028 ilişki bildirenleri · 0029 sistem durumu · 0030 kullanıcı ve rol yönetimi · 0031 yönetişim görevleri · 0032 kampanyalar · 0033 kalite kuralları · 0034 denetim izi ve mühürler · 0035 uyarılar ve bildirimler · 0036 ServiceNow replikası · 0037 yapay zekâ önerileri · 0038 kapsam boşluğu kuralı · 0039 kapsam/kimlik ayrımı (tools/migrate.py uygular)
services/                 api, reconciler (çalışan + alım tüketicisi + outbox), ingest-gateway (alım geçidi), indexer (arama),
                          connector-runner (şimdi çalıştır + zamanlama; kaynak sırları yalnızca burada, ADR-0021),
                          governance (görev çalışanı: kampanya ve kural değerlendirme, sağlık ölçümü, ServiceNow'a görev teslimi, eşitleme,
                          kapatma, denetim izi mühürleme ve günlük doğrulama, platform uyarıları, bildirim üretimi ve
                          e-posta/Teams teslimi; ADR-0035…0037, ADR-0039, ADR-0040), sn-sync (ServiceNow replikası: gölge → etkin,
                          önce doğrula sonra yaz, toplu değişiklik freni; ServiceNow yazma kimliği yalnızca burada, ADR-0042);
                          Evren MCP sunucusu ayrı servis değil, API'de POST /mcp (araçlar çağıranın rolleriyle; öneriler
                          ai.proposal, insan karar verir; ADR-0043)
deploy/                   tek imaj (Dockerfile, evren.py giriş noktası), compose geliştirme yığını, Kafka konuları, connectors.dev.yaml
docs/                     mimari belgeler, ADR'ler; kilavuz/ kullanım kılavuzu (kilavuz.yaml + konu başına Markdown, ADR-0033)
tools/                    yükleyici, harita/büyük resim/plan/demo üreticileri, SN dışa aktarım, geliştirme sunucusu
```

## GRC Platformu uyumu

Belgelerdeki GRC uygulaması `github.com/Gokhanagingil/tedarik` reposundaki **GRC Platformu**dur (eski KS GRC / `control` reposu artık kullanılmıyor). Aynı ilkeler: Entra ID (`tedarik/vendor/entra_auth.py`), API sözleşmesinde `idempotency_key` ve `provenance`, kısmi toplamada `inconclusive` sonucu ve **aynı görsel dil** (`tedarik/static/app.css`: beyaz yüzeyler, üst çubukta Ctrl+K arama, sağdan açılan detay paneli, kırmızı vurgu). Gezinme biçimi Evren'de ayrıştı: gruplu kenar menü yerine uygulama çubuğu ve bölüm sekmeleri (ADR-0041); GRC Platformu'na da önerilir. GRC Platformu'nun CMDB modülü Evren'den beslenecek (karar 25.11.2026). Uyum gerektiren işlerde repoyu yan klasöre klonla ve ilgili dosyayı oku.
