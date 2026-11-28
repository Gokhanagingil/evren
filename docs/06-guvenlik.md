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
| T | Denetim izinin değiştirilmesi | UPDATE/DELETE/TRUNCATE engelli tetikleyici (uygulama rolleri); ayrıcalıklı kullanıcıya (tablo sahibi, veritabanı yöneticisi) karşı tespit: akış başına saatlik zincirli mühür, görev çalışanının günlük ve Denetçinin elle doğrulaması (ekleme, silme, değiştirme, mühür kaldırma, dönem atlama yakalanır); mühür ve doğrulama `evren.audit.v1` ile SIEM'e gider (veritabanı dışı tanık) (ADR-0039) | `0003_security.sql`, göç 0034, `evren_core/audit.py`, test_audit, test_audit_seal_pg |
| T | SQL enjeksiyonu (sysparm_query) | Parametreli SQL; alan adları sözlükten; `javascript:` reddi | `encoded_query.py` |
| **R**epudiation | "Bu değişikliği ben yapmadım" | `audit.ci_change` (aktör, kaynak, gerekçe, korelasyon); yetki değişiklikleri `audit.security_event`; her güvenlik olayında istek kimliği (`X-Request-Id`) ve kaynak IP, aynı isteğin olayları birbirine bağlı (ADR-0039) | DB, api `deps.py` |
| **I**nformation disclosure | Güvenlik duruşu verisinin sızması | Ayrı kısıtlı tablo; alan hassasiyeti (`sensitivity`); rol bazlı okuma; okuma denetimi | `ci_security_posture`, IAM |
| I | Hata mesajlarında iç ayrıntı | Standart hata gövdesi; yığın izi yalnızca sunucu logunda | api |
| I | Yapay zekâ modeline hassas veri gitmesi | Onaylı/kurum içi model; veri sınıflandırması; asgari bağlam | docs/11 |
| I | Sistem durumunun (sürüm, bağlantı sayısı, kuyruk, servis adları) her kullanıcıya açılması | Yalnızca `platform` okuma izni (Platform operatörü, Veri sorumlusu, Denetçi); yanıtta sır ve bağlantı bilgisi yok; API olay içeriğini (`payload`) okuyamaz, yalnızca kimlik ve zaman sütunlarını (ADR-0032) | api `system.py`, göç 0029 |
| I | Geçmiş bir andaki değerin veya ilişki haritasının yetkisiz kişiye gösterilmesi | Geçmiş de bugünkü okuma izni ve alan duyarlılığıyla süzülür; okunamayan sınıftaki karşı CI'ın adı dönmez, haritada üzerinden genişlenmez (ADR-0031) | api `timeline.py`, `graph.py` |
| I | Teknik belgelerin (mimari, tehdit modeli) oturumsuz okunması veya belge adıyla dizin dışına çıkılması (`../`, mutlak yol) | Belge uç noktası oturum ister; ad izinli biçimde (`adr/` öneki, harf, rakam, nokta, tire; `..` yasak) ve `docs/` altında çözülür, değilse 400/404; içerik Markdown olarak döner, arayüz HTML dizgesi üretmeden DOM'a çevirir, dış adresler tıklanmaz (ADR-0033) | api `admin.py`, `lib/markdown.js` |
| I | "Size düşen işler" listesinin kişinin yetkisi olmayan işleri (ör. yetkisiz değişiklik, bağlayıcı kimliği) sayıyla da olsa göstermesi | Her satır kişinin ilgili izni varsa sayılır; bağlayıcı kimlikleri belirteç özeti içermeyen görünümden okunur; arayüzdeki görev kilidi yalnızca yönlendirmedir, asıl denetim API'de ve veritabanı rollerinde (ADR-0033) | api `admin.py` |
| **E**levation of privilege | Yetki yöneticisinin kendine veya üyesi olduğu gruba rol vermesi, kendi rolünü geri alması | API kişinin ham kimliğini "veren" olarak yazar; veritabanındaki `granted_by <> principal_id` kuralı ve API'deki grup üyeliği denetimi; kendi kişi kaydı değiştirilemez; rol olayı gerekçeyle denetim izinde (ADR-0034) | api `iam.py`, `0001_core.sql`, `0030_iam_admin.sql` |
| E | Geri alınan rolün oturum süresince (8 saat) geçerli kalması | Veritabanı rolleri çerezde tutulmaz, her istekte okunur; görevler ayrılığı her istekte denetlenir (ADR-0034) | api `deps.py`, `security.py` |
| E | Yetkili rolün süresiz kalması; makine rolünün kişiye verilmesi | Yetkili rol en çok 90 gün (break-glass 1 gün); makine rolü kişiye/gruba verilmez; süresi 14 gün içinde dolacak atamalar Yetki yöneticisine düşer | api `iam.py`, `admin.py` |
| T | ServiceNow kaynaklı kişi kaydının Evren'den değiştirilmesi veya ServiceNow eşlemesinin Entra kimliğini ezmesi | API yalnızca kendi açtığı kişiyi yazar, diğerinde yalnızca Entra kimliğini bağlar (tetikleyici); eşleme `entra_oid` sütununa yazamaz (sütun yetkisi) | `0030_iam_admin.sql`, `0020_reference_ingest.sql` |
| T | Görev kanalının ServiceNow'da standart dışı alana veya tabloya yazması; ServiceNow'da kural/ACL değişikliği gerektirmesi | Yalnızca standart görev alanları (izin listesi, kodda denetlenir ve testli); hedef tablo adı doğrulanır (`sys_`, `cmdb` reddedilir); `correlation_id` ile tekillik (ADR-0035) | `evren_core.governance`, `services/governance` |
| E | Görev çalışanının ele geçirilmesiyle CMDB'ye yazma veya karar verme | `evren_app_governance`: okuyucu + görev teslim sütunları + nabız; karar (durum, sonuç), kanal ayarı ve CMDB yazımı kapalı (testli) | `0031_governance_tasks.sql`, test_postgres |
| D/T | Yanlış kampanya koşuluyla binlerce görev açılması; koşuldan SQL enjeksiyonu | Koşul alanları yalnızca metadata sözlüğünden, değerler parametre; önizleme önce; kapsam sınırı (varsayılan 2000, en çok 20 000) aşılırsa çalışma görev açmadan durur; CI başına tek açık görev (tekillik anahtarı) (ADR-0036) | `evren_core.governance.campaign_scope`, `services/governance` |
| I/S | Entegrasyon sihirbazının sır değeri toplaması veya kaynak adresini/dosya yollarını saklaması; sihirbazın ürettiği bloğun kötü yol içermesi | Planda sır alanı yok (`extra="forbid"`), sır anahtarları reddedilir; yollar mutlak, `..` yok; adres https; plan ucu saklamaz ve günlüğe yazmaz (ADR-0021 §2 korunur); belirteç yalnızca kimlik adımında, adımdan çıkınca pencereden silinir; doğrulayıcı çalıştırıcıyla ortak (ADR-0038) | `evren_core.connectors.instances`, api `integrations.py` |
| D/T | Kural koşulundan SQL enjeksiyonu; yanlış kuralla binlerce bulgu ve görev; sağlıksız kaynak yüzünden toplu yanlış bulgu | Kural türleri sabit katalog, parametreler sözlükten doğrulanır (serbest SQL yok), değerler parametre; önizleme önce; bulgu sınırı aşılırsa değerlendirme hiçbir şeyi değiştirmez; rol bağlı kuralda kaynak sağlıksızsa "sonuç yok" (ADR-0037) | `evren_core.quality`, `services/governance` |
| E | Görev çalışanının istisna vermesi veya kural değiştirmesi; API'nin bulgu açıp kapatması | Çalışan bulgu ve ölçüm yazar, istisna sütunlarına ve kural tanımına yazamaz; API kural, durum ve istisna yazar, bulgu açamaz/silemez; istisna süre ve kimlik ister, kapanan bulgu yeniden açılmaz (sütun yetkisi + tetikleyici, testli) | `0033_quality_rules.sql`, test_postgres |
| E | Görev çalışanının kampanya tanımını veya durumunu değiştirmesi; API'nin çalışma sonucunu yazması | Görev çalışanı yalnızca zamanlama sütunlarını ve çalışma sonucunu yazar; API tanım, durum ve çalışma isteği yazar; kampanya silinmez (sütun yetkisi, testli) | `0032_governance_campaigns.sql`, test_postgres |
| I | Başkasının görevinin (CI, kişi) görülmesi | Görev yalnızca sorumlu kişiye/grubuna, Konfigürasyon yöneticisine ve Denetçiye görünür; diğerlerine 404 (varlığı sızmaz) | api `tasks.py` |
| I | ServiceNow yazma kimliğinin sızması | Sır yalnızca görev çalışanında, dosyadan; hata iletisi sır ve sunucu gövdesi içermez; panelden değişen kanal ayarı sır içermez | `services/governance`, `connectors/http.py` |
| **D**enial of service | Büyük sorgu veya derin etki analizi | Koşul ve IN sınırı, sayfalama, sorgu zaman aşımı, derinlik sınırı, hız sınırı; geçmiş an sorgusu 20 000 değişiklikte durur, harita 3 adım ve 120 düğümle sınırlı | api, mcp |
| T | Kaynağın ilişkiyi bırakmasının veya kısmi çalışmanın ilişkileri silmesi | İlişki yalnızca tam ve devre kesicisi OK çalışmada, kaynak kaydı geldiği hâlde listelemiyorsa kalkar; başka bildireni varsa kalmaz; kalkma değişim geçmişinde (ADR-0031) | reconciler |
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
| T | Elle düzenlemeyle sözlük dışı alana veya başka kaynak adına yazma | Alan adları yalnızca sözlükten; tip/seçim/uzunluk/referans doğrulaması; API yalnızca istek yazar, `source_record` yazma yetkisi geri alındı (0008); uzlaştırıcı rol önceliğini uygular | `evren_api/editing.py`, test_edit |
| I | Geçersiz alan adının hata yanıtında yansıtılması | Yalnızca tanımlayıcı biçimindeki adlar yanıta girer; diğerleri "(geçersiz ad)" | `evren_api/editing.py` |
| D | Uygulanamayan (zehirli) bir isteğin uzlaştırıcı kuyruğunu kilitlemesi | İstek başına kayıt noktası; beklenmeyen hata yalnızca o isteği hata kimliğiyle reddeder; geçici hata yeniden denenir | `ReconcilerStore._drain`, test_worker_pg |
| I | Çalışan günlüğünde bağlantı adresinin (parola) görünmesi | Adres yalnızca dosya/ortamdan okunur; yapılandırma `repr`'ında yok; bağlantı hatalarında yalnızca hata sınıfı yazılır (testli) | `evren_reconciler/worker.py` |
| T | GRC'ye giden kanıtın sonradan değiştirilmesi | `insight.evidence_snapshot` salt ekleme (sahibi dahil güncelleme/silme/TRUNCATE tetikleyiciyle engelli); kararlı JSON + SHA-256; indirmede özet yeniden doğrulanır | `0010_shadow_report.sql`, test_report |
| I | Raporda okuma yetkisi olmayan sınıf veya hassas alanın görünmesi | Sınıflar okuma iznine göre süzülür; değer farkı öznitelikleri okuma izninin hassasiyet sınırıyla süzülür | `routers/reports.py` |
| E | Şema değişikliğini hazırlayanın kendi setini onaylaması veya yayınlanmış seti değiştirmesi | API (403), veritabanı kısıtları (onaylayan/reddeden ≠ hazırlayan), tetikleyici (izinli durum geçişleri, yayınlanan/reddedilen set ve işlemleri değişmez/silinmez, işlem yalnız taslağa); API'nin güncelleme yetkisi sütun düzeyinde | `0011_change_sets.sql`, test_schema |
| T | Metadata'nın denetimsiz değişmesi | Yayın yalnızca değişiklik dosyası üretir (SHA-256); dağıtım PR + CI doğrulaması (`load_metadata --check`) ile | ADR-0015 |
| R | Yetki reddinin denetim kaydının işlemle birlikte geri alınması | Ret olayı istek işlemi içindeyse ayrı bağlantıyla yazılır; ret testlerle denetim izinde doğrulanır | `evren_api/deps.py`, test_split |
| S | Sahte bağlayıcının veri göndermesi | Bağlayıcı belirteci (`evr_` + 32 bayt; yalnızca SHA-256 özeti saklanır), süre, iptal, son kullanım; ağda TLS zorunlu, isteğe bağlı mTLS | `evren_gateway`, test_gateway |
| E | Bir bağlayıcının başka kaynak adına yazması veya geçidi atlatması | Belirteç tek veri kaynağına bağlı (403 + denetim); emekli kaynak yazamaz; tüketici kaydın çalışmasını doğrular, çalışması olmayan veya başka kaynağa ait kayıt DLQ'ya | `evren_gateway`, `consumer.py`, test_gateway, test_ingest_pg |
| T | Kafka mesajının yeniden teslimiyle sayaç ve değişikliklerin çoğalması | Ofset veritabanı işleminden sonra saklanır; içerik özeti aynı → değişiklik yok; çalışma sayaçları kaynak kayıtlarından (anahtar başına tek satır) | ADR-0017, test_ingest_pg |
| D | Zehirli mesajın tüketiciyi kilitlemesi; büyük veya yoğun gönderim | DLQ (neden ve konum başlıkta); gövde, toplu gönderim ve kayıt boyutu sınırı (`Content-Length` zorunlu); kimlik başına hız sınırı (429) | `evren_gateway`, `messaging.py` |
| T | Kısmi veya terk edilmiş çalışmanın CI'ları emekliye ayırması | Bildirilen sayı teslim edilenden fazla olamaz; eksik veya terk edilmiş çalışma `completed=false` kapanır, devre kesici açık; delta çalışması "görülmeyeni eksik say" işlemine girmez | `store.finalize_ready_runs`, test_ingest_pg |
| I | Olay akışında hassas alan değerinin yayılması | `confidential`/`restricted` alan değerleri olaya konmaz (`redacted`); konu ACL'leri (üretim) | `messaging.redact_payload`, test_messaging |
| I | Kafka trafiğinin dinlenmesi veya Kafka parolasının sızması | İstemci varsayılanı SASL_SSL + ana bilgisayar adı doğrulaması; PLAINTEXT yalnızca açık geliştirme izniyle; parola yalnızca dosyadan, `repr`'da yok (testli) | `messaging.KafkaSettings` |
| I | Aramada okuma yetkisi olmayan sınıfın veya görülmeyen alanın sızması | Okunabilen sınıflar sorgu süzgeci, görülebilen alanlar arama alanı; yanıt sınıf bazında yeniden süzülür (motor süzgeci atlansa bile); gizli hassasiyetteki alan dizine hiç girmez | `routers/search.py`, test_search_api, test_search_os |
| T | Arama sorgusuyla motor sözdizimi enjeksiyonu | Kullanıcı metni yalnızca JSON değeri olarak (`term`, `prefix`, `multi_match`); `query_string` yok; dizin adı doğrulanır; yedek SQL'de joker karakterler kaçırılır ve parametrelidir | `evren_core/search.py`, `opensearch_client.py` |
| E | Dizinleyicinin ele geçirilmesiyle veri değiştirme | `evren_app_indexer`: okuyucu + kaynak bağının üç sütunu + kendi nabzı; CI, olay, kaynak kaydı, kimlik bilgisi ve kısıtlı tabloya yazamaz/okuyamaz (testli) | göç 0013, test_indexer_pg, test_postgres |
| I | OpenSearch kimlik bilgisinin sızması veya trafiğin dinlenmesi | HTTPS + zincir/ana bilgisayar doğrulaması varsayılan; düz HTTP yalnızca açık geliştirme izniyle; kimlik adreste reddedilir, parola yalnızca dosyadan, `repr`'da ve hata iletisinde yok (testli) | `opensearch_client.py`, test_search |
| E | Grup üyeliği yoluyla görevler ayrılığı çatışması | Girişte uygulama rolü + grup ataması birleşimi `iam.sod_constraint` ile denetlenir; çatışmada giriş reddedilir ve denetime yazılır | `routers/auth.py` |
| S | Konteyner içinde dev oturumunun dışarıya açılması | Dev modu 0.0.0.0'a yalnızca açık `EVREN_DEV_CONTAINER=1` ile bağlanır; bu modda loopback ve özel ağ dışından gelen her istek 403; Entra modunda bayrak yok sayılır; compose portları yalnızca 127.0.0.1 (CI denetler) | `evren_api/app.py`, test_dev_container |
| E | Bir servisin ele geçirilmesiyle başka servisin yetkisine erişim | Servis başına LOGIN rolü, `NOINHERIT`, yalnızca kendi grup rolü, bağlantı sınırı; sahip rolü yalnızca göç işinde | `tools/migrate.py`, test_migrate_pg |
| T | Uygulanmış göçün değiştirilmesi veya eski imajın yeni şemaya karşı çalışması | Göç defteri (sürüm + SHA-256); içerik farkı veya bilinmeyen sürümde kurulum durur | `tools/migrate.py`, test_migrate_pg |
| T | Konteyner imajına veya tedarik zincirine müdahale; bilinen zafiyetle yayın | Temel ve dış görüntüler özetle sabit (CI denetler); bağımlılıklar `--require-hashes --no-deps`; derleme anında zorunlu Debian güvenlik güncellemesi; Trivy imaj kapısı (düzeltmesi olan YÜKSEK/KRİTİK, sır) + imaj SBOM'u; uid 10001, salt okunur kök, `cap_drop: ALL`, `no-new-privileges` (CI denetler); kurumsal CA yalnızca derleme sırrı. İmzalama hedef ortamla (U10) | `deploy/`, ADR-0016 |
| I | Kaynak veya geçit kimlik bilgisinin bağlayıcıdan sızması (komut satırı, günlük, hata iletisi, kayıt dosyası) | Sırlar yalnızca dosyadan veya standart girdiden; adreste kullanıcı bilgisi reddedilir; `repr` yalnızca "auth=var/yok"; hata iletisinde başlık, sorgu dizesi ve sunucu gövdesi yok; Zabbix hata iletisi yazılmaz (yalnızca kod); kayıt dosyası yalnızca yöntem + yol + gövde özeti ve yanıt gövdesi tutar (testli) | `connectors/http.py`, test_connector_runtime |
| I | Kayıt dosyasında parola özeti veya oturum kimliği kalması (çevrimdışı parola tahmini, oturum çalma) | İstek parmak izinde kimlik alanları (`password`, `userName` …) özetten önce maskelenir; gizli yanıt başlıkları (oturum kimliği, `set-cookie`) yer tutucuyla, istek başlıkları hiç kaydedilmez; oynatma gerçek kimlik istemez (testli; vCenter testinde bulunup düzeltildi) | `connectors/http.py`, test_connector_vcenter |
| S/I | vCenter oturumunun açık kalması veya kimliğin gereğinden geniş kullanılması | Parola yalnızca `Login` gövdesinde; oturum kimliği yalnızca bellekte, iş bitince ve hata durumunda `Logout`, görünümler `DestroyView` ile kapatılır (testli); servis hesabına salt okunur rol | `sources/vcenter.py` |
| T | İki vCenter'ın aynı `moId`'li nesnelerinin tek CI'da birleşmesi | Kaynak anahtarı ve ilişki hedefi `<vCenter örnek kimliği>/<moId>` (testli) | `sources/vcenter.py`, `esx_host.mapping.yaml` |
| T | ServiceNow kimliğiyle herhangi bir CI'ın sys_id'si hedeflenerek kayıt gönderilmesi | Beklenen davranıştır: replika tüm sys_id'leri bilir (ADR-0004). Belirteç yalnızca ServiceNow kaynağına bağlıdır. ITSM rolü yalnızca uzlaştırma kurallarının ona verdiği alanları yazar. Her değişiklik kaynak ve çalışma kimliğiyle denetim izindedir. Gölge veya devredilen kaynak CI açamaz (testli) | `ReconcilerStore.apply`, test_connector_servicenow_pg |
| I | ServiceNow hata gövdesinin (iç ayrıntı) günlüğe veya rapora sızması | Hata iletisinde yalnızca yöntem, yol ve durum kodu; `error.message`/`detail` yazılmaz (testli) | `sources/servicenow.py`, test_connector_servicenow |
| I | Kaynağın döndürdüğü sayfa bağlantısıyla (ör. BMC `next`) belirtecin başka bir adrese gitmesi | Sayfa adresi sunucudan alınmaz; istemci kendi temel adresiyle kurar (`results_id` + `offset`) | `sources/bmc_discovery.py` |
| I | Entra istemci sırrının veya erişim belirtecinin kayıt dosyasına ya da günlüğe girmesi | Sır yalnızca dosyadan ve yalnızca belirteç isteğinin form gövdesinde; form gövdesindeki kimlik alanları parmak izinde maskelenir; yanıttaki `access_token`/`refresh_token`/`id_token` kayda yer tutucuyla yazılır; belirteç iş sonunda istemciden silinir; hata iletisinde Entra hata açıklaması yok (testli) | `connectors/http.py`, `sources/defender.py`, test_connector_defender |
| I | Defender risk/maruziyet verisinin CI özniteliği olarak genel okuyucuya açılması | Eşleme bu alanları almaz (testli); yalnızca `cmdb.ci_security_posture` (F1-02) | `connectors/defender/mapping.yaml` |
| D/I | Kaynağın XML yanıtıyla varlık genişletme (billion laughs) veya XXE (yerel dosya okuma, SSRF) | `safe_xml`: expat üzerinde DOCTYPE, varlık tanımı ve dış varlık reddedilir; derinlik 64, boyut 64 MB; `xml.etree` doğrudan kullanılmaz (testli: iç varlık, dış varlık, dış DTD, tanımsız varlık, derinlik) | `connectors/safe_xml.py`, test_connector_nnmi |
| T | Kaynağın sıralama güvencesi olmadan sayfalanması nedeniyle kayıt atlanması (sessiz eksik veri) | NNMi kimlikleri artan sırada gelmezse çalışma durur ve tamamlanmamış kapanır; devre kesici yıkıcı işleri durdurur (testli) | `sources/nnmi.py` |
| I | Arayüzde bağlayıcı belirtecinin veya özetinin görünmesi | API rolü kimlik tablosunu okuyamaz; yalnızca belirteç özeti içermeyen `connector_credential_status` görünümünü okur; liste ve çalışmayı açan kimlik `connector_read` izniyle (Veri sorumlusu, Denetçi); yanıtta belirteç/özet olmadığı testli | göç 0018, `routers/platform.py`, test_api, test_web_api |
| S | Sahte kaynak veya ortadaki adam bağlayıcıya veri verir | Kaynakta yalnızca `https`, zincir ve ana bilgisayar adı doğrulaması, kurum CA paketi (`EVREN_SOURCE_CA_FILE`); düz http kaynak için hiç, geçit için yalnızca açık geliştirme izniyle | `connectors/http.py`, ADR-0020 |
| T | Yarıda kalan çekme veya reddedilen gönderimle "tamamlandı" bildirilmesi (CI'ların yanlışlıkla eskimesi) | `completed` yalnızca sayfalama sonuna kadar hatasız, hiçbir toplu gönderim reddedilmemiş ve eşleme hatası yoksa; aksi hâlde çalışma `completed=false` ve hata sayısıyla kapanır, devre kesici yıkıcı işleri durdurur (birim + geçitle uçtan uca test) | `connectors/runtime.py`, test_connector_e2e |
| D | Kaynağı yorma veya aşırı büyük yanıtla bağlayıcıyı çökertme | Sayfa boyutu, tek yanıt için 64 MB sınırı, sınırlı geri çekilmeli yeniden deneme, `Retry-After` (üst sınır 120 sn) | `connectors/http.py` |
| I | Kuru çalıştırma veya kayıt dosyalarında kurum verisinin dışarı çıkması | Dosya yalnızca açık `--out`/`--record` ile yazılır; belgede kurum dışına çıkarılmaz; repoda yalnızca "SENTETİK" işaretli örnek veri (testli) | `tools/connector_run.py`, ADR-0020 |
| E | API'deki bir açıkla kaynak sırlarına (vCenter parolası, Defender istemci sırrı, Zabbix belirteci) ulaşılması | API bağlayıcıyı çalıştırmaz, istek yazar; kaynak sırları yalnızca ayrı çalıştırıcı servisinin makinesinde, dosyadan; örnek yapılandırması veritabanına sırsız özetle gider (adres, kullanıcı, dosya yolu yok; testli) | ADR-0021, `services/connector-runner`, test_runner_pg |
| E | Çalıştırıcı servisinin ele geçirilmesiyle CMDB'nin değiştirilmesi | Ayrı rol `evren_app_connector`: CMDB'yi okuyamaz/yazamaz, kaynak kaydı ve kimlik tablosuna erişemez, isteğin içeriğini değiştiremez; veri yalnızca geçitten (kural 1–2; yetki testleri) | göç 0019, test_postgres, test_runner_pg |
| I | Alt süreç (bağlayıcı) ortamından veritabanı adresinin veya başka sırların sızması | Alt sürecin ortamı en az hâliyle kurulur: geçiş listesi (PATH, vekil, CA) + örneğin dosya yolları; çalıştırıcının veritabanı adresi aktarılmaz (testli); kabuk yok, sabit argüman listesi | `runner.py`, test_runner_pg |
| I | Bağlayıcı hata iletisinde kaynak adresi veya dosya yolunun arayüze çıkması | Sonuç notu en çok 300 karakter; örneğin adresi, kullanıcı adı ve yolları yer tutucuyla, kalan URL'ler `‹adres›` ile değiştirilir; istekte yalnızca sayılar saklanır (testli) | `runner.redact`, test_runner_config, test_runner_pg |
| D | "Şimdi çalıştır" ile kaynağı yorma veya istek seli | `connector_admin` izni; örnek başına tek etkin istek (benzersiz dizin, 409); zamanlanmış dakika bir kez; çalıştırıcı başına eşzamanlılık sınırı; çalışma başına süre sınırı; 24 saati geçen bekleyen istek reddedilir | göç 0019, `runner.py` |
| T | Çalıştırma isteğinin sonucunun veya kökeninin sahtelenmesi | Durum geçişleri tetikleyiciyle (sonuçlanmış istek değişmez); API köken sütununa yazamaz (elle istek `manual`), durum yazamaz, iptal yalnızca işlevle; her istek, iptal ve başlangıç denetim izinde | göç 0019, test_postgres |
| I | Arayüzden verilen geçit belirtecinin sızması (günlük, önbellek, tekrar güvenliği kaydı, tarayıcı) | Belirteç API'de üretilir; veritabanına yalnızca SHA-256 özeti `SECURITY DEFINER` işlevle gider; yanıt `no-store`; tekrar güvenliği kaydı ve denetim izi belirteçsiz (tekrarda belirteç dönmez); iletişim kutusu kapanınca alan temizlenir ve DOM'dan kalkar (testli) | göç 0019, `routers/connectors.py`, test_api_connectors, test_web_api |
| E | Emekli veya bağlayıcısız kaynağa kimlik verilmesi, bozuk özet | İşlev ad, kaynak, yaşam döngüsü, süre (1–730 gün) ve özet biçimini denetler; bağlayıcısız kaynak (Manual, Evren) API'de reddedilir (testli) | göç 0019, test_postgres, test_api_connectors |
| I | ServiceNow referans yüklemesiyle gereğinden fazla kişisel verinin Evren'e girmesi | Yalnızca izinli sütunlar istenir (`sysparm_fields`, tek liste `evren_core/reference.py`); kullanıcıda kullanıcı adı, ad, e-posta, departman, etkinlik. Tabloda olmayan sütun reddedilir (testli; KVKK veri en aza indirme) | ADR-0022, test_reference, test_connector_servicenow |
| E/T | Referans yolundan CMDB'ye, satır kaynağına veya Entra alanlarına yazılması; Entra/Evren satırının ezilmesi | Referans kaydı kimlik çözümlemesine ve CI uzlaştırmasına girmez; uzlaştırıcıya `ref` şemasında yalnızca listedeki sütunlar için yetki (`source`, `entra_oid`, `entra_group_id`, grup üyeliği, silme yok; yetki ↔ liste eşitliği testli); güncelleme yalnızca `source='servicenow'` satırında; değer türü uymayan kayıt yazılmaz | göç 0020, `ReconcilerStore.apply_reference`, test_postgres, test_connector_servicenow_pg |
| I | Kısıtlı güvenlik duruşunun (risk, maruziyet) kaynak kaydına, CI özniteliğine, değişim geçmişine, arama dizinine veya olaya sızması | Zarfta ayrı `posture` bölümü; eşleme duruşu özniteliğe koymaz; kaynak kaydı yalnızca öznitelik/lookup/kimlik/ilişki saklar; uzlaştırıcı yalnızca `cmdb.ci_security_posture`'a yazar; dizinleyici tabloyu okuyamaz (testli: kaynak kaydı, CI ve `audit.ci_change` metninde duruş yok) | ADR-0023, test_mapping, test_connector_servicenow_pg |
| I/R | Duruşun yetkisiz görüntülenmesi; kimin baktığının bilinmemesi | API yalnızca `restricted` okuma yetkisiyle (Güvenlik okuyucu, Denetçi, break-glass) döndürür, diğerlerine bölümün varlığı da gösterilmez; her gösterim `audit.security_event` `restricted_read` (testli) | `routers/ci.py`, test_api_posture, test_web_api |
| T | Başka rolden veya gölgedeki üründen gelen duruşun kısıtlı tabloya yazılması | Yalnızca `posture_source` rolü (EndpointSecurity), etkin/devredilen kaynak; devirde rol içi sırası küçük kaynak kazanır; diğerleri yok sayılır ve günlüğe yazılır (testli) | `ReconcilerStore._write_posture`, test_connector_servicenow_pg |
| I | Kafka konusu ve DLQ'da taşınan kısıtlı veri | Konu yalnızca iç ağda; üretimde TLS + konu ACL'i + saklama süresi (U10) | ADR-0023, ADR-0017 |
| I | Ham yük arşivinin (kişisel ve kısıtlı veri içerir) okunması | Fernet ile zorunlu şifreleme (anahtar dosyadan, anahtarsız arşiv açılmaz, düz metin seçeneği yok); dizin 0700, dosya 0600; arşiv yalnızca çalıştırıcı makinesinde, API ve arayüz göstermez; manifestte veri yok (testli) | ADR-0024, `connectors/archive.py`, test_archive |
| T | Arşivin kurcalanması veya yanlış anahtarla okunması | Parça SHA-256 özetleri manifestte; okuyucu önce özeti denetler, sonra çözer; uyuşmazlık ve yanlış anahtar açık hatayla durur, ileti içerik taşımaz (testli) | `ArchivedRun.records`, test_archive |
| E | Yeniden işleme veya temizlik yoluyla arşiv kökü dışına erişim (yol geçişi) | Yol bileşenleri `<kaynak>/<tarih>/<run_id>` biçimiyle ve kök altında olma şartıyla doğrulanır; temizlik yalnızca biçime uyan dizinleri siler, sembolik bağlara dokunmaz (testli) | `open_run`, `prune`, test_archive |
| I | Kişisel verinin gereğinden uzun saklanması | Saklama süresi (varsayılan 30 gün), çalıştırıcı günde bir kez siler; tek kişinin arşivden silinmesi desteklenmez (sınır yazılı) | ADR-0024, `Runner.prune_archive` |
| T | Arşivden yeniden işlemenin kural 1–2'yi atlaması | Arşiv bugünkü eşlemeyle yeni çalışma olarak alım geçidine gider; uzlaştırıcı yazar; eksik asıl çalışma `delta` olarak oynatılır (kaynakta yok tespiti yapılmaz) | `connector_run.py --from-archive`, test_archive |
| S/T | Değişiklik kaydını CI özniteliği gibi yazma veya başka rolün (ITSM, gölge ürün) değişiklik kaydı yazması | Değişiklik tabloları yalnızca `change_source` rolünün etkin/devredilen kaynağından; referans ve değişiklik yetenekleri ayrı; değişiklik rolü `ci_attribute_source: false`; uzlaştırıcıya yalnızca `CHANGE_TABLES` sütunlarında yetki, silme yok; durum/onay/tür veritabanında sözlük denetimli (testli) | ADR-0025, göç 0021, test_change_control_pg, test_postgres |
| R | Yetkisiz değişikliğin sonradan açılan değişiklik kaydıyla izsiz "aklanması" | Anomali silinmez; kapanırsa "sonradan kaydedilen değişiklik" notu, değişiklik numarası ve gözlem satırında `late` işareti kalır; GRC kanıtı izlenebilir (testli) | `ReconcilerStore._recheck_unauthorized`, test_change_control_pg |
| D/T | Eşleme hatası veya kaynak arızasının toplu sahte yetkisiz değişiklik üretmesi | Gözlem yalnızca kaynağın kendi kaydındaki değişimden; devre kesici çalışmayı açtıysa `suppressed` (anomali yok); çalışma kapanmadan karar verilmez (testli) | ADR-0025 §6, test_change_control_pg |
| I | Gözlenen değişiklik tablosundan (öznitelik geçmişi) okuyucu veya MCP ile sızıntı | `insight.config_change`'i `evren_readonly`/MCP okuyamaz; API alan hassasiyetine göre süzer; API `sync.sn_ci_map`'te yalnızca iki sütunu okur (testli) | göç 0021, test_postgres, `routers/ci.py` |
| R/E | Anomali kararının yetkisiz verilmesi veya inkâr edilmesi | `anomaly_review` izni (Veri sorumlusu), CSRF başlığı, yanlış alarmda gerekçe zorunlu, "kapandı" yalnızca sistemde; karar veren/zaman/gerekçe anomali satırında ve `audit.security_event`'te (testli) | `routers/insight.py`, test_api_change_control, test_web_api |
| T/E | Elle CI oluşturmayla golden record'a kuralsız yazım veya API'nin CI'ı doğrudan açması | API yalnızca `ingest.manual_create`'e istek sütunlarıyla yazar (sonuç ve durum yok); uzlaştırıcı Manual kaynak kaydı olarak uzlaştırır; alanlar sözlükten ve elle düzenleme politikasıyla doğrulanır; kök sınıf ve izinsiz sınıf reddedilir (testli) | ADR-0026, göç 0022, test_api_manual_create, test_postgres |
| T | Elle girdiyle mükerrer CI (var olanın kopyası, keşfin sonradan tanıyamayacağı CI) | Oluşturma kimlik çözümlemesinden geçer: kesin eşleşmede açılmaz ve zorlanamaz, belirsiz eşleşmede gerekçeli karar; açılan CI'ın kimlik anahtarları dizine girer, keşif kaydı ona bağlanır (testli) | `ReconcilerStore.apply_manual_creates`, test_manual_create_pg, test_web_api |
| T/E | Elle ilişkiyle kuralsız bağ veya keşfin yazdığı ilişkinin elle silinmesi | API ve uzlaştırıcı izinli ilişki kuralını zorunlu tutar (ters yön ve kuralsız ilişki reddedilir); kaldırma yalnızca elle girilmiş ilişkiye, satır yalnızca `Manual` kaynaklıysa silinir; API `cmdb_rel_ci`'a yazamaz; `cmdb_rel_ci/create` ve `delete` izinleri; iki uçta değişim geçmişi (testli) | ADR-0026 §5, göç 0023, test_manual_relation_pg, test_api_manual_relation |
| T | CSV içe aktarmada formül enjeksiyonu (`=HYPERLINK(…)` hücresinin sonradan bir elektronik tabloda çalışması) | `=`, `+`, `@`, sekme veya satır başıyla başlayan hücre satır hatasıdır ve yazılmaz; değer hiçbir zaman CSV olarak geri verilmez (testli) | `evren_api/importing.py`, test_api_import |
| T | CSV ile toplu bozulma (sütun kayması, yanlış sınıf, var olan CI'ın ezilmesi) | Önizleme hiçbir şey yazmaz, tanınmayan sütun gösterilir; onay dosyayı yeniden doğrular ve yalnızca geçerli satırları alır; her satır elle düzenleme politikası ve kimlik çözümlemesinden geçer; güncelleme (upsert) yoktur, var olan CI'a denk gelen satır "zaten var" olur; satır sonuçları toplu işte izlenir (testli) | ADR-0026 §6, göç 0024, test_api_import |
| D | Büyük istek gövdesi veya dosya ile API'nin bellek tüketmesi | ASGI katmanında 5 MB gövde sınırı (`413 istek_cok_buyuk`, güvenlik başlıklarıyla); CSV en çok 2 MB ve 5000 satır; önizleme yanıtı en çok 500 satır gösterir (testli) | `evren_api/app.py` `BodyLimit`, test_api_import |
| S/E | Sahiplik onayının sahibi olmayan biri tarafından verilmesi veya başkası adına onay | Üç koşul birlikte: `attest` izni (CI sahibi rolü), oturumun `ref.sys_user` kaydına eşlenmesi (Entra'da yalnızca değişmez `entra_oid`; UPN/e-posta kullanılmaz) ve kişinin CI'ın `owned_by`/`managed_by` alanında olması; uzlaştırıcı uygulama anında yeniden denetler (sahip değiştiyse uygulanmaz); Veri sorumlusu başkası adına onay veremez (testli) | ADR-0027, `routers/attestation.py`, test_attestation_pg, test_api_attestation |
| T/R | Onay alanlarının elle düzenleme, kaynak veya API ile yazılması; onayın inkârı | Alanlar düzenleme ve oluşturma formunda yok, uzlaştırma kuralında yalnızca Manual; API `cmdb_ci`'a ve istek sonucuna yazamaz; değişim geçmişinde neden `attestation` ve kişi, `audit.security_event` `ci_attest` (testli) | göç 0025, test_postgres |
| R | Süresi dolmuş veya eski sahibin onayının geçerli görünmesi | Okuma ve GRC kontrolü aynı kuralla süreyi ve sahip değişikliğini hesaplar; uzlaştırıcının dönemsel taraması alanı "Henüz incelenmedi" yapar, son onayın kanıtı kalır (testli) | `AttestationPolicy.state`/`state_sql`, `expire_attestations` |
| T/D | Kısmi veya hatalı keşif çalışmasının toplu yanlış emekliye ayırmaya yol açması | Kayıt yalnızca tam ve `OK` çalışmada `missing` olur; öneri bütün gözlemci kaynaklar görmüyor ve son çalışmaları `OK` iken ilerler, aksi hâlde bastırılır; golden record kendiliğinden değişmez; emekliye ayırma insan kararı ve uygulama anında yeniden denetim (testli tatbikat) | ADR-0028, `evaluate_staleness`, `apply_retirements`, test_staleness_pg |
| E/R | Emekliye ayırmanın yetkisiz verilmesi, inkârı veya CI'ın silinmesi | `retire` izni (Veri sorumlusu), CSRF, gerekçe zorunlu; API öneriyi ve sonucu yazamaz; CI silinmez, kimlik bağları kalır; değişim geçmişinde neden `staleness` ve kişi, `audit.security_event` `ci_retire_decision` (testli) | göç 0026, test_api_staleness, test_postgres |
| R | Kimin açtığının ve neden zorladığının bilinmemesi | İstekte kişi ve gerekçe (zorlamada veritabanı gerekçeyi zorunlu kılar), `audit.security_event` `ci_create` (toplu işte `ci_import`, dosya adı ve satır sayısıyla), kimlik bağında karar veren, değişim geçmişinde kişi ve neden `manual` (testli) | göç 0022, test_manual_create_pg |
| I | Sırların imaja, ortam değişkenine veya günlüğe sızması | Sırlar yalnızca dosyadan (`*_FILE`, compose `secrets`); `.dockerignore` sır dizinini dışlar; göç aracı psycopg hatalarında yalnızca sınıf yazar; geliştirme sırları rastgele ve git dışı | `tools/dev_secrets.py`, `tools/migrate.py` |


### Denetim izi (ADR-0039)

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| S | Kaynak IP'nin `X-Forwarded-For` ile sahtelenmesi | Başlığa yalnızca `EVREN_TRUSTED_PROXIES` listesindeki vekillerden gelince güvenilir; "her adres" yapılandırma hatası; liste boşsa doğrudan bağlanan adres | api `config.py`, `deploy/evren.py`, test_api_audit |
| I | Bir isteğin denetim bağlamının (istek kimliği, IP) sonraki isteğe taşınması | Oturum ayarı bağlantı havuza dönerken temizlenir; temizlenemezse bağlantı atılır | api `db.py` |
| I | Denetim izinin toplu dışarı çıkarılması | Yalnızca Denetçi (`audit_read`); dışa aktarma en çok 50.000 kayıt ve kendisi denetim olayı (kim, süzgeç, sayı); CI değişikliğinde görme yetkisi olmayan alanın değeri gizli | api `routers/audit.py`, test_api_audit |
| T | Dışa aktarılan CSV'de formül enjeksiyonu (kişi adı veya nesnede `=HYPERLINK(…)`) | `=`, `+`, `-`, `@`, `\|`, sekme veya satır başıyla başlayan hücrenin önüne `'` | `evren_core.audit.safe_cell`, test_audit, test_api_audit |
| T/R | Mühür ekleyen çalışanın zinciri bozması veya incelemenin sonradan değiştirilmesi | Çalışan yalnızca mühür ekler (sütun yetkisi), zincir tetikleyicisi boşluğu ve sahte bağı reddeder; mühür, doğrulama ve inceleme salt eklemedir (tablo sahibi için bile); API mühür yazamaz | göç 0034, test_postgres, test_audit_seal_pg |
| R | Kayıtların incelenmediği veya mühür sorunu varken "sorun yok" denmesi | İnceleme kaydı mühürleri doğrular, sayıları sabitler; mühür sorunu varken "Bulgu yok" reddedilir; 7 günü geçen inceleme Denetçiye iş olarak düşer | api `routers/audit.py`, `admin.py` |
| E | Platform yöneticisinin kendi izini görmesi veya incelemesi | Görevler ayrılığı: denetim izi yalnızca Denetçi rolünde; Platform yöneticisi ve Konfigürasyon yöneticisi 403 (testli) | `0002_iam_seed.sql`, test_api_audit |

### Uyarılar, bildirimler ve metrikler (ADR-0040)

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| I | SMTP parolası veya Teams web kancası adresinin sızması (veritabanı, günlük, arayüz, hata iletisi) | Yalnızca görev çalışanının sır dosyalarında; veritabanında yalnızca Teams hedef adı; hata iletileri adres ve sunucu yanıtı içermez; arayüz ve API yalnızca hedef adlarını görür (testli) | `evren_governance/config.py`, `delivery.py`, test_api_notifications |
| S/T | Sahte bildirimle kullanıcıyı yönlendirme (oltalama) | Bildirimi görev çalışanı üretir; API yalnızca "sınama" ekleyebilir (tetikleyici); bağlantı yalnızca uygulama içi (`#/…`, şema kısıtı); e-posta düz metin, Teams kartında Markdown kaçışlı; değiştirilemez | göç 0035, `evren_core/notify.py`, test_postgres, test_ops_notify |
| I | Başkasının bildirimini okuma veya okundu işaretleme | Gelen kutusu kişi, grup üyeliği ve etkin rollerle süzülür; okundu işareti yalnızca görünen bildirime eklenir (testli) | api `routers/notifications.py` |
| T/R | Uyarının sessizce kapatılması veya onayın inkârı | Uyarıyı yalnızca görev çalışanı açar ve çözer; API yalnızca onay sütunlarını yazar, onay bir kez ve gerekçeli; uyarı silinemez, çözülen değişmez; onay `audit.security_event` `alert_ack`; geçişler olay omurgasında | göç 0035, test_api_notifications |
| D | Bildirim seli ve dış kanalın tıkanması | Tekrar anahtarı (aynı olay ikinci bildirim üretmez); yönlendirmede önem eşiği; teslimde artan aralıkla yeniden deneme ve sınır | `notifier.py` |
| I | `/metrics` ile iç ayrıntının açılması | Belirteç dosyası yoksa 404; sabit süreli karşılaştırmalı Bearer; etiketlerde kişi, kimlik değeri veya sorgu metni yok (rota şablonu) | api `metrics.py`, test_api_notifications |
| E | İzleyicinin kendisinin durması (uyarı açılamaz) | Arayüzde "uyarı izleyicisi çalışmıyor" uyarısı; dışarıdan `evren_service_up{kind="governance"}` izlenir | Sistem Durumu, `/metrics` |

### Evren MCP sunucusu ve yapay zekâ önerileri (ADR-0043)

Yeni dış arayüz: kurum MCP uygulaması → Evren, `POST /mcp` (Streamable HTTP, JSON-RPC).

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| S | Kimliksiz veya sahte belirteçle araç çağrısı | Entra'da yalnızca taşıyıcı belirteç: RS256, kiracı, düzenleyen, hedef kitle (istemci veya `api://istemci`), süre; geçersiz belirteç 401 (testli) | `entra.verify_access`, test_api_mcp |
| T/E | Tarayıcıdaki oturumla siteler arası MCP çağrısı (CSRF, DNS yeniden bağlama) | MCP oturum çerezini kabul etmez; `Origin` farklıysa 403 (testli) | `routers/mcp.py` |
| E | Ajanın çağırandan fazla yetkiyle okuması veya kısıtlı alana erişmesi | Araçlar REST işlevlerini çağırır: etkin roller, alan hassasiyeti ve görevler ayrılığı aynı; rolsüz makine kimliği okuyamaz (testli) | `routers/mcp.py`, test_api_mcp |
| T | İstem enjeksiyonu: CI içeriğindeki talimatın yazma üretmesi | MCP'de yazma aracı yok; yanıtlar `content_is_data`; `initialize` yönergesi; öneri bile insan onayı ister (testli: açıklamadaki "tüm kayıtları sil" hiçbir kayıt üretmez) | `routers/mcp.py`, test_api_mcp |
| T/R | Yapay zekâ önerisinin denetimsiz uygulanması veya inkârı | Öneri `ai.proposal`'a; öneren karar veremez (kısıt); kabul kararı verenin yetkisiyle her zamanki yoldan; öneri silinmez, içeriği ve kararı değişmez; model, gerekçe ve karar saklanır; `mcp_call`, `proposal_create`, `proposal_decide` denetim izinde | göç 0037, `routers/proposals.py` |
| D | Ajanın aşırı istekle veya öneri seliyle sistemi yorması | İstek 256 KB, yanıt 60 KB, sonuç ve derinlik sınırları; önerici başına 500 bekleyen öneri | `routers/mcp.py`, `routers/proposals.py` |

### ServiceNow replikası (ADR-0042)

Yeni dış arayüz: Evren → ServiceNow, giden HTTPS ve IRE API. ServiceNow'dan Evren'e geri çağrı yoktur.

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| T | Evren CI'ının ServiceNow'daki başka bir CI'ın üzerine yazılması (yanlış kimlik eşleşmesi) | Önce doğrula, sonra yaz: doğrulanmamış eşlemede işlemeden deneme (`/identifyreconcile/query`); beklenmeyen kayıt, var olanla eşleşen yeni CI veya başka Evren CI'ına eşli kayıt **çelişki** olur ve yazılmaz; aynı kimlik anahtarlı ikinci CI aynı turda gönderilmez (testli) | `evren_core.replica.judge`, `evren_sn_sync`, test_sn_sync_pg |
| T/D | Kural veya kapsam hatasıyla binlerce ServiceNow kaydının tek turda değişmesi | Önce gölge mod (yazmaz), etkin mod gölge turu tamamlanmadan açılmaz; tur başına değişiklik sınırı aşılırsa tur bekletilir, gerekçeli onay bir kez (tetikleyici); kapsam dışı alan değişikliği freni tetiklemez | göç 0036, `routers/replica.py`, test_api_replica, test_sn_sync_pg |
| E | sn-sync'in CMDB'ye yazması veya kendi itmesini onaylaması | `evren_app_sync` CMDB'ye, ayara ve onay sütunlarına yazamaz; eski kaynak kaydı yazma yetkisi geri alındı (testli) | göç 0036, test_postgres |
| R | Mod, kapsam, onay veya çelişki kararının inkârı | Hepsi `platform/replica_admin` ister, gerekçeli ve `audit.security_event`'e yazılır (`replica_setting`, `replica_scope`, `replica_approve`, `replica_resolve`) | `routers/replica.py`, test_api_replica |
| I | ServiceNow yazma kimliğinin sızması | Sır yalnızca sn-sync'te, dosyadan (`EVREN_SN_REPLICA_*_FILE`); görev kanalının kimliğinden ayrı; hata iletisi sır ve sunucu gövdesi içermez; bilinen IRE hata kodları Türkçe açıklanır | `evren_sn_sync/config.py`, `connectors/http.py` |
| I | Kısıtlı veya Evren'e özgü verinin ServiceNow'a gitmesi | Yalnızca sözlükte `sn_sync`, ServiceNow'da da var olan ve kısıtlı olmayan alanlar; kişi/grup referansı yalnızca ServiceNow kaynaklıysa; Defender duruşu `cmdb_ci`'da değildir | `metadata/sn_sync.yaml`, `ReplicaWorker.fields_for`, test_replica |

### ServiceNow uyumlu Table API (ADR-0044)

Yeni dış arayüz: ServiceNow Table API'ye yazılmış betik ve entegrasyonlar → Evren, `GET /api/now/table/…` (salt okuma).

| Tehdit | Senaryo | Denetim | Nerede |
| --- | --- | --- | --- |
| S | Kimliksiz veya parolalı (temel kimlik doğrulama) istek | Entra erişim belirteci (MCP ile ortak doğrulama) veya oturum; temel kimlik doğrulama yok; geçersiz belirteç 401 (testli) | `routers/sn_table.py`, `routers/mcp.bearer_principal` |
| T/E | Encoded query ile SQL enjeksiyonu veya sunucu tarafı betik | Alan adları sözlükten, değerler parametre; `javascript:` reddedilir; dot-walk yok; koşul ve IN sınırları (testli) | `evren_core.encoded_query`, test_api_sn_table |
| I | Görülmeyen alanın süzgeçle sezdirilmesi (`salary>100` gibi ikili arama) | Hassasiyet sınırını aşan alan yanıtta yok ve sorguda/sıralamada kullanılamaz (400, testli) | `routers/sn_table._Plan` |
| I | Reddedilen alt sınıfın üst tablo veya ilişki tablosu üzerinden okunması | Üst tablo sorgusu yalnızca okunabilen sınıfları kapsar; ilişki iki ucu okunabiliyorsa görünür (testli) | `routers/sn_table._Plan` |
| I | Kişisel verinin toplu çekilmesi | `sys_user` yalnızca kullanıcı adı, ad, departman, etkinlik; e-posta yalnızca gizli düzeyde; kısıtlı güvenlik verisi bu yoldan okunamaz | `routers/sn_table.py` |
| D | Büyük sayfa veya pahalı sorguyla API'nin yorulması | Sayfa en çok 10000 kayıt, koşul 200; istek zaman aşımı veritabanı havuzunda | `routers/sn_table.py` |
| R | Yetkisiz okuma girişiminin izsiz kalması | 403 `audit.security_event`'e yazılır (`read:<tablo>`, `api: sn_table`, testli) | `routers/sn_table._Plan._need` |

## Uygulanan denetimler (bu depoda, testli)

- `0003_security.sql`: 7 grup rolü, şema ve tablo düzeyinde yetkiler, salt ekleme denetim izi, rol atama denetimi, kısıtlı güvenlik tablosu.
- `tests/test_postgres.py` ile doğrulananlar:
  - 8 yetki sınırı (ör. API golden record'a yazamaz, okuyucu kısıtlı tabloyu göremez, uzlaştırıcı CI silemez, denetçi yazamaz).
  - Denetim izinin değiştirilemezliği.
  - SoD.
  - Kendine yetki verememe.
- `evren_core.web.SECURITY_HEADERS`: CSP (`default-src 'none'`, inline yok), `X-Frame-Options DENY`, `nosniff`, `no-referrer`, COOP/COEP/CORP. Tarayıcı testinde CSP ihlali olursa test başarısız olur.
- Arayüzde `innerHTML`/`eval` yasağı birim testinde ve güvenlik iş akışında denetlenir. Kılavuz ve teknik belgeler için Markdown işleyici (`lib/markdown.js`) HTML dizgesi üretmez; tarayıcı testi `<script>`, `<img onerror>` ve `javascript:` bağlantısı içeren metnin düz metin kaldığını sınar (ADR-0033).
- `.github/workflows/security.yml`: gitleaks (geçmiş dahil), pip-audit, bandit (yüksek eşik), Trivy (dosya sistemi + yanlış yapılandırma + sır), CycloneDX SBOM. Sürümler SHA-256 ile doğrulanır.
  - `.gitleaks.toml` istisnası yalnızca genel anahtar kuralı ve sentetik `source_key` değerleriyle sınırlıdır; iş akışındaki olumsuz test, istisnanın gerçek bir belirteci gizlemediğini her çalıştırmada doğrular (yol bazlı istisna bu nedenle reddedildi).
  - Bağımlılıklar hash'li ve 14 günlük tedarik zinciri bekleme süresiyle kilitlenir (ADR-0005).
- API (`services/api`, ADR-0010): her bağlantı `evren_app_api` rolüne geçer; sorgu zaman aşımı; tüm dinamik SQL `psycopg.sql` ile kurulur; Swagger/ReDoc (dış CDN) kapalı.
  - `services/api/tests`: 68 test — yetki reddi ve denetimi, CSRF, idempotency, sütun düzeyi yetki, Entra akışının olumsuz senaryoları (tekrar, MFA bağlamı yok, yanlış kiracı/düzenleyen/hedef kitle, sahte imza, HS256'ya algoritma değişimi, kurcalanmış çerez, SoD).
- Uzlaştırıcı (`services/reconciler`): `evren_app_reconciler` rolüyle; denetim izini okuyamaz, CI ve kimlik anahtarı silemez (testli).
- Alım geçidi (`services/ingest-gateway`, ADR-0017): `evren_app_ingest` rolüyle; kaynak kaydına erişemez, çalışmayı kapatamaz, kimlik bilgisini iptal edemez (testli). `services/ingest-gateway/tests`: 20 test (kimlik, kaynak yetkisi, emekli kaynak, ya hep ya hiç, boyut/hız sınırı, Kafka kesintisi, sayı uyumu, başka kaynağın çalışması görünmez).
- Bağlayıcı çalıştırıcı servisi (`services/connector-runner`, ADR-0021): ayrı rol `evren_app_connector` (CMDB yetkisi yok), her çalışma ayrı alt süreç (süre sınırı, iptal, en az ortam). `services/connector-runner/tests` (33 test: yapılandırmada sır değeri reddi, https, gerçek alt süreçle uçtan uca, not temizliği, süre aşımı, iptal, zamanlama tekrarı, kurtarma, emekli kaynak).
- Bağlayıcı çalıştırıcı (`evren_core/connectors`, ADR-0020): veritabanı erişimi yoktur, tek çıkışı alım geçididir. `tests/test_connector_runtime.py` (30 test: sızıntısız hata, yeniden deneme sınırları, sırların dosyadan okunması, kayıt/oynatma, tamamlanma kuralı, yapılandırma hataları) ve `services/ingest-gateway/tests/test_connector_e2e.py` (gerçek geçitle: Kafka teslimi, yarıda kalan çekme, reddedilen gönderim, başka kaynağın kimliği).
- Yığın (`.github/workflows/ci.yml` → "Yığın", ADR-0016): imaj derlenir, compose yığını açılır, uçtan uca duman testi (API → uzlaştırıcı → golden record, Kafka konuları, OpenSearch) ve konteyner sıkılaştırması (uid, salt okunur kök, yetenekler, port bağlama) ve Trivy imaj taraması her değişiklikte çalışır.

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
| A.8.15 / A.8.16 | Kayıt ve izleme | Salt ekleme denetim izi; tek görünüm, istek kimliği ve kaynak IP; zincirli mühür ve doğrulama; `evren.audit.v1` ile SIEM (ADR-0039); platform uyarıları, `/metrics` (ADR-0040) |
| A.5.28 | Kanıt toplama | Denetlenen dışa aktarma, mühür doğrulama ve dönem incelemesi kaydı |
| CIS Controls v8 8.2, 8.5, 8.9, 8.11 | Kayıt toplama, ayrıntılı kayıt, merkezi toplama, kayıt gözden geçirme | Birleşik denetim izi, SIEM konusu, haftalık inceleme kanıtı (`audit.review`) |
| A.8.25–A.8.28 | Güvenli geliştirme | CI güvenlik kapıları, kod gözden geçirme komutu, ASVS |
| ISO/IEC 20000-1 8.2.6 | Yapılandırma yönetimi | CI tanımı, ilişkiler, doğrulama ve denetim |
| ISO/IEC 42001 | Yapay zekâ yönetim sistemi | docs/11 |

## Açık işler

- Kurum sır kasası entegrasyonu ve SIEM ürününe özgü teslim (konu sözleşmesi hazır: `evren.audit.v1`; HEC veya Syslog/CEF ürün seçilince) — açık soru.
- Denetim kaydı saklama ve arşivleme politikası (mühürle birlikte soğuk depolama) ve harici zaman damgası (RFC 3161).
- Dağıtım ortamına göre ağ politikaları (Kubernetes NetworkPolicy veya güvenlik duvarı kuralları).
- Sızma testi: F2 öncesi, ServiceNow replikası etkin moda alınmadan (U10).
