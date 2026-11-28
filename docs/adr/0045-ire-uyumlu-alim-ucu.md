# ADR-0045: IRE uyumlu alım ucu

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (U9 kapsamı), mimari, güvenlik

## Bağlam

Evren ana kaynak olunca ServiceNow CMDB'si replikaya döner (ADR-0042). Bugün ServiceNow CMDB'sine IRE API ile (`POST /api/now/identifyreconcile`) yazan araçlar varsa iki sorun çıkar:

- Replikaya ikinci bir yazar girer.
- Araçların gördüğü bilgi Evren'e hiç ulaşmaz.

Bu araçların hedefini değiştirmek gerekir. En az zahmetli yol, aynı IRE yükünü kabul eden bir Evren ucudur (plan F2-05).

Bu kararın bir ön koşulu var. Kurumda bugün ServiceNow'a IRE ile yazan araçların gerçekten olup olmadığı **henüz bilinmiyor**. Bunu ServiceNow CMDB'sindeki `discovery_source` değerlerinin dağılımı gösterir. Bu döküm Evren'de henüz yok, çünkü ServiceNow CMDB bağlayıcısı alanı okumuyor; gölge mod raporuna eklenmesi plandadır (F0-13). Uç bu yüzden küçük tutuldu ve var olan alım yolunun üzerine kuruldu.

## Karar

### 1. Uç alım geçidinde, uzlaştırıcıdan geçer

- **Uç noktalar:** `POST /api/now/identifyreconcile?sysparm_data_source=<kaynak>` ve `.../identifyreconcile/query`. API'de değil, **alım geçidindedir**.
- **Kimlik:** Bağlayıcı belirteci (`Authorization: Bearer evr_…`, ADR-0017) gerekir. Belirteç tek bir veri kaynağına bağlıdır. `sysparm_data_source` bu kaynakla aynı değilse istek 403 alır; emekli kaynak da 403 alır.
- **Kaynak kaydı:** Araç Evren'de kendi rolüyle bir veri kaynağıdır; panelden, entegrasyon sihirbazıyla (ADR-0038) ve önce gölge modda eklenir. Kurallar ürüne değil role yazılır (kural 8).
- **İşleme:** Her istek tek bir **delta çalışması** olur. Akış: çalışma açılır, öğeler kaynak kaydı olarak Kafka'ya gider, kapanış istenir. Kimlik çözümleme ve uzlaştırma uzlaştırıcıda yapılır.
- **Golden record'a yazılmaz** (kural 1). Geçidin CMDB'ye erişimi yoktur.

### 2. Dönüşüm (`evren_core.ire_inbound`)

| IRE | Evren alım kaydı |
| --- | --- |
| `className` | `class_hint` (CMDB sınıfı olmalı) |
| `values` | `attributes`. Sözlükte olmayan alan ve `sys_*` uyarıyla atılır. Referans alanı sys_id bekler; görünen değer uyarılır |
| `sys_object_source_info.source_native_key` | `source_key` (**zorunlu**) |
| `sys_object_source_info.source_recency_timestamp` | `observed_at` (UTC; yoksa şimdi, gelecek tarih reddedilir) |
| `lookup` (`cmdb_serial_number`, `cmdb_ci_network_adapter`, `cmdb_ci_ip_address`) | `lookup` (seri, MAC, IP) |
| `relations` (`parent`, `child`, `type`) | Ebeveyn kaydında `relations`. Bağımlı sınıfta kuraldaki ilişki `parent` olur (ADR-0029) |

### 3. ServiceNow'dan bilinçli farklar

- **Kaynak yerel anahtarı zorunludur.** Eskime, silinme ve geri alınabilir birleştirme bu anahtarla izlenir (kural 3). Anahtarsız yük reddedilir.
- **Ya hep ya hiç.** Bir öğe hatalıysa hiçbiri alınmaz; geçidin toplu gönderim sözleşmesi de böyledir. Hata ServiceNow biçimindedir. Ayrıntı, sorunun yerini söyler (ör. `items[1].sys_object_source_info.source_native_key`); girdi değerini yansıtmaz.
- **Asenkron işleme.** Yanıt IRE biçimindedir, ancak öğeler `QUEUED` döner ve `sysId` `Unknown` olur. Yanıttaki `evren` bölümü çalışma kimliğini ve durum adresini (`/ingest/v1/runs/{id}`) verir. HTTP 200 döner; bazı araçlar yalnızca 200'ü başarı sayar.
- **`/query` yalnızca doğrular.** Yük `VALIDATED` döner, hiçbir şey yazılmaz. ServiceNow'daki gibi kimlik sonucu tahmin edilmez, çünkü geçidin CMDB'ye erişimi yoktur.
- **İşlenmeyen parçalar:** `related` ve `referenceItems` uyarıyla geçilir. Kimlik doğrulama ve uzlaştırma kuralları Evren'indir; IRE yükündeki `settings` yok sayılır.
- **Kafka hatası.** Kafka teslim edemezse çalışma hemen "eksik" kapanış ister. Devre kesici o kaynağın yıkıcı işlemlerini durdurur ve araç yeniden dener (503, `Retry-After`).
- **Sınırlar:** öğe sayısı geçidin toplu sınırı kadardır (500); kimlik başına dakikalık kayıt sınırı uygulanır.

## Sonuçlar

- ServiceNow'a IRE ile yazan araçlar adres ve kimlik değiştirerek Evren'e yazar. Veri, keşif kaynaklarıyla aynı yoldan geçer: kimlik çözümleme, uzlaştırma, devre kesici, değişiklik denetimi, denetim izi. Replikaya ikinci bir yazar girmez.
- Denetim izi: her kabul `ire_ingest` olayıdır; ret, geçidin mevcut ret olaylarıyla yazılır.
- **Sınırlar:**
  - Araçlar temel kimlik doğrulamadan bağlayıcı belirtecine geçmelidir.
  - Anında `sysId` dönmez; buna dayanan araç Evren'in REST veya Table API'siyle sonradan okur.
  - `related` yok.
  - Gerçek bir IRE istemcisiyle kabul yapılmadı; testler sentetik yükle yapıldı. Kurumda böyle bir araç olup olmadığı, `discovery_source` dökümü gölge mod raporuna eklenince (F0-13) ve canlı kabulde (U10) anlaşılacak.

## Değerlendirilen seçenekler

- **API sürecinde (Table API'nin yanında):** API golden record'a yazmaz ve bağlayıcı kimliğini tanımaz. Alım yolunu ikinci kez yazmak gerekirdi. Reddedildi.
- **Eşzamanlı uzlaştırma (yanıtta sysId):** Geçidin CMDB'ye erişmesini ve Kafka'yı atlamasını gerektirirdi. Bu, tek yazar ve devre kesici ilkelerini zayıflatır. Reddedildi.
- **Kısmi kabul (ServiceNow gibi öğe öğe):** Çalışma sayımı ve devre kesici, kısmen alınmış bir yükü yanlış yorumlayabilir. Ya hep ya hiç tutarlıdır. Reddedildi.
