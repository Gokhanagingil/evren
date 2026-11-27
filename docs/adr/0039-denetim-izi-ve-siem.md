# ADR-0039: Denetim izi, değiştirilemezlik mühürleri ve SIEM aktarımı

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (hedef: eksiksiz bir ESM platformu; denetim sonucu bunun çıktılarından biri; U8.8), mimari, güvenlik

## Bağlam

Evren her değişikliği zaten kaydediyordu:

- `audit.security_event`: oturum, yetki reddi, rol verme, şema, entegrasyon ve yönetişim kararları;
- `audit.ci_change`: CI öznitelik değişiklikleri (eski ve yeni değer, kaynak, neden);
- `gov.task_event`: görev olayları.

Üç tablo da salt eklemedir (UPDATE, DELETE ve TRUNCATE tetikleyiciyle engelli; `0003_security.sql`). Ancak bir denetimde ve günlük güvenlik işletiminde şu sorulara yanıt yoktu:

1. **Nereden?** Olayda kaynak IP ve isteğin kimliği yoktu. Aynı isteğin ürettiği olaylar birbirine bağlanamıyordu.
2. **Tek yerden kim baktı?** Olaylar üç tabloya dağılmıştı ve arayüzde birleşik bir görünüm yoktu. Denetçi yalnızca CI geçmişini ve yetki geçmişini ayrı ayrı görüyordu.
3. **Kayıt sonradan değişti mi?** Salt ekleme tetikleyicisi uygulama rollerini durdurur. Tablo sahibi veya veritabanı yöneticisi ise tetikleyiciyi kapatıp kaydı değiştirebilir ya da silebilir. Bunun izi kalmıyordu.
4. **Kayıtlar incelendi mi?** CIS 8.11, ISO/IEC 27001 A.8.15–A.8.16 ve ISO/IEC 20000-1 kayıtların düzenli gözden geçirilmesini ve bunun kanıtını bekler. Kanıt yeri yoktu.
5. **Kurum güvenlik izlemesi görüyor mu?** SIEM'e aktarım belgelerde "açık iş" olarak duruyordu.

Proje sahibi şunu vurguladı: Evren ileride süreç uygulamasıyla (olay, değişiklik, talep) eksiksiz bir ESM platformu olacak. Denetim izi bu nedenle yalnızca CMDB'ye özgü olmamalı. Bütün süreçlerin kullanacağı ortak altyapı olmalı.

## Karar

### 1. Tek denetim izi görünümü: `audit.trail`

Görünüm üç akışı aynı sütunlarla birleştirir:

| Sütun | Anlamı |
|---|---|
| `source` | Akış: `security`, `ci`, `task` |
| `id`, `at` | Kayıt kimliği ve zamanı |
| `actor` | Kim |
| `action` | Ne |
| `object` | Hangi nesne |
| `outcome` | Sonuç: izin veya ret |
| `detail` | Ayrıntı |
| `client_ip` | Kaynak IP |
| `request_id` | İstek kimliği |

Süreç uygulamasının olayları ileride yeni akış olarak eklenir (F4-04). Arayüz, dışa aktarma, mühür ve SIEM akışı değişmez.

Olay kataloğu (`evren_core.audit`) her olay türünü Türkçe ada ve kategoriye eşler:

- erişim ve oturum;
- kimlik ve yetki;
- CMDB kayıtları;
- veri modeli;
- entegrasyon;
- yönetişim;
- denetim;
- diğer.

Yetki reddi `<işlem>:<kaynak>` biçiminde gelir ve "Yetki reddi: …" olarak okunur.

### 2. Kaynak IP ve istek kimliği: her ekleme noktasını değiştirmeden

Her API isteği 32 haneli bir istek kimliği alır. Kimlik yanıtta `X-Request-Id` başlığıyla döner.

`deps.context` istek kimliğini ve doğrulanmış istemci adresini veritabanı oturum ayarlarına (GUC) yazar: `evren.request_id`, `evren.client_ip`. `audit.security_event` üzerindeki BEFORE INSERT tetikleyicisi bunları olaya işler. Böylece yirmiden fazla ekleme noktası değişmedi.

Ayrı bağlantıyla yazılan olaylar değerleri sütunlara açıkça yazar: retler (`Ctx.security_event`) ve oturum açma olayları (`auth.py`).

Bağlantı havuza dönerken ayarlar temizlenir (`db.make_pool`, `reset`). Bir isteğin bağlamı sonraki isteğe geçmez.

Yük dengeleyici arkasında gerçek istemci adresi için `EVREN_TRUSTED_PROXIES` kullanılır. Bu, vekil adreslerinin (IP veya CIDR) listesidir ve yalnızca bu adreslerden gelen `X-Forwarded-For` başlığına güvenilir. "Her adres" (`0.0.0.0/0`, `::/0`) yapılandırma hatasıdır. Liste boşsa doğrudan bağlanan adres yazılır.

### 3. Değiştirilemezlik mühürleri: saatlik, akış başına, zincirli

Görev çalışanı (`evren_governance.audit_seal`) her turda mühürleme yapar:

- Bitmiş ve 15 dakikalık bekleme süresi geçmiş saatlik dönemleri mühürler. Bekleme süresi, dönemin sonunda başlayıp geç işlenen işlemlerin de mühre girmesi içindir.
- Dönemin kayıtlarını sabit sırayla (kimlik) ve sabit biçimle yazar: sıralı anahtarlı JSON, UTC mikro saniye.
- SHA-256 özetini ve satır sayısını alır.
- Zincir değeri `chain = sha256(önceki_zincir ":" özet)` olarak hesaplanır. İlk mührün önceki zinciri 64 sıfırdır.

Mühürleyen ve doğrulayan aynı SQL metnini ve aynı biçimi kullanır (`SEAL_SQL`, `canonical_line`).

- Boş dönem de mühürlenir. Bir dönemin kaldırılması böylece görünür olur.
- Bir akışın ilk mührü **başlangıç mührüdür**: kurulumdan önceki bütün geçmişi tek dönemde kapsar. Yıllarca geriye gidip binlerce boş dönem üretilmez.
- Çalışan bir süre kapalı kalırsa aradaki saatler sırayla mühürlenir. Her turda en çok 200 dönem mühürlenir.

Veritabanı zinciri ayrıca kendisi korur:

- `audit.seal` salt eklemedir.
- Ekleme tetikleyicisi (`tg_seal_link`) yeni mührün bir öncekine bitişik olmasını ve doğru önceki zincir değerini taşımasını zorlar. Boşluk ve sahte bağ reddedilir.
- `(stream, period_start)` benzersizdir. İki çalışan yarışırsa ikinci ekleme reddedilir ve o akış bir sonraki tura kalır.

### 4. Doğrulama: kendiliğinden ve elle, sonuç kanıttır

Doğrulama (`check_seal`, `verify_chain`) mührün dönemini bugünkü kayıtlarla yeniden hesaplar. Şunları yakalar:

| Durum | Doğrulamanın dediği |
|---|---|
| Döneme sonradan kayıt eklendi | "kayıt eklenmiş" |
| Dönemden kayıt silindi | "kayıt silinmiş" |
| Kayıt değiştirildi | "değiştirilmiş" |
| Mühür kaydının kendisi değiştirildi | zincir tutmuyor |
| Bir mühür kaldırıldı | "önceki mühürle bağ kopuk" |
| Dönem atlandı | "bitişik değil" |

Doğrulama iki yoldan yapılır:

- **Görev çalışanı** günde bir son 7 günün mühürlerini kendiliğinden doğrular.
- **Denetçi** istediği dönemi (en çok 92 gün) arayüzden doğrular.

Her sonuç `audit.seal_check`'e yazılır. Tablo salt eklemedir. Sorunlu sonuç:

- yönetim panelinde "Size düşen işler"e düşer;
- `audit.seal_check` olayıyla SIEM'e gider.

### 5. İnceleme kanıtı: `audit.review`

Denetçi bir dönemi gözden geçirir ve sonucu kaydeder: "Bulgu yok" veya "Bulgu var", artı not (10–4000 karakter). Kayıt sırasında sırayla şunlar olur:

1. Dönemin mühürleri doğrulanır. Doğrulama kanıtı inceleme reddedilse de kalır.
2. Olay ve ret sayıları inceleme kaydına sabitlenir.

Mühür sorunu varken "Bulgu yok" kaydedilmez.

- İnceleme salt eklemedir ve tekrar güvenlidir (`idempotency_key`).
- Son inceleme 7 günden eskiyse Denetçiye "Haftalık denetim izi incelemesi" işi düşer (CIS 8.11).

### 6. SIEM'e aktarım: olay omurgası üzerinden

Her güvenlik olayı, her mühür ve her doğrulama aynı veritabanı işleminde `events.outbox`'a yazılır. Bunu SECURITY DEFINER tetikleyiciler yapar. Uzlaştırıcının outbox yayıncısı bu kayıtları `evren.audit.v1` Kafka konusuna gönderir:

- 3 bölüm;
- 1 yıl saklama;
- `audit.*` olay türleri bu konuya yönlenir (`messaging.topic_for_event`).

Kurum SIEM'i konuyu tüketir. Ürüne özgü bağlayıcı (Splunk HEC, QRadar, Sentinel) ürün seçilince eklenir. Konu sözleşmesi değişmez.

Mühür zincir değerlerinin SIEM'de bulunması **veritabanı dışında bir tanık** sağlar. Tablo sahibi hem kayıtları hem mühürleri değiştirip zinciri yeniden hesaplasa bile, SIEM'deki zincir değerleriyle karşılaştırma sahteciliği ortaya çıkarır.

### 7. Arayüz ve yetki

**Denetim İzi** sayfasının (`#/denetim`) üç sekmesi vardır.

**Olaylar**
- Süzgeçler: dönem, akış, kategori, sonuç, arama.
- Sayfalama imleçlidir.
- Bir olaydan iz sürülebilir: "Bu kişinin olayları", "Bu nesnenin olayları", "Aynı istekteki olaylar", "Bu adresten gelenler".
- Ayrıntıda kaynak IP, istek kimliği ve olayın mühür durumu görünür.
- Dışa aktarma CSV veya NDJSON'dır, en çok 50.000 kayıttır.

**Bütünlük**
- Akış başına son mühür.
- Gecikme uyarısı.
- Kendiliğinden ve elle doğrulama, sorunlu doğrulamalar.

**İncelemeler**
- İnceleme listesi ve kaydetme penceresi.

Pencere dönemin özetini kontrol listesi olarak gösterir: ret sayısı ve en çok ret alanlar, yetki değişiklikleri, model ve entegrasyon değişiklikleri, denetim olayları.

Yetkiler:

| İşlem | İzin | Kimde |
|---|---|---|
| Okuma ve dışa aktarma | `audit/audit_read` | Denetçi |
| Doğrulama ve inceleme kaydı | `audit/audit_review` (yeni) | Denetçi |

Görevler ayrılığı gereği Platform yöneticisi kendi denetçisi olamaz ve denetim izini göremez. Okuma denetlenmez. Dışa aktarma ise denetlenir: kim, hangi süzgeçle, kaç kayıt aktardı.

CI değişikliğinde okuyanın görme yetkisi olmayan alanın eski ve yeni değeri gizlenir. Ölçüt CI geçmişindekiyle aynıdır.

## Değerlendirilen seçenekler

| Seçenek | Neden seçilmedi |
|---|---|
| Her olaya kendi özetini ve önceki olayın özetini yazmak (satır düzeyinde zincir) | Her eklemede satır kilidi ve sıralama gerekir. Uzlaştırıcı yüksek hacimde paralel yazar. Ekleme noktalarının hepsi değişirdi. Saatlik mühür aynı güvenceyi ekleme yoluna dokunmadan verir. |
| Yalnızca SIEM'e güvenmek (veritabanında mühür yok) | SIEM ürünü ve saklama süresi henüz belli değil. Evren içinden doğrulama ve inceleme kanıtı olmazdı. |
| WORM depolama veya harici zaman damgası (RFC 3161) | Kurum altyapısına bağlı. Mühür zinciri hazır olduğu için ileride zincir değeri zaman damgası yetkilisine gönderilebilir (bilinen sınır). |
| İstek kimliği ve IP'yi her ekleme noktasında açıkça yazmak | Yirmiden fazla yer değişirdi. Yeni eklenen bir yerde unutulma riski olurdu. Oturum ayarı + tetikleyici tek noktadır. |
| Okumaları da denetlemek | Gürültü çok olur ve asıl olayları bastırır. Kısıtlı veri okuması (`restricted_read`) ve dışa aktarma zaten denetlenir. |

## Sonuçlar (olumlu / olumsuz / riskler)

**Olumlu**
- "Kim, ne zaman, neyi, nereden, hangi sonuçla" tek ekranda ve tek API'de.
- Kayıtların sonradan değişmediği kanıtlanabilir.
- İnceleme yapıldığı kanıtlanabilir.
- Güvenlik izlemesi olaylara gerçek zamanlıya yakın erişir.
- Süreç uygulaması aynı altyapıyı kullanacak.

**Olumsuz**
- Görev çalışanının işi büyüdü: her tur mühür, günde bir doğrulama. Doğrulama son 7 günün bütün kayıtlarını okur. Büyük kurulumda gece yükü olarak izlenmeli.
- Dışa aktarma 50.000 kayıtla sınırlı. Daha geniş dönem için SIEM veya parça parça aktarma kullanılır.

**Riskler**

| Risk | Önlem |
|---|---|
| Uzun süren bir işlemin kaydı (15 dakikadan uzun) mühürlendikten sonra işlenirse doğrulama onu "eklenmiş" sayar | Bu yanlış alarmdır. İnceleme notunda açıklanır. Bekleme süresi uygulama işlemlerinin süresinden çok uzundur. |
| Saklama politikasıyla eski kayıtlar silinirse eski mühürler doğrulanamaz | Saklama işi henüz yok. Eklenirken silinen dönemin mühürleri "arşivlendi" olarak işaretlenmeli (bilinen sınır). |

## Güvenlik ve uyum etkisi

**Tehdit modeli** (`docs/06-guvenlik.md`) için:

- T (denetim izinin değiştirilmesi): ayrıcalıklı kullanıcıya karşı da tespit sağlandı (mühür ve SIEM tanığı).
- R (inkâr): istek kimliği ve kaynak IP eklendi.
- I (dışa aktarma): dışa aktarma denetlenir. CSV'de formül enjeksiyonuna karşı hücre koruması var: `=`, `+`, `-`, `@`, sekme ile başlayan değerin önüne `'` konur.

**En az yetki:**

- Görev çalışanı mühür ekler; değiştiremez, silemez.
- Görev çalışanı güvenlik olayı yazamaz ve incelemeleri göremez.
- API mühür yazamaz; yalnızca doğrulama ve inceleme kanıtı ekler (sütun düzeyi yetki).
- Salt okur rol denetim izini göremez.
- Denetçi salt okuru (`evren_auditor_ro`) görünümü, mühürleri ve incelemeleri okur, yazamaz.

**Standartlar:**

| Standart | Karşılığı |
|---|---|
| ISO/IEC 27001:2022 A.8.15 (kayıt) | Birleşik iz, kaynak IP, istek kimliği |
| A.8.16 (izleme) | SIEM akışı, sorunlu mühür uyarısı |
| A.5.28 (kanıt toplama) | Dışa aktarma, mühür, inceleme |
| CIS Controls v8 | 8.2 kayıt toplama, 8.5 ayrıntılı kayıt, 8.9 merkezi toplama, 8.11 kayıt gözden geçirme |
| ISO/IEC 20000-1 | Yapılandırma ve değişiklik kayıtlarının bütünlüğü ve izlenebilirliği. Süreç uygulamasıyla genişler (F4-04, F4-05). |
| OWASP ASVS 7.x (kayıt ve izleme) | Güvenlik olaylarında kim/ne/ne zaman/nereden, kayıt bütünlüğü, kayıtta sır yok |

## Doğrulama (kararın doğru uygulandığı nasıl test edilir)

**Çekirdek** (`packages/evren-core/tests/test_audit.py`)
- Olay kataloğu ve Türkçe adlar.
- Sabit biçimin saat dilimi ve anahtar sırasından bağımsızlığı.
- Özet ve zincir.
- Başlangıç mührü ve saatlik dönemler (bekleme süresi, boşluksuz yakalama, sınır).
- Doğrulamanın ekleme, silme ve değiştirmeyi; mühür kaldırmayı; mühür kaydının değiştirilmesini; dönem atlamayı yakalaması.
- CSV hücre koruması.

**Görev çalışanı** (`services/governance/tests/test_audit_seal_pg.py`; her test geri alınan işlemde)
- Ardışık ve zincirli mühür; yeniden çalıştırmada çift mühür yok.
- Mühürdeki sayı ve özetin dönem kayıtlarıyla aynı olması.
- Zincir tetikleyicisinin boşluğu ve sahte bağı reddetmesi.
- Çalışanın mühür ve kayıt değiştirememesi.
- Günlük doğrulamanın kurcalamayı (ekleme, değiştirme) yakalayıp kanıt yazması.
- Mühür, doğrulama ve güvenlik olayının outbox'a düşmesi.

**API** (`services/api/tests/test_api_audit.py`)
- Her yanıtta istek kimliği.
- Ret olayında kaynak IP ve istek kimliği; süzgeçler (kişi, istek, IP, kategori, sonuç).
- İmleçli sayfalamada boşluk ve tekrar olmaması.
- Olay ayrıntısında mühür durumu ve ilgili olaylar; aynı izde CI ve görev olayları.
- Geçersiz dönem ve imlecin reddi.
- CSV formül koruması; dışa aktarmanın denetlenmesi.
- Bütünlük görünümü.
- Kurcalamanın elle doğrulamada yakalanması; mühür sorunu varken "Bulgu yok"un reddi; panelde uyarı.
- İncelemenin kaydı, tekrarı ve çakışması.
- Okuyucu, Platform yöneticisi ve Konfigürasyon yöneticisinin 403 alması; CSRF.
- Güvenilir vekil ayarının doğrulanması.

**PostgreSQL** (`tests/test_postgres.py`)
- Mühür, doğrulama ve incelemenin tablo sahibi için bile salt ekleme olması.
- En az yetki sınırları ve izinli yollar.

**Tarayıcı** (`apps/web/tests/test_web_api.py`)
- Denetçi ret olaylarını arar.
- Ayrıntıda mühür durumunu görür.
- Kişiye göre iz sürer ve süzgeci klavyeyle kaldırır.
- CSV indirir.
- Mühürleri doğrular.
- Kısa notu reddettirip incelemeyi kaydeder.
- Yetkisiz kişi bildirim görür.
- Mobilde üç sekmede de yatay taşma yok.

## Bilinen sınırlar

- **SIEM ürününe özgü teslim:** Splunk HEC, Syslog/CEF veya Sentinel teslimi ürün seçilince eklenecek. Bugün sözleşme Kafka konusudur.
- **Zincir değerinin dışarıdaki kopyasıyla karşılaştırma:** SIEM'deki kopyayla karşılaştırma bir işletim prosedürüdür. Evren SIEM'i okumaz.
- **Saklama ve arşivleme:** Denetim kaydı için saklama süresi ve arşivleme (soğuk depolama, mühürle birlikte) yok. Kurum politikası belli olunca eklenecek.
- **Harici zaman damgası:** Zincir değerinin RFC 3161 zaman damgası yetkilisine gönderilmesi yok.
- **Diğer servislerin güvenlik olayları:**
  - Bağlayıcı belirteci aracı ve alım geçidi istek kimliği taşımaz.
  - Görev çalışanının güvenlik olayları IP içermez; çalışan sistem kimliğiyle (`evren:…`) görünür.
- **Test verisi:** Testler sentetik veriyle yapıldı. Kurcalama testleri tablo sahibi rolüyle ve geri alınan işlemde yapıldı. Gerçek SIEM tüketimi canlı entegrasyonda ayrıca sınanacak.
