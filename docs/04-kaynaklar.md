# 04 · Kaynak sistemler

Her kaynak için: neyi sağlar, hangi tanımlayıcılarla gelir, neye yetkilidir, tuzakları nelerdir. Eşleme dosyaları `connectors/<bağlayıcı>/`. Yeni kaynak eklemek için `/yeni-baglayici` komutunu kullan.

## Kaynak rolleri: ürün değişebilir, kurallar değişmez (ADR-0009)

Kaynak sistemler değişebilir ve bu listeyle sınırlı değildir. Bu yüzden uzlaştırma ve veri tazeleme kuralları ürün adını değil **kaynak rolünü** bilir. Kaynak kaydı: `metadata/data_sources.yaml`.

| Rol | Görev | Bugünkü ürün |
| --- | --- | --- |
| `Discovery` | İşletim sistemi, donanım, seri numarası, yazılım envanteri | BMC Helix Discovery |
| `Virtualization` | Sanal makine, hipervizör, küme; VM kaynakları | VMware vCenter |
| `Network` | Ağ cihazları, arayüzler, L2/L3 topoloji | OpenText NNMi |
| `Monitoring` | İzleme kapsamı ve canlılık (envanterde düşük öncelik) | Zabbix |
| `EndpointSecurity` | Koruma durumu, cihaz kimliği, kısıtlı güvenlik duruşu | Microsoft Defender for Endpoint |
| `ITSM` | Geçiş dönemindeki eski CMDB; yönetişim alanları | ServiceNow CMDB |
| `ChangeManagement` | Onaylı değişiklik pencereleri (CI özniteliği yazmaz) | ServiceNow Change |
| `Manual`, `Platform` | Elle giriş; eskime önerileri | Evren |

**Yaşam döngüsü:** `active` (yazar, CI açar) · `shadow` (yalnızca karşılaştırır) · `retiring` (yazar, CI açmaz) · `retired` (yok sayılır). Aynı rolde yazan iki kaynağın sırası `role_rank` ile belirlenir.

**Ürün değişimi yordamı:** (1) yeni bağlayıcı + eşleme + sentetik örnek ve test, (2) aynı rolde yeni veri kaynağı `shadow`: kapsam ve değer farkı raporlanır, golden record değişmez, (3) devir: yeni kaynak `active` + `role_rank: 1`, eskisi `retiring` + `role_rank: 2`, (4) eskisi `retired`. Kurallar, CI'lar ve sys_id'ler değişmez; mevcut CI'lar seri, UUID ve FQDN kurallarıyla yeni ürünün kayıtlarına bağlanır.

**Ürün değişimi tatbikatı (F1-13, sentetik):** `python tools/build_drill.py` BMC'nin yerine tatbikat ürünü "Keşif-B"yi (kendi eşleme dosyası `tools/drill/kesif_b_host.mapping.yaml`) dört aşamadan geçirir ve sonucu Veri Kaynakları sayfasında gösterir (`apps/web/content/drill.json`, CI'da `--check`). Denetimler: gölge modda golden record'da değer/kaynak değişmez ve CI açılmaz; etkin olunca yeni ürün yetkili olur ve eşleşmeyen kayıtlar CI açar; devredilen ürün CI açamaz; emeklilik önizlemesi sahipsiz kalacak alanları önceden ve birebir gösterir; kural dosyalarının SHA-256'sı hiç değişmez. Gerçek veritabanında aynı geçişler `ReconcilerStore.rereconcile_source` ile uygulanır (`tests/test_product_swap_pg.py`).

Tatbikatın gösterdiği, gerçek bir değişimde kontrol edilmesi gerekenler:

- **Eşleme kapsamı:** yeni ürünün eşlemesi eski ürünün verdiği her alanı üretmeyebilir (tatbikatta işlemci tipi/hızı, çekirdek sayısı, DNS alanı, üretici). Emeklilik önizlemesinde sahipsiz kalacak alanlar ya eşlemeye eklenir ya da bilinçli bırakılır.
- **Adlandırma farkı:** aynı değer farklı yazılabilir (tatbikatta işletim sistemi uyumu %0: "RHEL 9.4" ↔ "Red Hat Enterprise Linux 9.4"). Etkin moda geçmeden önce normalleştirme kararı verilir.
- **Kapsam farkı:** yalnız eski ürünün gördüğü CI'lar emeklilikten önce listelenir; başka bir keşif rolündeki kaynak görmüyorsa kapsam kaybı olur.

## Bağlayıcının Evren'e gönderimi (alım geçidi, ADR-0017)

Bağlayıcı CMDB'ye yazmaz; eşleme dosyasıyla ürettiği `IngestRecord`'ları alım geçidine gönderir. Sözleşme `docs/08`'dedir.

1. **Kimlik verilir:** `python tools/connector_credential.py issue --name zabbix-dc1 --data-source Zabbix --by <kişi>`. Belirteç yalnızca bir kez gösterilir ve kurum sır kasasına konur. Belirteç tek bir veri kaynağına bağlıdır: Zabbix belirteci vCenter adına yazamaz (403, denetim izinde). Emekli kaynağa kimlik verilmez, emekli kaynak gönderim yapamaz.
2. **Her toplama bir çalışmadır:** açılır (`snapshot` = tam liste, `delta` = yalnızca değişenler), en fazla 500 kayıtlık toplu gönderimlerle doldurulur, bildirilen farklı nesne sayısıyla kapanır. Uzlaştırıcı tüm kayıtları işleyince çalışma kapanır. Kayıtlar eksik kalırsa çalışma eksik kapanır ve devre kesici eskime ile silmeyi durdurur.
3. **Hata durumunda:** Kafka'ya ulaşılamazsa (503) aynı toplu gönderim yeniden denenir; tekrar zararsızdır. Hız sınırında (429) `Retry-After` kadar beklenir. Reddedilen toplu gönderimde (400) hata ayrıntısı hangi kaydın hangi alanının neden reddedildiğini söyler.
4. **Kimlik yönetimi:** `list` kimlikleri, son kullanım zamanını ve durumu (etkin, süresi doldu, iptal) gösterir. `revoke` kimliği hemen geçersiz kılar. Arayüzden yönetim U5'te gelecek.

## Bağlayıcı çalıştırıcı (U5, ADR-0020)

Kaynaktan salt okunur çeker, eşleme dosyasını uygular, geçide toplu gönderir ve çalışmayı açıp kapatır. Veritabanı erişimi yoktur.

```bash
# Kaynağa bağlanmadan: simülatör + kuru çalıştırma (kayıtlar dosyaya, geçide gitmez)
python tools/connector_run.py --connector zabbix --simulate connectors/zabbix/samples --dry-run --out build/zabbix.ndjson
# Gerçek kaynak → geçit (sırlar yalnızca dosyadan)
EVREN_SOURCE_URL=https://zabbix.kurum.local EVREN_SOURCE_TOKEN_FILE=/run/secrets/zabbix_api \
EVREN_GATEWAY_URL=https://evren-gateway.kurum.local EVREN_CONNECTOR_TOKEN_FILE=/run/secrets/zabbix_dc1 \
  python tools/connector_run.py --connector zabbix --name zabbix-dc1
# Kurum ortamında gerçek yanıtları bir kez kaydet, sonra kayıttan oynat (eşleme doğrulaması, U10)
python tools/connector_run.py --connector zabbix --record build/zabbix-kayit --dry-run
python tools/connector_run.py --connector zabbix --replay build/zabbix-kayit --dry-run
```

- **Rapor:** tek satır JSON: çekilen, eşlenen, atlanan, gönderilen, hata, sınıf dağılımı, geçitteki durum. Çıkış kodu 0 tamamlandı, 1 tamamlanmadı, 2 yapılandırma hatası.
- **Tamamlanma:** yalnızca sayfalama sonuna kadar hatasız gelindiyse, hiçbir toplu gönderim reddedilmediyse ve eşleme hatası yoksa `completed=true`. Aksi hâlde çalışma eksik kapanır; kısmi veri yine uzlaştırılır, ama devre kesici eskime ve emekliye ayırmayı durdurur.
- **Yeniden deneme:** bağlantı hatası, 429 ve 5xx için geri çekilmeli (1, 2, 4, 8 sn; `--retries`), `Retry-After`'a uyulur. 4xx kalıcıdır.
- **Kayıt dosyaları:** `--record` ve `--out` kurum verisi içerir; repoya konmaz. Repodaki `connectors/*/samples/` verisi sentetiktir.
- **Konteynerde:** `evren connector-run …`; geliştirme yığınında `docker compose run --rm -T -e EVREN_CONNECTOR_TOKEN_FILE=/dev/stdin connector connector-run --connector zabbix --simulate connectors/zabbix/samples < belirtec`.
- **Durum:** altı kaynağın istemcisi hazır (Zabbix, vCenter, ServiceNow + referans verileri, BMC, Defender, NNMi; U5.1–U5.2). Zamanlama ve arayüzden "şimdi çalıştır" çalıştırıcı servisiyle (ADR-0021).
- **Kaynağa göre kimlik bilgisi:** Zabbix API belirteci (`EVREN_SOURCE_TOKEN_FILE`); vCenter kullanıcı (`EVREN_SOURCE_USER`) + parola dosyası (`EVREN_SOURCE_PASSWORD_FILE`), API sürümü `--api-release` (varsayılan 8.0.1.0).
- **Eşleme koşulları:** `require` (hepsi sağlanmalı; ör. nesne türü VM ve şablon değil) ve `include_when` (en az biri; ör. misafir adı bildirilmiş).

**Yeni kaynak türü** (bulut hesapları, Kubernetes, yedekleme, PAM…): mevcut bir rolü dolduruyorsa yalnızca kaynak kaydı ve eşleme; yeni bir görevse yeni rol + o rolün yazabileceği alanlar için uzlaştırma kuralı.

## vCenter (F0)

- **Veri:** VM'ler, ESXi hostlar, kümeler, datastore'lar, etiketler. Her VM için iki kayıt:
  - VM örneği (`cmdb_ci_vmware_instance`).
  - Misafir sunucu (`cmdb_ci_server`, `virtual=true`). Yalnızca VMware Tools hostName bildiriyorsa üretilir.
- **İstemci (U5.2):** `evren_core/connectors/sources/vcenter.py`, VI/JSON API (vCenter 8.0 U1+). Oturum `SessionManager.Login` ile açılır; parola dosyadan okunur ve yalnızca bu istekte gönderilir. Sonraki isteklerde `vmware-api-session-id` başlığı kullanılır. Host'lar ve VM'ler kapsayıcı görünümden `RetrievePropertiesEx` ile 500'lük sayfalarla çekilir; görünüm ve oturum her durumda kapatılır. Servis hesabına salt okunur rol yeter.
- **Kayıtlar:** ESXi host (`esx_host.mapping.yaml`, `cmdb_ci_esx_server`; kimlik için fiziksel kart MAC'leri, seri, donanım UUID'si), VM örneği ve misafir sunucu. İlişkiler: VM örneği → host `Registered on`, misafir sunucu → VM örneği `Instantiates`, misafir sunucu → host `Virtualized by`.
- **Tanımlayıcılar:** `config.instanceUuid` (VM kalıcı kimliği), `config.uuid` (BIOS UUID), vCenter `instanceUuid` + `moId`. Host kaynak anahtarı `<vCenter örnek kimliği>/<moId>` biçimindedir; `moId` vCenter'lar arasında tekrar eder. Misafir sunucu seri numarası BIOS UUID'den türetilir: `VMware-42 1a …`.
- **Yetkili olduğu alanlar:** VM kaynakları (vCPU, bellek), sanal sunucularda CPU/RAM.
- **Tuzaklar:**
  - VM görünen adı hostname değildir (`abc-1 (Muhasebe)`).
  - Şablonlar (`config.template`) CI sayılmamalı.
  - Klonlanan VM'lerde BIOS UUID çakışabilir; çelişki denetimi ve `instanceUuid` bunu yakalar.

## Zabbix (F0)

- **Veri:** izlenen hostlar, arayüzler, envanter alanları, host grupları.
- **İstemci (U5.1):** `evren_core/connectors/sources/zabbix.py`. JSON-RPC `host.get`; kimlik doğrulama Zabbix API belirteciyle `Authorization: Bearer` başlığında (Zabbix 6.4+; istek gövdesindeki `auth` alanı 7.2'de kaldırıldı). `selectHostGroups` nedeniyle Zabbix 6.2+ gerekir. API'de ofset olmadığı için önce yalnızca `hostid` listesi çekilir, ayrıntı 500'lük kimlik parçalarıyla istenir. Güvenilir "değişti" alanı olmadığından her çalışma tam listedir.
- **Tanımlayıcılar:** `hostid`, teknik ad (`host`), envanter seri ve MAC.
- **Yetkili olduğu alanlar:** izleme kapsamı ve canlılık. Envanterde düşük öncelikli.
- **Tuzaklar:**
  - Görünen ad (`name`) serbest metindir; teknik ad (`host`) kullanılır.
  - Envanter alanları çoğunlukla boştur.
  - Host adı olarak IP girilmiş kayıtlar ad kimliği sayılmaz. MAC veya seri de yoksa kayıt yeni CI açmaz; "kimlik verisi yetersiz" olarak park edilir.
  - Host grubu adları Türkçe olabilir ("Ağ cihazları"); sınıf kuralları `ğ` harfini de kabul eder.
- **CMDB'ye girmez:** metrik ve alarm. Yalnızca "izleniyor mu" ve "son canlılık" özetleri girer.

## Microsoft Defender for Endpoint (F1)

- **Veri:** makine envanteri, OS, son IP, Entra cihaz kimliği, koruma ve risk durumu.
- **Kapsam:** yalnızca sunucu işletim sistemleri (`include_when`).
- **Tanımlayıcılar:** `id` (makine kimliği), `aadDeviceId`, `computerDnsName`.
- **Kısıtlı veri:** maruziyet, risk ve zafiyet sayıları `cmdb.ci_security_posture` tablosuna gider. Genel okuyucu göremez.
- **Tuzak:** bulut API'si; çıkış trafiği ve vekil sunucu izni gerekir. Uygulama kaydında yalnızca okuma izni verilir.
- **İstemci (U5.2):** `evren_core/connectors/sources/defender.py`.
  - Kimlik doğrulama: Entra istemci kimliği akışı. Ortam: `EVREN_SOURCE_TENANT_ID`, `EVREN_SOURCE_CLIENT_ID`, `EVREN_SOURCE_CLIENT_SECRET_FILE`; isteğe bağlı `EVREN_SOURCE_LOGIN_URL` (ulusal bulut) ve `EVREN_SOURCE_SCOPE`.
  - Çağrı: `GET /api/machines?$top=…`. Sonraki sayfa yalnızca aynı ana bilgisayardaysa `@odata.nextLink` ile izlenir.
  - Belirtecin süresi dolarsa bir kez yenilenir. API adresi varsayılan olarak `https://api.security.microsoft.com`.
  - Risk ve maruziyet CI'a yazılmaz.
- **Kısıtlı duruş yolu (ADR-0023):** eşlemenin `posture:` bölümü koruma ve sağlık durumu, maruziyet, risk puanı ve son görülmeyi zarfın ayrı `posture` bölümüne koyar. Bilinmeyen değer boşa düşer.
  - Uzlaştırıcı, kayıt bir CI'a bağlandıktan sonra yalnızca `cmdb.ci_security_posture`'a yazar. Kaynak kaydında, CI özniteliğinde, değişim geçmişinde ve arama dizininde duruş yoktur.
  - Yalnızca `posture_source` rolü (EndpointSecurity) yazar; devirde rol içi sırası küçük kaynak kazanır, gölge ürün yazmaz.
  - API yalnızca Güvenlik okuyucu, Denetçi ve break-glass rollerine gösterir; her gösterim denetim izine yazılır.
  - Henüz yok: zafiyet sayıları (ayrı zafiyet API'si, `vulnerabilities_*` sütunları boş kalır).

## NNMi (F1)

- **Veri:** ağ düğümleri, arayüzler, L2 bağlantıları, yönetim IP'leri.
- **Tanımlayıcılar:** düğüm `uuid`, `systemName` (sysName), `longName`. `systemObjectId` kimlik **değildir**: modeli tanımlar, aynı modeldeki tüm cihazlarda aynıdır.
- **Yetkili olduğu alanlar:** ağ cihazı sınıflarında ad, IP, seri numarası, firmware.
- **Tuzaklar:**
  - HSRP/VRRP sanal MAC'leri paylaşılır; zayıf olarak işaretlenir.
  - L3 switch'ler (`switchrouter`) switch sınıfında kalır.
  - API sürüme göre değişir (SOAP/REST); alan adları doğrulanmalı.
- **İstemci (U5.2):** `evren_core/connectors/sources/nnmi.py`.
  - Çağrı: WS-I SOAP `NodeBeanService.getNodes`; kimlik Basic (`EVREN_SOURCE_USER` + `EVREN_SOURCE_PASSWORD_FILE`, "Web Services Client" rolü).
  - Sayfalama: `id GT SON` + `maxObjects`. Kimlikler artan sırada gelmezse çalışma durur.
  - XML `safe_xml` ile ayrıştırılır (DTD/varlık reddi). Süzgeç biçimi kurum sürümünde doğrulanacak (F1-03).

## BMC Helix Discovery (F1) — Keşif rolü

- **Ürün:** BMC Helix Discovery (eski adıyla BMC Discovery / ADDM). On-prem appliance ve Helix SaaS aynı REST API'yi sunar; fark ağ yolu (SaaS'ta outpost) ve kimlik bilgisidir. Kurum kurulum tipini doğrulayacak (açık soru).
- **Çekme:** `GET /api/v1.x/data/search` ile `search Host show …` sorgusu, `format=object`, sayfalama tamamlanmadan çalışma tamamlandı sayılmaz. Sorgu ve alanlar `connectors/bmc_discovery/host.mapping.yaml` başında.
- **Tanımlayıcılar:** düğüm `key`, `hostname`, `local_fqdn`, `serial`, `uuid`; MAC ve IP listeleri.
- **Yetkili olduğu alanlar (Keşif rolü):** işletim sistemi, ad ve ağ kimliği; fiziksel sunucularda donanım.
- **Tuzaklar:**
  - Sanal sunucularda `serial` VMware biçimindedir (`VMware-42 1a …`); BIOS UUID'ye bağlanır ve vCenter misafir sunucusuyla aynı CI'a eşleşir.
  - `ram` MB, `processor_speed` MHz döner; sanal sunucuda işletim sisteminin gördüğü bellek vCenter değerinden biraz azdır (vCenter önceliklidir).
  - `host_type` "… Desktop" olan düğümler kapsam dışıdır.
- Alan adları kurum örneğinde doğrulanınca eşlemedeki `verify: true` kaldırılır (F1-01).
- **İstemci (U5.2):** `evren_core/connectors/sources/bmc_discovery.py`.
  - Çağrı: `GET /api/v1.x/data/search?format=object` (sorgu `HOST_QUERY`).
  - Kimlik doğrulama: Discovery kullanıcısının API belirteci (`EVREN_SOURCE_TOKEN_FILE`, Bearer).
  - Sayfalama: `results_id` + `offset`; toplanan sayı `count`'la karşılaştırılır.
  - API sürümü: `--api-release` (varsayılan 1.12).

## ServiceNow (F0 ilk yükleme, F1 çekme, F1 Change)

- **CMDB ilk yükleme:** `cmdb_ci` soyağacı, `cmdb_rel_ci` ve referans tabloları aynı sys_id ile.
- **İstemci (U5.2):** `evren_core/connectors/sources/servicenow.py`, Table API.
  - Okunan tablolar: `cmdb_ci_server` ve `cmdb_ci_netgear` (alt sınıflarıyla), ardından referans tabloları.
  - Sayfalama `sys_id>SON^ORDERBYsys_id` ile anahtar sırasına göredir; kısa sayfada durmaz, boş sayfaya kadar devam eder.
  - Referanslar sys_id olarak gelir. Kimlik doğrulama Basic'tir (`EVREN_SOURCE_USER` + `EVREN_SOURCE_PASSWORD_FILE`).
- **Entegrasyon kullanıcısı:** salt okunur (CMDB tablolarında okuma), saat dilimi **UTC** (delta sorgusu tarihi kullanıcının saat dilimiyle yorumlanır). Kullanıcı, okunacak tüm CI kayıtlarını görebilmelidir: ACL tam bir sayfayı gizlerse sayfalama orada biter.
- **sys_id korunur:** uzlaştırıcı, bağı olmayan ServiceNow kaydını kimlik çözümlemesinden geçirmez (`sys_id_key`, ADR-0020 §10).
  - `sync.sn_ci_map`'te eşleme varsa kayıt Evren CI'ına bağlanır.
  - Eşleme yoksa CI aynı sys_id ile açılır.
  - Keşif ve izleme kaynakları sonra bu CI'a kimlik kurallarıyla (seri, UUID, MAC) bağlanır.
- **Artımlı çekme:** `sys_updated_on` ile (`--mode delta --since …`). Geçiş döneminde SN'de yapılan elle değişiklikler "ServiceNow" kaynağı olarak uzlaştırmaya girer.
- **Referans verileri (ADR-0022, göç 0020):** CI tablolarından sonra aynı çalışmada `sys_user`, `sys_user_group`, `cmn_location`, `core_company`, `cmn_department`, `cmn_cost_center` ve `cmdb_model` okunur.
  - Her tablodan yalnızca `evren_core/reference.py`'deki izinli sütunlar istenir. Kişisel veri en azdır: kullanıcıda yalnızca kullanıcı adı, ad, e-posta, departman ve etkinlik.
  - Eşleme: `connectors/servicenow_cmdb/reference.mapping.yaml`. Sınıf ipucu tablo adıdır; uzlaştırıcı kaydı kimlik çözümlemesine sokmaz, `ref.<tablo>`'ya aynı sys_id ile yazar.
  - Yalnızca referans kaynağı rolü (ITSM, `reference_source: true`) yazar; gölgedeki kaynak yazmaz. Entra veya Evren kaynaklı satır ezilmez; silinen kayıt pasifleşir.
  - Entegrasyon kullanıcısının bu tablolarda okuma rolü olmalıdır (ServiceNow'da ACL değiştirilmez; var olan okuma rolü verilir).
  - Henüz yok: grup üyeliği (`sys_user_grmember`; ABAC ile, U8) ve kişi/grup verisinin Entra ile eşlenmesi.
- **Change kayıtları:** `change_request` + `task_ci`. Yetkisiz değişiklik tespiti için onaylı pencere sağlar. CI özniteliği yazmaz (`ci_attribute_source: false`).

## Gelecekte eklenebilecek kaynaklar

Bulut hesapları, Kubernetes, hipervizör/HCI platformları, yedekleme yazılımı ve PAM envanteri. Her biri yalnızca bir bağlayıcı ve bir eşleme dosyası ister; ara katmandan sonraki bileşenler değişmez.
