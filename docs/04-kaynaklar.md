# 04 · Kaynak sistemler

Her kaynak için: neyi sağlar, hangi tanımlayıcılarla gelir, neye yetkilidir, tuzakları nelerdir. Eşleme dosyaları `connectors/<kaynak>/`. Yeni kaynak eklemek için `/yeni-baglayici` komutunu kullan.

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
  - Host adı olarak IP girilmiş kayıtlar ad kimliği sayılmaz.
- **CMDB'ye girmez:** metrik ve alarm. Yalnızca "izleniyor mu" ve "son canlılık" özetleri girer.

## Microsoft Defender for Endpoint (F1)

- **Veri:** makine envanteri, OS, son IP, Entra cihaz kimliği, koruma ve risk durumu.
- **Kapsam:** yalnızca sunucu işletim sistemleri (`include_when`).
- **Tanımlayıcılar:** `id` (makine kimliği), `aadDeviceId`, `computerDnsName`.
- **Kısıtlı veri:** maruziyet, risk ve zafiyet sayıları `cmdb.ci_security_posture` tablosuna gider. Genel okuyucu göremez.
- **Tuzak:** bulut API'si; çıkış trafiği ve vekil sunucu izni gerekir. Uygulama kaydında yalnızca okuma izni verilir.

## NNMi (F1)

- **Veri:** ağ düğümleri, arayüzler, L2 bağlantıları, yönetim IP'leri.
- **Tanımlayıcılar:** düğüm `uuid`, `systemName` (sysName), `longName`, `systemObjectId`.
- **Yetkili olduğu alanlar:** ağ cihazı sınıflarında ad, IP, seri numarası, firmware.
- **Tuzaklar:**
  - HSRP/VRRP sanal MAC'leri paylaşılır; zayıf olarak işaretlenir.
  - L3 switch'ler (`switchrouter`) switch sınıfında kalır.
  - API sürüme göre değişir (SOAP/REST); alan adları doğrulanmalı.

## BMC ajanları (F1 — engelli)

- **Açık soru:** hangi ürün? BMC Discovery/ADDM, Client Management ya da Helix Discovery.
- Eşleme dosyası BMC Discovery "Host" düğümü varsayımıyla yazılmış bir taslaktır.
- Hedef: işletim sistemi, donanım ve seri numarası için yetkili kaynak.

## ServiceNow (F0 ilk yükleme, F1 çekme, F1 Change)

- **CMDB ilk yükleme:** `cmdb_ci` soyağacı, `cmdb_rel_ci` ve referans tabloları aynı sys_id ile.
- **Artımlı çekme:** `sys_updated_on` ile. Geçiş döneminde SN'de yapılan elle değişiklikler "ServiceNow" kaynağı olarak uzlaştırmaya girer.
- **Change kayıtları:** `change_request` + `task_ci`. Yetkisiz değişiklik tespiti için onaylı pencere sağlar. CI özniteliği yazmaz (`ci_attribute_source: false`).

## Gelecekte eklenebilecek kaynaklar

Bulut hesapları, Kubernetes, hipervizör/HCI platformları, yedekleme yazılımı ve PAM envanteri. Her biri yalnızca bir bağlayıcı ve bir eşleme dosyası ister; ara katmandan sonraki bileşenler değişmez.
