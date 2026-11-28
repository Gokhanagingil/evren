# ADR-0040: Gözlemlenebilirlik ve bildirimler — platform uyarıları, bildirim merkezi, metrikler

- Durum: Kabul edildi
- Tarih: 28.11.2026
- Karar verenler: Proje sahibi (hedef: eksiksiz ESM platformu; U8.9), mimari, güvenlik, işletim

## Bağlam

Sistem Durumu sayfası (ADR-0032) anlık durumu gösteriyordu. Ancak:

- Sorunu **kimse görmeden** fark etmenin yolu yoktu. Sayfaya bakan olmazsa durmuş bir servis, biriken olaylar veya bozulan denetim mührü günlerce sürebilirdi.
- Görevler (ADR-0035) atandığında, son tarih yaklaştığında veya süre geçtiğinde sorumluya **haber gitmiyordu**. Tırmanma da yoktu. ServiceNow kanalındaki görevi ServiceNow bildiriyordu; Evren kanalındaki görev yalnızca "Size düşen işler"de görünüyordu.
- Kurumun izleme sistemi (Prometheus, Grafana, OpenTelemetry) Evren'in iç durumunu **kazıyamıyordu**. Eğilim ve geçmiş için de bir veri kaynağı yoktu.

Bildirim yalnızca CMDB'nin ihtiyacı değil. Süreç uygulaması (olay, değişiklik, talep; F4) da aynı altyapıyı kullanacak. Bu yüzden bildirim merkezi ortak bir platform servisi olarak kuruldu.

## Karar

### 1. Platform uyarıları: kural, süre koşulu, yaşam döngüsü

Kurallar `metadata/alerts.yaml` dosyasındadır. Her kural şunları tanımlar:

- **sinyal:** servis nabzı, Kafka'ya ulaşma, tüketici gecikmesi, olay yayını birikmesi, devre kesici, çalıştırma isteği, görev teslimi, denetim mührü gecikmesi ve bozulması;
- eşik ve süre (`for_minutes`);
- önem;
- alıcı roller;
- kısa öneri ("ne yapılmalı").

Kurallar ürüne değil sinyale yazılır (kural 8). Doğrulayıcı ürün adını reddeder.

Görev çalışanı her turda iki iş yapar:

1. Platformun anlık görüntüsünü alır. Bu salt okumadır; yalnızca gereken sütunlar okunur.
2. Kuralları değerlendirir (`evren_core.ops`, `evren_governance.ops_watch`).

Uyarı durumları şöyle ilerler:

| Durum | Ne zaman |
|---|---|
| `pending` | Koşul doğdu, süre henüz dolmadı |
| `firing` | Koşul `for_minutes` boyunca sürdü |
| `resolved` | Koşul kalktı |

Süre dolmadan kalkan koşul kimseyi rahatsız etmez: ateşlenmeden çözülür.

Değişmez kurallar:

- Kural ve konu başına tek açık uyarı olur (benzersiz indeks).
- Uyarı silinmez. Çözülen uyarı değişmez.
- Ateşlenen ve çözülen uyarı ile onay olay omurgasına gider: `ops.alert.*` olayları, `evren.cmdb.activity.v1` konusuna.

İki çalışan aynı anda değerlendirmesin diye tur danışma kilidiyle (advisory lock) yapılır.

**Onay:** Platform operatörü veya Platform yöneticisi (yeni `platform/ops_admin` izni) ateşlenmiş uyarıyı gerekçeyle ve bir kez onaylar. Onay uyarıyı kapatmaz. Yalnızca "biliyoruz, ilgileniyoruz" kaydıdır. Onay ayrıca denetim izine `alert_ack` olarak düşer.

### 2. Bildirim merkezi: kişi, grup, rol

Bildirim üç tür alıcıya adreslenir:

- kişiye (`sys_user`);
- gruba (`sys_user_group`);
- role (`evren_*`).

Rol üyeleri her zaman bilinemez (Entra grubundan gelen roller), bu yüzden rolün kendisine adreslenir. Uygulama içinde her zaman görünür. Okundu bilgisi kişi başınadır: rol bildirimini her alıcı ayrı okur.

| Olay | Kime |
|---|---|
| Görev atandı / yeniden atandı | Sorumluya |
| Son tarih yaklaşıyor | Sorumluya |
| Süresi geçti | Sorumluya, ayrıca **tırmanma**: grubun yöneticisine (`sys_user_group.manager`); o yoksa Konfigürasyon yöneticisi rolüne |
| Sorumlusu olmayan görev | Konfigürasyon yöneticisine |
| Uyarı ateşlendi / çözüldü | Kuralın rollerine |

Bildirimi görev çalışanı üretir (`notifier`). Görev olaylarını kaldığı yerden okur. Aynı olay için ikinci bildirim üretilmez (tekrar anahtarı). İlk kurulumda imleç bugünden başlar; geçmiş görev olayları toplu bildirime dönmez.

API yalnızca kanal kurulumunu sınamak için "test" bildirimi ekleyebilir (tetikleyici zorlar). Bildirimin bağlantısı yalnızca uygulama içidir (`#/…`, şema kısıtı).

Arayüzde üst çubukta zil ve okunmamış sayısı (kritik varsa kırmızı) bulunur. Sağdan açılan panelde bildirimler listelenir. Bir bildirime tıklamak onu okundu sayar ve ilgili ekrana götürür; panel "Tümünü okundu say" da sunar.

### 3. Dış kanallar: e-posta ve Teams

| Kanal | Ayar | Not |
|---|---|---|
| E-posta | SMTP (`EVREN_SMTP_*`, STARTTLS/SSL) | Düz bağlantı yalnızca geri döngü adresinde |
| Teams | İş akışı gelen web kancası (Uyarlanabilir Kart) | Adresler sır dosyasında (`EVREN_TEAMS_WEBHOOKS_FILE`, `ad=https://…`) |

Sırlar yalnızca görev çalışanındadır. Veritabanı, API ve arayüz Teams'in yalnızca **hedef adlarını** görür.

Teslim hedefleri:

- **Kişi:** kendi adresi. Görev ServiceNow'a teslim edildiyse e-posta gönderilmez; ServiceNow zaten bildirir.
- **Grup:** grubun adresi.
- **Rol ve grup:** yönlendirmeyle (`notify.route`: kanal, hedef, önem eşiği). Yönlendirme Sistem Durumu → Bildirimler'den eklenir ve kapatılır. "Sınama bildirimi" kurulumu dener.

Teslim kuyruğu (`notify.delivery`) hata olursa artan aralıkla yeniden dener. Sınır aşılınca teslim başarısız olur. Hata iletisi adres ve sunucu yanıtı içermez.

### 4. Metrikler: `/metrics` (Prometheus metin biçimi)

API `/metrics` ucunu sunar. Uç yalnızca `EVREN_METRICS_TOKEN_FILE` tanımlıysa açılır ve `Authorization: Bearer` ister. Biçim Prometheus metin biçimidir (0.0.4); yeni bağımlılık eklenmedi (`evren_core.metrics`).

İki tür metrik vardır:

- **İstek metrikleri:** istek sayısı ve süresi. Etiketler yöntem, rota şablonu ve durum sınıfıdır. Kişi, kimlik değeri ve sorgu metni etikete girmez.
- **Platform göstergeleri:** kazıma anında veritabanından okunur. Servis canlılığı, Kafka gecikmesi, olay yayını birikmesi, etkin uyarılar, açık ve süresi geçmiş görevler, açık bulgular, son mührün yaşı, teslim kuyruğu ve veritabanı erişimi.

Eğilim ve geçmiş kurumun Prometheus, Grafana veya OpenTelemetry Collector (prometheus alıcısı) altyapısında tutulur. Evren kendi zaman serisi deposunu kurmaz. Uyarı geçmişi ise Evren'de durur (son 30 gün).

### 5. Arayüz ilkesi

Proje sahibinin isteği şuydu: "ekranları karmaşıklaştırma, her bilgiyi ilk aşamada verme". Buna göre:

- Uyarılar Sistem Durumu'nun en üstünde ve yalnızca **etkin** olanlarla açılır.
- Geçmiş ve kural listesi ikinci adımdadır (sekme ve katlanır bölüm).
- Bildirim kanallarının durumu tek satırlık bir şerittir.
- Yönlendirme ekleme penceresi varsayılan olarak en olası alıcıyı (Platform operatörü) ve önem eşiğini ("Uyarı ve kritik") seçer.

## Değerlendirilen seçenekler

| Seçenek | Neden seçilmedi |
|---|---|
| Uyarıyı yalnızca kurumun Prometheus Alertmanager'ına bırakmak | Kurumda henüz olmayabilir. Görev ve denetim gibi alan uyarıları (görev teslim edilemiyor, mühür bozuk) izleme sisteminin bilgisi değildir. İkisi birlikte: Evren alan uyarılarını yönetir, altyapıyı dış sistem de izler (`/metrics`). |
| OpenTelemetry SDK eklemek | Yeni bağımlılık ve dışa iten (push) yapılandırması gerekirdi. Prometheus metin biçimi Collector'ın alıcısıyla da okunur. İzleme (tracing) ileride ADR ile eklenebilir. |
| Her işçi servisine ayrı `/metrics` sunucusu | Ek açık port ve kimlik doğrulama yüzeyi demekti. Nabız ve gecikme zaten veritabanında; API tek kazıma noktası olarak bunları verir. |
| Rol üyelerini Entra'dan çözüp kişiye göndermek | Graph eşitlemesi yok (U10). Role adresleme ve yönlendirmeyle ekip adresi bugün çalışır; eşitleme gelince kişi düzeyine inilebilir. |
| Bildirim tercihlerini kişi başına tutmak | İlk sürümde gereksiz karmaşıklık olurdu. Kişi e-postası yalnızca kendisine atanan Evren görevlerinde gider; ekip kanalları önem eşiğiyle süzülür. |

## Sonuçlar (olumlu / olumsuz / riskler)

**Olumlu**
- Sorun sayfaya bakan olmadan fark edilir ve doğru role bildirilir.
- Görevler sorumluya ulaşır; süresi geçen görev tırmanır.
- Kurumun izleme sistemi Evren'i kazıyabilir.
- Süreç uygulaması aynı bildirim merkezini ve kanalları kullanacak.

**Olumsuz**
- Uyarıları görev çalışanı değerlendirir. Çalışan durursa uyarı açılamaz. Önlem: arayüzde "uyarı izleyicisi çalışmıyor" ve dış izlemede `evren_service_up{kind="governance"}`.
- Eşikler ilk değerlerdir. Gerçek yükte ayarlanmalıdır (metadata ile, kod değişmeden).

**Riskler**

| Risk | Önlem |
|---|---|
| Bildirim yorgunluğu | Önem eşiği, tekrar anahtarı, süre koşulu. Kişi tercihleri ileride. |

## Güvenlik ve uyum etkisi

- **Tehdit modeli:** `docs/06-guvenlik.md`'ye yeni tablo eklendi. Kapsadığı tehditler:
  - sır sızıntısı;
  - sahte bildirimle oltalama;
  - başkasının bildirimini okuma;
  - uyarının sessiz kapatılması;
  - bildirim seli;
  - `/metrics` sızıntısı;
  - izleyicinin durması.
- **En az yetki:**
  - Görev çalışanı uyarıyı açar ve çözer, onaylayamaz. Yönlendirmeyi yazamaz, okundu bilgisini göremez.
  - API uyarı durumunu yazamaz, bildirim silemez, teslim durumunu yazamaz. Bildirimi yalnızca "test" türünde ekler.
  - Salt okur ve bağlayıcı rol uyarı ve bildirim göremez.
- **Standartlar:**
  - ISO/IEC 27001 A.8.16 (izleme);
  - CIS 8.11'in tamamlayıcısı: bozuk mühür uyarısı Denetçiye gider;
  - ISO/IEC 20000-1: olay ve görev bildirimi, tırmanma.

## Doğrulama (kararın doğru uygulandığı nasıl test edilir)

**Çekirdek** (`test_ops_notify.py`)
- Depodaki kuralların geçerliliği.
- Geçersiz kural ve ürün adının reddi.
- Her sinyalin değerlendirmesi ve uyarı yaşam döngüsü tablosu.
- Atama, hatırlatma, süre aşımı ve tırmanma (yönetici veya rol).
- Tekrar anahtarı.
- Teslim hedefleri: kanal kapalıyken, önem eşiğinde, ServiceNow görevinde.
- İletinin düz metin olması, Markdown kaçışı, iç bağlantı.
- Prometheus metin biçimi.

**Görev çalışanı** (`test_ops_notify_pg.py`; her test geri alınan işlemde)
- Kural alıcılarının rol olarak var olması.
- Süre koşuluyla ateşlenme, bildirim ve çözülme.
- Kısa süreli koşulun ateşlenmemesi.
- Görev bildirimleri ve tırmanma.
- E-posta teslimi; ServiceNow görevinde kişiye e-posta gitmemesi.
- Rol yönlendirmesi ve Teams.
- Yeniden deneme ve başarısızlık.
- Çalışanın yetki sınırları.

**API** (`test_api_notifications.py`)
- Gelen kutusunun kişi, grup ve rolle süzülmesi; okundu bilgisinin kişi başına tutulması; başkasının bildiriminin işaretlenememesi; CSRF.
- Yönlendirme yetkisi ve doğrulaması, Teams hedefinin çalışanın bildirdiği hedef olması.
- Sınama bildirimi ve denetim kaydı.
- Uyarı onayı: gerekçe, bir kez, yalnızca ateşlenmiş uyarı, yetki.
- `/metrics`: belirteçsiz 404, yanlış belirteçle 401, doğrusuyla biçim; kısa belirtecin reddi.

**PostgreSQL** (`test_postgres.py`)
- En az yetki sınırları.
- API'nin yalnızca test bildirimi ekleyebilmesi; bildirimin değiştirilemezliği.

**Tarayıcı**
- Zil ve okunmamış sayısı.
- Panelde okundu sayma ve yönlendirme.
- Uyarı onayı (kısa notun reddi).
- Yönlendirme ekleme.
- Klavye ve mobil.

## Bilinen sınırlar

- Kişi bazında bildirim tercihi ve sessiz saatler yok.
- E-posta HTML değil, düz metin.
- Teams kartında eylem (karttan onay) yok; plan F4-08.
- Rol üyelerine kişi kişi gönderim için Entra Graph eşitlemesi gerekir (U10).
- Uyarı eşikleri ilk değerlerdir; gerçek yükte ayarlanmalı.
- Evren kendi zaman serisi deposunu tutmaz; eğilim kurumun izleme sistemindedir.
- İzleme (tracing, OpenTelemetry SDK) yok.
- SMTP ve Teams yalnızca simülatörle sınandı. Gerçek sunucu ve kanalla kabul U10'dadır.
