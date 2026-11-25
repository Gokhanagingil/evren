# 06 · Güvenlik

Evren kurumsal standartlarda ve güvenlik öncelikli tasarlanır. CMDB, bir saldırgan için altyapının haritasıdır: IP'ler, bağımlılıklar, koruma durumu. Bu yüzden **yüksek değerli varlık** olarak ele alınır.

Hedef doğrulama seviyesi: **OWASP ASVS 4.0 Seviye 2**.

## İlkeler

1. **Varsayılan reddet.** İzin açıkça verilmedikçe yoktur.
2. **En az yetki.** Her servis ayrı veritabanı rolüyle çalışır. Golden record'a yalnızca uzlaştırıcı yazar.
3. **Derinlemesine savunma.** Kurallar hem uygulamada hem veritabanında uygulanır: tetikleyiciler, roller, kısıtlar.
4. **Her değişiklik iz bırakır.** Denetim izi salt eklemelidir.
5. **Sır yok.** Repo, imaj, log ve test verisinde kimlik bilgisi bulunmaz.
6. **Tedarik zinciri kontrol altında.** Hash kilitli bağımlılıklar, sabitlenmiş CI eylemleri, SBOM, tarama.
7. **Yapay zekâ önerir, insan karar verir.**

## Tehdit modeli (STRIDE)

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| **S**poofing | Sahte bağlayıcı veri gönderir | mTLS istemci sertifikası; anahtar ↔ data_source eşlemesi | ingest-gateway |
| S | Oturum çalma | Entra OIDC + PKCE, `__Host-` çerez, kısa oturum, koşullu erişim | api |
| **T**ampering | Kaynak verisi CMDB'yi bozar (ör. kısmi çalışma, eşleme hatası) | Şema doğrulama, devre kesici, uzlaştırma kuralları, birleştirmenin geri alınabilmesi | reconciler, `circuit_breaker.py` |
| T | Denetim izinin değiştirilmesi | UPDATE/DELETE/TRUNCATE engelli tetikleyici; SIEM'e aktarım | `0003_security.sql` |
| T | SQL enjeksiyonu (sysparm_query) | Parametreli SQL; alan adları sözlükten; `javascript:` reddi | `encoded_query.py` |
| **R**epudiation | "Bu değişikliği ben yapmadım" | `audit.ci_change` (aktör, kaynak, gerekçe, korelasyon); yetki değişiklikleri `audit.security_event` | DB |
| **I**nformation disclosure | Güvenlik duruşu verisinin sızması | Ayrı kısıtlı tablo; alan hassasiyeti (`sensitivity`); rol bazlı okuma; okuma denetimi | `ci_security_posture`, IAM |
| I | Hata mesajlarında iç ayrıntı | Standart hata gövdesi; yığın izi yalnızca sunucu logunda | api |
| I | Yapay zekâ modeline hassas veri gitmesi | Onaylı/kurum içi model; veri sınıflandırması; asgari bağlam | docs/11 |
| **D**enial of service | Büyük sorgu veya derin etki analizi | Koşul ve IN sınırı, sayfalama, sorgu zaman aşımı, derinlik sınırı, hız sınırı | api, mcp |
| D | Kaynak taşması | Alım geçidinde boyut ve hız sınırı; Kafka geri basıncı | ingest-gateway |
| **E**levation of privilege | Kendine rol verme, çatışan roller | `granted_by <> principal`; SoD tetikleyicisi; rol değişikliği denetimi | `iam.*` |
| E | Makine hesabıyla insan işlemi | Makine rolleri (`machine=true`) yönetim işlemlerine kapalı | IAM |
| E | MCP üzerinden istem enjeksiyonu | CMDB içeriği veri olarak işaretlenir; MCP rolü yazamaz; öneriler insan onayına düşer | mcp |
| E | XSS ile oturum ele geçirme | CSP `script-src 'self'`; innerHTML yasak (test ve CI denetimi) | apps/web |
| S | Entra akışında kod/akış tekrarı, nonce sahteciliği | PKCE S256; `state`/`nonce`/doğrulayıcı süreç anahtarıyla mühürlü akış çerezinde; aynı akış ikinci kez reddedilir; RS256, kiracı, hedef kitle, düzenleyen, `nonce`, MFA bağlamı (`acrs`) zorunlu | `evren_api/entra.py`, test_entra |
| S | Oturum çerezinin kurcalanması | Fernet (şifreli + imzalı) süreli çerez; kurcalanan çerez yok sayılır (401) | `evren_api/security.py` |
| S | `dev` modunun üretimde açılması | `dev` yalnızca geri döngü adresinde başlar; Entra ayarı varken reddedilir | `evren_api/config.py` |
| T | Siteler arası istek sahteciliği (CSRF) | Durum değiştiren isteklerde `X-Evren-Request` başlığı + aynı köken; `SameSite=Strict`; CORS yok | `evren_api/deps.py` |
| T | Kuyruk kararının API'den golden record'a doğrudan yazılması | API yalnızca karar sütunlarını güncelleyebilir (sütun düzeyi yetki); bağı ve CI'ı uzlaştırıcı yazar | `0006_api_decisions.sql` |
| T | Aynı kararın tekrar gönderilmesi | `idempotency_key`: aynı içerik → kayıtlı yanıt; farklı içerik → 409 | `api.idempotency` |
| I | Yetki dışı alanların yanıtta görünmesi | Rol izninin en yüksek hassasiyeti üstündeki alanlar yanıta hiç konmaz (arayüzde gizlemek yeterli sayılmaz) | `evren_api/authz.py` |
| I | Doğrulama hatasında girdinin yansıtılması | Hata gövdesi yalnızca alan konumu ve hata türü içerir | `evren_api/errors.py` |
| T | Ayırma (split) ile kimlik dizininin bozulması veya CI kimliğinin kaybı | API yalnızca istek yazar; uzlaştırıcı yeniden doğrular (bağ değişti mi, tek kayıt mı, ilk yükleme kaydı mı); anahtar silme yalnızca tek CI için `SECURITY DEFINER` işlevle (sabit `search_path`), genel DELETE yok | `0007_split.sql`, test_split_pg |
| R | Yetki reddinin denetim kaydının işlemle birlikte geri alınması | Ret olayı istek işlemi içindeyse ayrı bağlantıyla yazılır; ret testlerle denetim izinde doğrulanır | `evren_api/deps.py`, test_split |
| E | Grup üyeliği yoluyla görevler ayrılığı çatışması | Girişte uygulama rolü + grup ataması birleşimi `iam.sod_constraint` ile denetlenir; çatışmada giriş reddedilir ve denetime yazılır | `routers/auth.py` |

## Uygulanan denetimler (bu depoda, testli)

- `0003_security.sql`: 7 grup rolü, şema ve tablo düzeyinde yetkiler, salt ekleme denetim izi, rol atama denetimi, kısıtlı güvenlik tablosu.
- `tests/test_postgres.py` ile doğrulananlar:
  - 8 yetki sınırı (ör. API golden record'a yazamaz, okuyucu kısıtlı tabloyu göremez, uzlaştırıcı CI silemez, denetçi yazamaz).
  - Denetim izinin değiştirilemezliği.
  - SoD.
  - Kendine yetki verememe.
- `evren_core.web.SECURITY_HEADERS`: CSP (`default-src 'none'`, inline yok), `X-Frame-Options DENY`, `nosniff`, `no-referrer`, COOP/COEP/CORP. Tarayıcı testinde CSP ihlali olursa test başarısız olur.
- Arayüzde `innerHTML`/`eval` yasağı birim testinde ve güvenlik iş akışında denetlenir.
- `.github/workflows/security.yml`: gitleaks (geçmiş dahil), pip-audit, bandit (yüksek eşik), Trivy (dosya sistemi + yanlış yapılandırma + sır), CycloneDX SBOM. Sürümler SHA-256 ile doğrulanır.
  - `.gitleaks.toml` istisnası yalnızca genel anahtar kuralı ve sentetik `source_key` değerleriyle sınırlıdır; iş akışındaki olumsuz test, istisnanın gerçek bir belirteci gizlemediğini her çalıştırmada doğrular (yol bazlı istisna bu nedenle reddedildi).
  - Bağımlılıklar hash'li ve 14 günlük tedarik zinciri bekleme süresiyle kilitlenir (ADR-0005).
- API (`services/api`, ADR-0010): her bağlantı `evren_app_api` rolüne geçer; sorgu zaman aşımı; tüm dinamik SQL `psycopg.sql` ile kurulur; Swagger/ReDoc (dış CDN) kapalı.
  - `services/api/tests`: 34 test — yetki reddi ve denetimi, CSRF, idempotency, sütun düzeyi yetki, Entra akışının olumsuz senaryoları (tekrar, MFA bağlamı yok, yanlış kiracı/düzenleyen/hedef kitle, sahte imza, HS256'ya algoritma değişimi, kurcalanmış çerez, SoD).
- Uzlaştırıcı (`services/reconciler`): `evren_app_reconciler` rolüyle; denetim izini okuyamaz, CI ve kimlik anahtarı silemez (testli).

## Ağ ve platform

- Servisler arası mTLS; bağlayıcı, uygulama ve veri bölütleri ayrı.
- ServiceNow'a giden trafik ayrı çıkış kuralı; SN'den gelen geri çağrılar DMZ ağ geçidi veya MID Server üzerinden.
- Konteynerler root olmayan kullanıcıyla, salt okunur kök dosya sistemiyle, `cap_drop: ALL` ve `no-new-privileges` ile çalışır.
- PostgreSQL: TLS zorunlu, `scram-sha-256`, disk şifreleme, şifreli PITR yedekleri, düzenli geri yükleme tatbikatı.
- Sırlar kurum sır kasasından dosya olarak bağlanır (`*_FILE`) ve döndürülebilir.

## Kişisel veri (KVKK)

- CMDB'deki kişisel veri iş gereğiyle sınırlıdır: CI sahibi ve destek grubu üyeleri.
- `ref.sys_user` yalnızca gereken alanları tutar.
- Loglarda kişisel veri tutulmaz.
- Saklama süreleri veri sınıfına göre belirlenir.

## Standart eşlemesi (özet)

| Çerçeve | İlgili denetim | Evren karşılığı |
| --- | --- | --- |
| ISO/IEC 27001:2022 A.5.9 | Bilgi varlıkları envanteri | CMDB'nin kendisi; kapsam boşluğu raporları |
| A.5.15 / A.5.18 / A.8.2 | Erişim kontrolü, ayrıcalıklı erişim | RBAC+ABAC, SoD, break-glass, rol denetimi |
| A.8.9 | Yapılandırma yönetimi | Golden record, kaynak bilgisi, yetkisiz değişiklik tespiti |
| A.8.15 / A.8.16 | Kayıt ve izleme | Salt ekleme denetim izi, SIEM |
| A.8.25–A.8.28 | Güvenli geliştirme | CI güvenlik kapıları, kod gözden geçirme komutu, ASVS |
| ISO/IEC 20000-1 8.2.6 | Yapılandırma yönetimi | CI tanımı, ilişkiler, doğrulama ve denetim |
| ISO/IEC 42001 | Yapay zekâ yönetim sistemi | docs/11 |

## Açık işler

- Kurum sır kasası ve SIEM ürününe göre entegrasyon (açık soru).
- Dağıtım ortamına göre ağ politikaları (Kubernetes NetworkPolicy veya güvenlik duvarı kuralları).
- Sızma testi: F2 öncesi, ServiceNow'a yazma başlamadan.
