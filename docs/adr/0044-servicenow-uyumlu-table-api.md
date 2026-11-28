# ADR-0044: ServiceNow uyumlu Table API (okuma)

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (U9 kapsamı), mimari, güvenlik

## Bağlam

Kurumda ServiceNow CMDB'sini Table API ile okuyan çok sayıda betik ve entegrasyon var: raporlar, otomasyonlar, CI seçiciler, GRC Platformu'nun CMDB modülü. Geçiş döneminde ana kaynak Evren'dir (ADR-0042). Bu tüketiciler Evren'e taşınırken kodlarını yeniden yazmak zorunda kalmamalı; adres ve kimlik değiştirmek yetmeli (plan F2-05).

Uyumluluk, Evren'in güvenlik ilkelerinden ödün vermeden sağlanmalı:

- **Yetki:** Rol, alan hassasiyeti ve sınıf kalıtımı REST'teki gibi uygulanır.
- **Sorgu güvenliği:** Sunucu tarafında betik çalışmaz; alan adları yalnızca sözlükten çözülür.
- **Sezdirme yok:** Görülmeyen bir alan süzgeçte kullanılarak değeri tahmin edilemez.

## Karar

### 1. Uç noktalar ve tablolar

- `GET /api/now/table/{tablo}` ve `GET /api/now/table/{tablo}/{sys_id}` API sürecindedir (`routers/sn_table.py`). Ayrı servis yoktur.
- **Tablolar:**
  - CMDB sınıfları (`cmdb_ci` soyağacı);
  - `cmdb_rel_ci` ve `cmdb_rel_type`;
  - referans tabloları: `sys_user`, `sys_user_group`, `cmn_location`, `core_company`, `cmn_department`, `cmn_cost_center`, `cmdb_model`. Böylece referans bağlantıları boşa düşmez.
  - Başka tablo 400 döner.
- **Tablo kalıtımı:** Üst sınıf tablosu alt sınıfların kayıtlarını da döner; alanlar istenen tablonun alanlarıdır (ServiceNow'daki gibi). Tekil okumada kayıt istenen tablonun soyağacında değilse 404 döner.
- **Yazma yoktur.** `POST`, `PUT`, `PATCH` ve `DELETE` 405 döner. Golden record'a tek yazan uzlaştırıcıdır (kural 1). Kaynak verisi gönderen araçlar için IRE uyumlu uç nokta ayrı adımdır (U9.3b). O uç nokta da golden record'a yazmaz; veriyi kaynak kaydı olarak alım geçidinden geçirir.

### 2. Parametreler

| Parametre | Davranış |
| --- | --- |
| `sysparm_query` | Encoded query (`evren_core.encoded_query`): `^`, `^OR`, `^NQ`, `ORDERBY`/`ORDERBYDESC`, `=`, `!=`, `LIKE`, `STARTSWITH`, `ENDSWITH`, `IN`, `ISEMPTY`, `BETWEEN`, `INSTANCEOF`… Metin karşılaştırmaları büyük/küçük harf duyarsızdır. |
| `sysparm_fields` | Virgülle alan listesi. Bilinmeyen veya görülmeyen alan sessizce atlanır (ServiceNow gibi). Dot-walk 400 döner. |
| `sysparm_limit`, `sysparm_offset` | Varsayılan 100, en çok 1000; aşım 400 döner. Sıralama her zaman `sys_id` ile tamamlanır; böylece sayfalar kararlıdır. |
| `sysparm_exclude_reference_link` | `true` ise referans düz sys_id olarak döner. |
| `sysparm_display_value` | Yalnızca `false` kabul edilir; diğer değerler 400 döner. Görünen değer desteği sonraki sürümdedir. |

### 3. Yanıt biçimi (ServiceNow)

- **Gövde:** `{"result": [...]}`; tekil okumada `{"result": {...}}`.
- **Değerler:**
  - Değerler metindir; boş değer `""`.
  - Mantıksal değer `"true"`/`"false"`.
  - Zaman UTC, `YYYY-MM-DD hh:mm:ss`.
  - Referans `{"link": "<kök>/api/now/table/<tablo>/<sys_id>", "value": "<sys_id>"}`.
- **Başlıklar:** `X-Total-Count`; `Link` başlığında `first`, `prev`, `next` ve `last` bağlantıları.
- **Hata:** `{"error": {"message", "detail"}, "status": "failure"}`. `/api/now/` altındaki bütün hatalar bu biçimdedir: kimlik (401), yetki (403), doğrulama (400), bulunamadı (404), yöntem (405) ve beklenmeyen hata (500). Ayrıntıda iç bilgi (SQL, yığın) yoktur.

### 4. Kimlik ve yetki

- **Kimlik:**
  - Entra kurulumunda makine ve kullanıcı istemcileri `Authorization: Bearer` ile Entra erişim belirteci gönderir. Doğrulama MCP ile ortaktır (`bearer_principal`, ADR-0043).
  - Oturum çereziyle yalnızca okuma yapılabilir. Başka kökenin okuması tarayıcının aynı köken kuralıyla engellenir.
  - Geliştirme kurulumunda geliştirme kimliği kullanılır.
  - ServiceNow'daki temel kimlik doğrulama (kullanıcı adı ve parola) **desteklenmez**.
- **Okuma izni tabloya göredir:**
  - CMDB sınıfı için sınıfın kendisi (alt sınıflara kalıtılır);
  - ilişki tabloları için `cmdb_rel_ci`;
  - referans tabloları için `cmdb_ci` (referans aramasıyla aynı).
  - Yetkisiz okuma 403 döner ve `audit.security_event`'e yazılır.
- **Reddedilen alt sınıf** (`deny`) üst tablo sorgusunda da, tekil okumada da yoktur.
- **Hassasiyet sınırını aşan alan** yanıtta yoktur ve süzgeçte ya da sıralamada kullanılamaz (400). Böylece görülmeyen değer süzgeçle sezdirilemez.
- **İlişki** yalnızca iki ucu da okunabilen CI'lar arasındaysa görünür.
- **Kişi e-postası** (`sys_user.email`) yalnızca gizli düzeyi görebilene gösterilir (KVKK, en az veri).
- **Kısıtlı güvenlik verisi** (`cmdb.ci_security_posture`) bu yoldan okunamaz.

### 5. Sorgu güvenliği

- `javascript:` değerleri reddedilir.
- Koşul sayısı 200, IN listesi 1000 ile sınırlıdır.
- Alan ifadeleri sözlükten gelir, kullanıcı değerleri yalnızca SQL parametresidir.

## Sonuçlar

- ServiceNow CMDB'sini Table API ile okuyan betik ve entegrasyonlar adres ve kimlik değiştirerek Evren'i okuyabilir. Dönen biçim ServiceNow'unkiyle aynıdır.
- REST, MCP ve Table API aynı yetki motorunu kullanır; ikinci bir yetki kuralı yoktur.
- **Sınırlar:**
  - `sysparm_display_value=true|all` yok. Görünen değer isteyen tüketici şimdilik referansı ayrıca okumalıdır.
  - Dot-walk yok.
  - `sysparm_view`, `sysparm_query_category`, `sysparm_no_count` ve `sysparm_suppress_pagination_header` yok sayılır.
  - Temel kimlik doğrulama yok. Parolalı eski betik Entra istemci kimliğine geçmelidir.
  - Değişiklik tabloları (`change_request`, `task_ci`) ve görev tabloları yok.
  - Kurum betiklerinden alınmış gerçek örneklerle uyum denemesi henüz yapılmadı; testler sentetik veriyle yapıldı. Canlı kabul U10'dadır.
  - Hız sınırı yok; yalnızca sayfa ve koşul sınırları var.

## Değerlendirilen seçenekler

- **ServiceNow'u okuma için yerinde tutmak (Evren → SN replikası üzerinden):** Replika yalnızca süreçlerin ihtiyaç duyduğu sınıfları taşır; ana kaynak Evren'dir. Tüketicilerin replikadan okuması geçişin sonunda ServiceNow lisansından çıkışı engeller. Reddedildi.
- **Yalnızca Evren'in kendi REST API'si:** Daha zengin bir API'dir, ama her tüketicinin yeniden yazılmasını gerektirir. İkisi birlikte sunulur: yeni entegrasyonlar `/api/v1`'i kullanır, eski betikler uyumlu ucu.
- **Temel kimlik doğrulama (ServiceNow'daki gibi):** Parolanın her istekte gitmesi ve ayrı bir parola deposu, Entra merkezli kimlik ilkesine (ADR-0005) aykırıdır. Reddedildi.
