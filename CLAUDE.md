# CLAUDE.md — Evren CMDB

Bu dosya Claude Code için proje yönergesidir. Her oturumun başında bunu ve `DURUM.md`'yi oku.

## Proje

Evren, kurum içi ve on-prem çalışan bir CMDB platformudur. Amaç ServiceNow'dan kademeli çıkıştır: önce CMDB, sonra task tarafı.

- Kaynaklar: vCenter, BMC ajanları, NNMi, Zabbix, Defender ve ServiceNow (ilk yükleme ve Change kayıtları).
- Ara katmanda kaynak verisi anlamlandırılır, kimliği çözülür ve uzlaştırılır.
- Veri deseni ServiceNow CMDB ile aynıdır: `cmdb_ci` soyağacı, `sys_id`, `cmdb_rel_ci`, IRE semantiği.
- Geçiş döneminde Evren ana kaynaktır, ServiceNow CMDB replikadır.

Mimarinin tamamı uygulamanın içindedir: **Mimari Harita** sayfası. İş planı da aynı şekilde: **Yol Haritası** sayfası. İkisinin tek kaynağı:

- `apps/web/content/architecture.yaml` → `python tools/build_architecture.py`
- `apps/web/content/roadmap.json`

## Çalışma biçimi (proje sahibinin beklentisi)

- İstenen işi yürüt; yalnızca plan veya öneri sunma. Yetki ve erişim kapsamındaki adımları tamamla.
- Verilmiş onayları dikkate al; aynı işlem için tekrar onay isteme.
- Gerçek bir engelde üç şeyi açıkça yaz: neyin eksik olduğu, etkisi ve uygulanabilir alternatifler.
- Uzun işlerde kısa ve anlamlı ilerleme bilgisi ver.
- **Her oturumun sonunda** `DURUM.md`'yi güncelle (kararlar, yapılanlar, bilinen eksikler, sıradaki adımlar). `roadmap.json` durumlarını eşitle ve `updated_at` alanını güncelle. Kesinti sonrası kaldığın yerden buradan devam edilir.
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
8. **MCP.** Kurumun kendi MCP uygulaması Evren'e Evren MCP sunucusu ve REST sözleşmesiyle bağlanır (`docs/09-mcp-sozlesmesi.md`). ServiceNow'un yerleşik MCP'si kullanılmaz. MCP veri replikasyonu için kullanılmaz; replikasyon REST ve IRE API ile yapılır.
9. **Yapay zekâ önerir, insan karar verir.** Yapay zekâ çıktısı doğrudan golden record'a yazılmaz. Her öneri model, girdi ve gerekçesiyle kaydedilir (ISO/IEC 42001).
10. **ServiceNow'da business rule, ACL veya task alanı değişikliği gerektiren bir çözüm önerme.** Replika deseni bunun için var (`docs/05-servicenow-gecisi.md`).

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

# Metadata doğrula / tohum SQL üret
python tools/load_metadata.py --check

# Mimari harita (YAML → SVG + JSON), JSON şemaları
python tools/build_architecture.py
python tools/export_schemas.py

# Arayüz (yerel, 127.0.0.1) ve tarayıcı duman testi
python tools/dev_server.py --port 8088
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
  identity/               IRE kuralları, olasılıksal skor, çözümleyici
  reconcile.py            öznitelik uzlaştırma (öncelik, veri tazeleme)
  circuit_breaker.py      kaynak sağlığı ve karantina
  encoded_query.py        ServiceNow sysparm_query → güvenli SQL
  envelope.py             alım zarfı (CloudEvents) → schemas/
  connectors/             bağlayıcı sözleşmesi, YAML alan eşleme motoru
  metadata.py             sözlük yükleme/doğrulama, tohum SQL, sn.* görünümler
  web.py                  güvenlik başlıkları (tek kaynak)
connectors/<kaynak>/      kaynak başına eşleme dosyaları
metadata/                 ServiceNow uyumlu sözlük tohumları (doğruluk kaynağı)
db/migrations/            0001 çekirdek · 0002 rol/izin tohumu · 0003 güvenlik
services/                 api, reconciler, ingest-gateway, sn-sync, mcp — sorumluluk tanımları
deploy/                   geliştirme ortamı (compose)
docs/                     mimari belgeler, ADR'ler
tools/                    yükleyici, harita üretici, SN dışa aktarım, geliştirme sunucusu
```

## KS GRC uyumu

`github.com/Gokhanagingil/control` ile aynı ilkeler kullanılır: Entra ID, API sözleşmesinde `idempotency_key` ve `provenance`, kısmi toplamada `inconclusive` sonucu ve aynı görsel dil. Uyum gerektiren işlerde repoyu yan klasöre klonla ve ilgili dosyayı oku.
