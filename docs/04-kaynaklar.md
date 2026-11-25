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

**Yeni kaynak türü** (bulut hesapları, Kubernetes, yedekleme, PAM…): mevcut bir rolü dolduruyorsa yalnızca kaynak kaydı ve eşleme; yeni bir görevse yeni rol + o rolün yazabileceği alanlar için uzlaştırma kuralı.

## vCenter (F0)

- **Veri:** VM'ler, ESXi hostlar, kümeler, datastore'lar, etiketler. Her VM için iki kayıt:
  - VM örneği (`cmdb_ci_vmware_instance`).
  - Misafir sunucu (`cmdb_ci_server`, `virtual=true`). Yalnızca VMware Tools hostName bildiriyorsa üretilir.
- **Tanımlayıcılar:** `config.instanceUuid` (VM kalıcı kimliği), `config.uuid` (BIOS UUID), vCenter `instanceUuid` + `moId`. Misafir sunucu seri numarası BIOS UUID'den türetilir: `VMware-42 1a …`.
- **Yetkili olduğu alanlar:** VM kaynakları (vCPU, bellek), sanal sunucularda CPU/RAM.
- **Tuzaklar:**
  - VM görünen adı hostname değildir (`abc-1 (Muhasebe)`).
  - Şablonlar (`config.template`) CI sayılmamalı.
  - Klonlanan VM'lerde BIOS UUID çakışabilir; çelişki denetimi ve `instanceUuid` bunu yakalar.

## Zabbix (F0)

- **Veri:** izlenen hostlar, arayüzler, envanter alanları, host grupları.
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

## NNMi (F1)

- **Veri:** ağ düğümleri, arayüzler, L2 bağlantıları, yönetim IP'leri.
- **Tanımlayıcılar:** düğüm `uuid`, `systemName` (sysName), `longName`. `systemObjectId` kimlik **değildir**: modeli tanımlar, aynı modeldeki tüm cihazlarda aynıdır.
- **Yetkili olduğu alanlar:** ağ cihazı sınıflarında ad, IP, seri numarası, firmware.
- **Tuzaklar:**
  - HSRP/VRRP sanal MAC'leri paylaşılır; zayıf olarak işaretlenir.
  - L3 switch'ler (`switchrouter`) switch sınıfında kalır.
  - API sürüme göre değişir (SOAP/REST); alan adları doğrulanmalı.

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

## ServiceNow (F0 ilk yükleme, F1 çekme, F1 Change)

- **CMDB ilk yükleme:** `cmdb_ci` soyağacı, `cmdb_rel_ci` ve referans tabloları aynı sys_id ile.
- **Artımlı çekme:** `sys_updated_on` ile. Geçiş döneminde SN'de yapılan elle değişiklikler "ServiceNow" kaynağı olarak uzlaştırmaya girer.
- **Change kayıtları:** `change_request` + `task_ci`. Yetkisiz değişiklik tespiti için onaylı pencere sağlar. CI özniteliği yazmaz (`ci_attribute_source: false`).

## Gelecekte eklenebilecek kaynaklar

Bulut hesapları, Kubernetes, hipervizör/HCI platformları, yedekleme yazılımı ve PAM envanteri. Her biri yalnızca bir bağlayıcı ve bir eşleme dosyası ister; ara katmandan sonraki bileşenler değişmez.
