# Evren CMDB — proje planı

> Bu dosya `apps/web/content/roadmap.json` dosyasından üretilir (`python tools/build_plan.py`); elle düzenlemeyin. Aynı plan uygulamadaki **Yol Haritası** sayfasında, mimari ise **Büyük Resim** ve **Mimari Harita** sayfalarındadır.

**Son güncelleme:** 27.11.2026 · **Etkin faz:** F0 — Temel ve gölge mod · **İlerleme:** 20/63 adım tamamlandı

## Nasıl izlenir

- Adımlar faz içinde **yapılış sırasıyla** listelenir. İşaretli kutu tamamlanan adımdır; durum etiketi devam eden, planlanan veya engelli adımı gösterir.
- **Sizden gereken** satırı, adımın başlaması için kurumdan gelmesi gereken karar veya erişimdir. Bunlar zamanında gelmezse ilgili adım bekler; diğer adımlar sürer.
- Bir faz, çıkış kriterlerinin tamamı karşılanınca kapanır.

## Uçtan uca izlek — şu anki çalışma sırası

> Proje sahibinin beklentisi (25.11.2026): PostgreSQL, Kafka ve OpenSearch altyapısı kurulsun; kaynak verisi anlamlandırılarak akıl katmanından geçsin (elle girdi dahil); veri modeli oluşsun; büyük resim uçtan uca tamamlansın ve tek arayüzden yönetilsin. Fazlar yönetişim için kalır; sıra bu izlekle belirlenir. Dış girdiler (Entra, ServiceNow hesabı, kaynak hesapları, hedef ortam) en sondadır.

**İzlek ilerlemesi:** 7/10 adım tamamlandı

- [x] **U1** Altyapı yığını: Evren konteyner imajı; tek komutla PostgreSQL + Kafka + OpenSearch + Evren servisleri; şema/rol kurulum aracı; CI'da yığın ayağa kalkar ve sağlık denetlenir — _Tamamlandı_ (ilgili: F0-10)
- [x] **U2** Olay omurgası: alım geçidi (bağlayıcı kimliği) → Kafka → uzlaştırıcı tüketicisi → golden record → değişiklik olayları Kafka'ya (outbox yayını) — _Tamamlandı_ (ilgili: F0-10)
- [x] **U3** Arama: OpenSearch dizinleyici; arayüz araması ve CI listesi OpenSearch'ten (PostgreSQL yedek) — _Tamamlandı_ (ilgili: F1-08)
- [x] **U4** CMDB veri modeli: ServiceNow uyumlu (ve daha iyisi) sınıf kütüphanesi ve CSDM katmanları, ilişki tipleri, yaşam döngüsü durumları; sınıf ağacı ve alan gezgini sayfası (değişiklik setleriyle bütünleşik) — bağlayıcıların yazacağı hedef sınıflar hazır olur — _Tamamlandı_ (ilgili: F0-17, F0-07, F1-11)
- [x] **U5** Bağlayıcılar ve kaynak yönetimi: 6 kaynağın istemcisi (kaydedilmiş örnek yanıtlarla); arayüzden şimdi çalıştır, zamanlama, çalışma geçmişi — _Tamamlandı_ (ilgili: F0-11, F1-01, F1-02, F1-03, F1-04, F1-14)
- [x] **U6** Elle girdi: arayüzden CI oluşturma, ilişki ekleme/kaldırma, CSV ile toplu içe aktarma — hepsi akıl katmanından geçer — _Tamamlandı_ (ilgili: F1-21, F1-17)
- [x] **U7** Yaşam döngüsü ve ilişkiler: eskime işletimi, bağımlı CI kimliği, uygulama/hizmet katmanı, ilişki haritası — _Tamamlandı_ (ilgili: F1-06, F1-07, F1-19)
- [ ] **U8** Yönetim ekranları: tek yönetim paneli ve kullanım kılavuzu altyapısı, sistem durumu, kullanıcılar ve roller, yönetişim görevleri (ServiceNow ve Evren kanalı; zamanlanmış kampanyalar, kural tabanlı bulgu görevleri), sağlık panosu, sihirbazlar, denetim izi — _Devam ediyor_ (ilgili: F1-09, F1-10, F1-15, F1-16, F1-18, F1-24, F1-25, F1-26)
- [ ] **U9** ServiceNow replika ve MCP yapısı: yazma yönü (IRE) ve Evren MCP sunucusu, kaydedilmiş örneklerle — _Planlandı_ (ilgili: F2-01, F2-02, F2-03)
- [ ] **U10** Canlı kabul: Entra, ServiceNow, kaynak hesapları ve hedef ortam bilgileriyle bağlantılar ve kabul testleri — _Planlandı_ (ilgili: F0-07, F0-08, F0-09, F2-09, F1-27)
  - Sizden gereken: Entra uygulama kaydı, ServiceNow salt okunur hesabı, kaynak servis hesapları, hedef çalışma ortamı (en sonda)

## Sıradaki adımlar (fazlara göre)

1. **F0-07** ServiceNow metadata dışa aktarımı; işaretli öğelerin (sınıf, alan, seçim listesi, ilişki tipi/kuralı) doğrulanması — _Devam ediyor_  
   Sizden gereken: Kalan 82 öğe ve kurum özelleştirmeleri için ServiceNow salt okunur servis hesabı (sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma) — canlı kabulde
1. **F0-09** API servisi, Entra ID oturumu ve Entra grup → rol eşlemesi — _Devam ediyor_  
   Sizden gereken: Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı
1. **F0-08** ServiceNow CMDB ve referans verilerinin ilk yüklemesi (sys_id korunarak, salt okunur) — _Devam ediyor_  
   Sizden gereken: ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi
1. **F0-11** vCenter ve Zabbix bağlayıcıları — _Devam ediyor_  
   Sizden gereken: vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi
1. **F0-13** Gölge mod raporları: SN CMDB'deki mükerrerler ve kaynaklar arası kapsam boşlukları — _Devam ediyor_  
   Sizden gereken: Gölge mod raporunu değerlendirecek CMDB sahibi ve süreç sahipleri

## Sizden gerekenler (karar ve erişim)

| Adım | Gereken | Durum |
| --- | --- | --- |
| F0-07 | Kalan 82 öğe ve kurum özelleştirmeleri için ServiceNow salt okunur servis hesabı (sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma) — canlı kabulde | Devam ediyor |
| F0-09 | Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı | Devam ediyor |
| F0-08 | ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi | Devam ediyor |
| F0-11 | vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi | Devam ediyor |
| F0-13 | Gölge mod raporunu değerlendirecek CMDB sahibi ve süreç sahipleri | Devam ediyor |
| F1-01 | BMC Helix Discovery API kullanıcısı; kurulum on-prem mi SaaS mı | Devam ediyor |
| F1-02 | Entra uygulama izni Machine.Read.All (yalnızca okuma); vekil sunucu çıkış izni | Devam ediyor |
| F1-03 | NNMi web servis kullanıcısı ve NNMi sürümü | Devam ediyor |
| F1-04 | ServiceNow change_request ve task_ci okuma izni | Devam ediyor |
| F1-10 | Süreç sahipleri ve iç denetimin rol–yetki matrisi onayı | Devam ediyor |
| F1-24 | ServiceNow'da görevin açılacağı tablo ve atama grubu kararı; o tabloya yazma izni olan entegrasyon hesabı (canlı kabul U10) | Planlandı |
| F1-25 | Konfigürasyon yöneticisi rolünün sahibi ve kampanya takvimi (süreç sahipleri) | Planlandı |
| F2-01 | ServiceNow tarafında Evren veri kaynağı için platform ekibi onayı | Planlandı |
| F2-04 | ServiceNow hesap yöneticisiyle abonelik birimi görüşmesi | Planlandı |
| F2-08 | GRC Platformu'nda CMDB modülünün Evren bağlantısıyla yapılandırılması | Planlandı |
| F3-01 | MID Server veya API ağ geçidi kapasitesi | Planlandı |
| F3-02 | Onaylı yapay zekâ modeli ve veri yerleşimi politikası | Planlandı |

### Açık sorular

- BMC Helix Discovery kurulumu on-prem appliance mı, Helix SaaS mı? (API aynı; ağ yolu ve kimlik bilgisi farklı)
- ServiceNow örneği SaaS mı; MID Server mevcut mu?
- Hedef çalışma ortamı: Kubernetes/OpenShift mi, sanal makine üzerinde Docker mı?
- Kurumun sır kasası ve SIEM ürünü hangisi?
- Yapay zekâ için onaylı model ve veri yerleşimi politikası nedir?

## F0 · Temel ve gölge mod

_Ay 0–3 · Ara 2026 – Şub 2027 (öneri)_ · 12/17 adım

Güvenli temel, ServiceNow CMDB'nin aynı sys_id'lerle yüklenmesi ve ilk iki kaynağın gölge modda eşleştirilmesi. ServiceNow'a hiçbir yazma yapılmaz.

- [x] **F0-01** Proje iskeleti, Claude Code yönergeleri ve durum dosyası
- [x] **F0-02** Çekirdek kütüphane: normalizasyon, kimlik çözümleme, uzlaştırma, devre kesici (testli)
- [x] **F0-03** PostgreSQL şeması, metadata sözlüğü ve sn.* görünümleri
- [x] **F0-04** Veritabanı güvenlik temeli: en az yetkili roller, salt ekleme denetim izi, görevler ayrılığı
- [x] **F0-05** Uygulama içi Mimari Harita ve Yol Haritası sayfaları
- [x] **F0-14** Kaynak rolleri: kurallar ürüne değil role bağlı; gölge/devir/emeklilik yaşam döngüsü (ADR-0009)
- [x] **F0-15** Büyük resim çizimi ve adım adım proje planı
- [x] **F0-16** Arayüzün GRC Platformu görsel diline taşınması; Veri Kaynakları, Eşleştirme Kuyruğu, CI 360°, Kurallar ve GRC Kontrolleri önizleme ekranları (sentetik demo)
  - Not: Demo verisi çekirdeğin gerçek hattından üretilir (tools/build_demo.py); kalıcı kararlar API ile (F0-09, F0-10).
- [x] **F0-06** CI güvenlik kapıları: gitleaks, Trivy, bandit, pip-audit, SBOM
  - Not: 25.11.2026: hash'li kilitler (14 gün bekleme), imaj digest'i, gitleaks istisnası olumsuz testli; iki iş akışı yeşil.
- [ ] **F0-07** ServiceNow metadata dışa aktarımı; işaretli öğelerin (sınıf, alan, seçim listesi, ilişki tipi/kuralı) doğrulanması — _Devam ediyor_
  - Not: 26.11.2026: herkese açık ServiceNow belgeleriyle doğrulandı (proje sahibinin yönlendirmesi; instance gerekmedi). Kanıt defteri metadata/verification.yaml: 181 kayıt, 95 kaynak; 74 doğrulandı, 18 düzeltildi, 5 kaldırıldı, 2 Evren tanımı; 55 kısmi + 27 bulunamadı (82 öğe işaretli kaldı). Kalanlar ve kuruma özgü özelleştirmeler canlı kabulde salt okunur sözlük dökümüyle (U10).
  - Sizden gereken: Kalan 82 öğe ve kurum özelleştirmeleri için ServiceNow salt okunur servis hesabı (sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma) — canlı kabulde
- [ ] **F0-09** API servisi, Entra ID oturumu ve Entra grup → rol eşlemesi — _Devam ediyor_
  - Not: Kod ve testler tamam (services/api, ADR-0010, ADR-0012): okuma uç noktaları, kalıcı kuyruk kararı, birleştirmeyi ayırma, elle düzenleme (Manual kaynak kaydı, alan başına sonuç), Entra OIDC + PKCE, rol eşlemesi, girişte SoD; arayüz API modunda çalışıyor. Canlı Entra kaydıyla kabul bekliyor. Makine hesapları ve ilişki düzenleme sırada.
  - Sizden gereken: Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı
- [ ] **F0-08** ServiceNow CMDB ve referans verilerinin ilk yüklemesi (sys_id korunarak, salt okunur) — _Devam ediyor_
  - Not: 26.11.2026: CI tarafı hazır — ServiceNow Table API istemcisi (U5.2) ve alım yolunda sys_id koruması (uzlaştırıcı; sync.sn_ci_map ile replikadan dönen CI), simülatör ve yığın duman testiyle sınandı. Referans verileri de hazır (ADR-0022, göç 0020): grup, kişi, konum, şirket, departman, maliyet merkezi ve model aynı çalışmada çekilir, uzlaştırıcı aynı sys_id ile ref.* tablolarına yazar (kişisel veri en az, yalnızca ITSM rolü, sütun düzeyi yetki). Canlı yükleme sırada.
  - Sizden gereken: ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi
- [x] **F0-10** Alım geçidi, Kafka ve uzlaştırıcı servisi
  - Not: PostgreSQL deposu, aday dizini, devre kesici, sürekli çalışan uzlaştırıcı (ADR-0013); alım geçidi (bağlayıcı belirteci, kaynak yetkisi), Kafka alım tüketicisi (en az bir kez, kalıcı sayaçlar, DLQ), çalışma kapanışı ve outbox yayını (ADR-0017). Canlı kurum ortamında kabul U10'da.
- [ ] **F0-11** vCenter ve Zabbix bağlayıcıları — _Devam ediyor_
  - Not: 26.11.2026: Zabbix (U5.1) ve vCenter (U5.2, VI/JSON) istemcileri hazır; simülatör, geçit, uzlaştırıcı ve yığın duman testiyle sınandı. Gerçek kaynaklarda kabul U10'da (salt okunur servis hesapları).
  - Sizden gereken: vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi
- [x] **F0-12** Eşleştirme kuyruğu ekranı (yan yana karşılaştırma, birleştir/ayır)
  - Not: Yan yana karşılaştırma, kanıtlar, kalıcı kararlar (birleştir, yeni CI, ertele, kaynakta düzelt) ve CI panelinden birleştirmeyi ayırma (ADR-0011: kimlik profili yeniden kurulur, ayrılan kaynağın değerleri temizlenir); uzlaştırıcı uygular. Sentetik veriyle test edildi.
- [ ] **F0-13** Gölge mod raporları: SN CMDB'deki mükerrerler ve kaynaklar arası kapsam boşlukları — _Devam ediyor_
  - Not: Rapor (kapsam, sınıf × rol, mükerrer adayları, değer farkları, gölge kaynak uyumu), arayüz sayfası ve değiştirilemez SHA-256 kanıt kaydı hazır (ADR-0014); sentetik veriyle test edildi. Gerçek ServiceNow verisiyle ilk rapor F0-08 yüklemesinden sonra; değerlendirme CMDB sahibinde.
  - Sizden gereken: Gölge mod raporunu değerlendirecek CMDB sahibi ve süreç sahipleri
- [x] **F0-17** CMDB veri modeli: ServiceNow uyumlu sınıf kütüphanesi (uç nokta, bulut, Kubernetes, depolama, ara katman yazılımı), CSDM katmanları, ilişki tipleri ve yaşam döngüsü durumları
  - Not: 26.11.2026 büyük resim incelemesinde planda karşılığı olmadığı görüldü; U4 ile tamamlandı (ADR-0019). Adların kurum örneğinden doğrulanması F0-07'de.

**Çıkış kriterleri**

- [ ] ServiceNow CMDB aynı sys_id'lerle yüklendi; sınıf bazında kayıt sayıları eşit
- [ ] vCenter ve Zabbix kayıtlarının en az %95'i otomatik eşleşti
- [ ] Örneklem denetiminde yanlış birleştirme oranı %0,5'in altında
- [x] ServiceNow'a hiçbir yazma yapılmadı
- [x] CI güvenlik kapıları yeşil; yüksek veya kritik bulgu yok
- [ ] İlk kapsam raporu GRC Platformu'na kanıt olarak aktarıldı

**Riskler**

- BMC Helix Discovery kurulum tipi (on-prem/SaaS) ve API erişiminin zamanında açılması
- ServiceNow örneğindeki sınıf/alan farkları
- Kaynak sistemlere salt okunur servis hesabı erişimlerinin zamanında açılması

## F1 · Tüm kaynaklar ve uzlaştırma

_Ay 3–6 · Mar – May 2027 (öneri)_ · 8/27 adım

Beş kaynağın tamamı canlı. Öznitelik düzeyinde uzlaştırma, devre kesici, eskime, kaynak bilgisi ve sağlık ölçümü çalışır.

- [ ] **F1-01** BMC Helix Discovery bağlayıcısı ve alan adlarının kurum örneğinde doğrulanması — _Devam ediyor_
  - Not: 26.11.2026: eşleme ve REST istemcisi hazır (U5.2: data/search, results_id ile sayfalama, sayı denetimi, simülatör; uzlaştırıcı ve yığın duman testiyle sınandı). On-prem ve SaaS aynı REST API'yi kullanır. Alan adlarının ve sorgudaki ilişki yolunun kurum örneğinde doğrulanması U10'da.
  - Sizden gereken: BMC Helix Discovery API kullanıcısı; kurulum on-prem mi SaaS mı
- [ ] **F1-02** Defender bağlayıcısı; kısıtlı güvenlik duruşu tablosu — _Devam ediyor_
  - Not: 26.11.2026: istemci hazır (U5.2: Entra istemci kimliği, nextLink güvenliği, belirteç yenileme, simülatör; uzlaştırıcı ve yığın duman testiyle sınandı). Risk ve maruziyet CI özniteliği olmaz; kısıtlı duruş yolu da hazır (ADR-0023): zarfta ayrı posture bölümü, yalnızca uç nokta güvenliği rolü cmdb.ci_security_posture'a yazar, API yalnızca restricted yetkiyle gösterir ve her okumayı denetim izine yazar. Zafiyet sayıları (ayrı API) sırada.
  - Sizden gereken: Entra uygulama izni Machine.Read.All (yalnızca okuma); vekil sunucu çıkış izni
- [ ] **F1-03** NNMi: düğüm, arayüz ve L2 bağlantıları — _Devam ediyor_
  - Not: 26.11.2026: düğüm istemcisi hazır (U5.2: WS-I SOAP getNodes, id sırasıyla sayfalama ve sıralama güvencesi, güvenli XML ayrıştırma, simülatör; uzlaştırıcı ve yığın duman testiyle sınandı). Arayüzler ve L2 de hazır: getInterfaces ile MAC adresleri kimlik eşleşmesine ve fiziksel arayüz sayısına, aynı connectionId'yi paylaşan arayüzler cihazlar arası 'Connects to' ilişkisine (kimliği küçük uç üst). Ağ adaptörü CI'ı bağımlı CI kimliğiyle (U7); süzgeç ve alan adları kurum sürümünde doğrulanacak.
  - Sizden gereken: NNMi web servis kullanıcısı ve NNMi sürümü
- [ ] **F1-04** ServiceNow Change bağlayıcısı ve yetkisiz değişiklik tespiti — _Devam ediyor_
  - Not: 26.11.2026 (ADR-0025, U5.5, göç 0021): servicenow_change bağlayıcısı (change_request + task_ci, açık ve son 30 gün, delta; durum/onay/tür Evren sözlüğüne çevrilir), yalnızca değişiklik rolüyle ref.sn_change_request ve ref.sn_task_ci; gözlemci rolünün kendi kaydındaki izlenen öznitelik değişikliği insight.config_change'e; bekleme süresinden sonra onaylı pencereye göre karar, unauthorized_change anomalisi, sonradan gelen kayıtla kanıtlı kapanış, devre kesici açıkken bastırma; CI ayrıntısında değişiklik denetimi, Veri sorumlusu kararı (anomaly_review), CMDB-05. Simülatör, uzlaştırıcı, API, tarayıcı ve yığın duman testiyle sınandı. Gerçek ServiceNow kabulü U10.
  - Sizden gereken: ServiceNow change_request ve task_ci okuma izni
- [ ] **F1-05** Uzlaştırma kuralları ve "bu değer nereden geldi?" görünümü — _Planlandı_
- [x] **F1-06** Devre kesici ve eskime işletimi; tatbikat
  - Not: 26.11.2026 (ADR-0028, U7.1, göç 0026): yalnızca gözlemci roller (keşif, izleme, güvenlik; ITSM/elle/platform canlı tutmaz); bütün gözlemciler görmüyor ve son çalışmaları tam ve OK ise eskimiş → emekliye ayırma adayı (insight.staleness_case), aksi hâlde bastırılır; golden record kendiliğinden değişmez; Veri sorumlusu gerekçeyle emekliye ayırır (yaşam döngüsü Manual kaydına, neden staleness) veya korur; uzlaştırıcı uygulama anında yeniden denetler; yeniden görülme öneriyi kapatır, emekli CI görülürse anomali. Tatbikat: gerçek devre kesiciyle 5 senaryolu PostgreSQL testi (test_staleness_pg). Eskime sayfası ve CI panelinde bölüm.
- [x] **F1-07** Bağımlı CI kimliği (uygulama, ağ adaptörü)
  - Not: 26.11.2026 (ADR-0029, U7.2, göç 0027): alım zarfında parent (ana kaydın kaynak anahtarı); uzlaştırıcı ana CI'ı bulur, yerel anahtarı ana CI bağlamında ingest.dependent_key'de arar (küresel dizine girmez), ana kayıt sonra gelirse çalışma sonunda çözer, eksik veri tahmin edilmez; başvuru alanı veya ilişki (yön ilişki kurallarından). NNMi MAC'li arayüzleri ağ adaptörü CI'ı olur; CI ayrıntısında bağımlı CI'lar.
- [ ] **F1-08** OpenSearch araması ve CI 360° ekranı — _Devam ediyor_
  - Not: Arama tamam (U3, ADR-0018); CI 360° ekranı (ilişkiler, etki) U7'de.
- [ ] **F1-09** CMDB sağlık panosu: tamlık, doğruluk, uyum, kapsam — _Planlandı_
  - Not: Beklemede: zemin hazır (metadata/health_rules.yaml + evren_core.health puanlama, testli). Pano, veri hattı (alım geçidi, bağlayıcılar, eskime, bağımlı CI) kurulduktan sonra; ölçecek gerçek veri yolu olmadan erken.
- [ ] **F1-10** Rol–yetki matrisi ekranı (RBAC + ABAC) — _Devam ediyor_
  - Not: 27.11.2026 (ADR-0034, U8.3, göç 0030): rol–yetki matrisi ekranı, kişiler ve gruplar, panelden rol verme ve geri alma (gerekçe zorunlu, yetkili rol süreli, makine rolü kişiye verilmez, kendine rol yok, görevler ayrılığı önceden), Evren'de kişi açma, Entra kimliği bağlama, yetki geçmişi; veritabanı rolleri her istekte okunur. Kalan: ABAC koşul değerlendirmesi (koşullu izinler) ve süreç sahiplerinin matris onayı.
  - Sizden gereken: Süreç sahipleri ve iç denetimin rol–yetki matrisi onayı
- [x] **F1-11** Şema tasarımcısı ve değişiklik setleri
  - Not: Şema ve Değişiklikler sayfası: 7 işlem (sınıf/alan ekle, alan değiştir, kullanımdan kaldır, seçim değeri, veri kaynağı ekle (gölge), yaşam döngüsü); taslak → onaya gönderme (doğrulama + etki analizi) → onay (hazırlayan onaylayamaz: API + kısıt + tetikleyici) → yayın = metadata/changes/ dosyası (ADR-0015). Alan silme ve fiziksel kolona taşıma sonraki sürümde.
- [ ] **F1-12** ServiceNow'da elle yapılan değişikliklerin çekilmesi — _Planlandı_
- [x] **F1-13** Ürün değişimi tatbikatı: ikinci bir keşif kaynağını gölge modda çalıştırıp kapsam ve değer farkını raporlama
  - Not: Sentetik tatbikat (tools/build_drill.py, Veri Kaynakları sayfası): BMC → Keşif-B gölge → etkin/devir → emekli; 9 denetim geçti, kural dosyaları değişmedi. Gerçek veritabanında rereconcile_source ile aynı geçişler testli. Bulgular: eşleme kapsamı farkı, adlandırma farkı. Gerçek ikinci ürün geldiğinde tatbikat onunla tekrarlanır.
- [x] **F1-14** Ham yük arşivi ve arşivden yeniden işleme (saklama politikasıyla)
  - Not: 26.11.2026 (ADR-0024, U5.4): çalıştırıcı tarafında şifreli parça dosyaları (Fernet, anahtar döndürme), veri içermeyen manifest ve özet denetimi, göreli raw_ref, saklama süresiyle günlük temizlik, connector_run --from-archive ile kaynaksız yeniden işleme; yığın duman testinde sınandı. S3 yazıcısı ve saklama süresinin kurum onayı U10.
- [ ] **F1-15** Gözlemlenebilirlik: servis metrikleri, Kafka tüketici gecikmesi, uyarılar (OpenTelemetry) — _Planlandı_
- [ ] **F1-16** Denetim ve güvenlik olaylarının kurum SIEM'ine aktarımı — _Planlandı_
- [x] **F1-17** Sahiplik ve sertifikasyon: CI sahibinin periyodik onayı, sahipsiz CI raporu
  - Not: 26.11.2026 (ADR-0027, U6.4, göç 0025): metadata/attestation.yaml (üretim 180 gün, diğer 365 gün; alt sınıflara kalıtılır, emekli CI kapsam dışı); attest izni (CI sahibi rolü) + oturumun sys_user eşlemesi (Entra'da yalnızca entra_oid) + owned_by/managed_by; ingest.attestation_request → uzlaştırıcı yeniden denetler, Manual kaydına yazar (neden attestation); ret neden+gerekçe ister ve Veri sorumlusuna anomali açar, sonraki onay kapatır; süre dolumu ve sahip değişikliği Not Yet Reviewed yapar; Sahiplik Onayı sayfası (Bende/toplu onay, Onay bekleyen, Reddedilen, Sahipsiz), CI panelinde bölüm; GRC kontrolü CMDB-06. Entra kullanıcılarının entra_oid eşitlemesi U8.
- [ ] **F1-18** Uyum denetimi (beklenen–gerçek): metadata ile tanımlı kurallar, ör. üretim sunucusunun destek grubu, izlemesi ve korunması — _Planlandı_
- [x] **F1-19** Zaman içinde sorgu: CI'ın geçmiş bir andaki hâli ve değişiklik zaman çizelgesi
  - Not: 26.11.2026 (ADR-0031, U7.4, göç 0028): CI'ın geçmiş bir andaki öznitelikleri ve ilişkileri değişim geçmişi bugünden geri sarılarak çıkarılır (evren_core/timeline.py; ikinci kopya tutulmaz); bugünle farkı, sonradan açılan/kalkan ilişkiler; CI o anda yoksa açıkça söylenir. Zaman çizelgesi aynı çalışmanın veya elle isteğin değişikliklerini tek an olarak gösterir. Bunun için ilişki yaşam döngüsü eklendi: ilişkiyi bildiren kaynak kaydı tutulur (cmdb_rel_ci_source), tam ve sağlıklı çalışmada bildirilmeyen ilişki kalkar, açılma/kalkma değişim geçmişindedir. İlişki haritası seçilen ana göre çizilir.
- [ ] **F1-20** Ölçek ve yük testi: 40.000 sunucu + 100.000 ağ cihazı hacmiyle alım, uzlaştırma ve arama — _Planlandı_
- [x] **F1-21** Elle girdi: arayüzden CI oluşturma, ilişki ekleme/kaldırma, CSV ile toplu içe aktarma — kimlik çözümlemesi ve uzlaştırmadan geçer
  - Not: 26.11.2026 (ADR-0026, U6.1, göç 0022): arayüzden CI oluşturma tamam — istek ingest.manual_create'e, uzlaştırıcı kimlik çözümlemesinden geçirir (kesin eşleşme açılmaz, belirsiz eşleşme veya aynı ad gerekçeli karar ister), açılan CI'ın kimlik anahtarları dizine girer, keşif sonradan bağlanır. U6.2 elle ilişki tamam (göç 0023): izinli ilişki kuralı zorunlu, ilişki CI'ın Manual kaydında ve cmdb_rel_ci'da Manual kaynağıyla, yalnızca elle girilmiş ilişki kaldırılır, iki uçta değişim geçmişi. U6.3 CSV ile toplu içe aktarma tamam (göç 0024): önizleme yazmaz, onay dosyayı yeniden doğrular, geçerli satırlar toplu iş altında oluşturma isteği olur ve sırayla kimlik çözümlemesinden geçer; güncelleme yok, formül enjeksiyonu reddedilir, satır sonuçları izlenir.
- [ ] **F1-22** Değişiklik setiyle ilişki tipi ve izinli ilişki kuralı yönetimi (etki yönü dahil; taslak → onay → yayın) — _Planlandı_
- [ ] **F1-23** Bağımlı CI kapsamının genişletilmesi: keşif kaynağının yazılım ve dosya sistemi verisi, Kubernetes bileşenleri; ana CI ayrılınca bağımlıların taşınması — _Planlandı_
- [ ] **F1-24** Yönetişim görevi altyapısı: görev modeli, sorumluya atama (CI sahibi, yöneten, destek grubu), son tarih, hatırlatma ve tırmanma; kanal soyutlaması: Evren içi kanal ve ServiceNow kanalı (standart görev tablosuna Table API ile açma, correlation_id ile tekillik, durum eşitleme; SN'de kural, ACL veya alan değişikliği yok) — _Planlandı_
  - Sizden gereken: ServiceNow'da görevin açılacağı tablo ve atama grubu kararı; o tabloya yazma izni olan entegrasyon hesabı (canlı kabul U10)
- [ ] **F1-25** Zamanlanmış yönetişim kampanyaları yönetim panelinden: veri güncelliği teyidi (sahiplik onayına bağlı), erişim gözden geçirme, serbest kontrol listesi; kapsam (sınıf ve koşul), sıklık, sorumlu alanı, önizleme (kaç görev, kime), etkinleştirme; sonuç ve kanıt (GRC Platformu'na) — _Planlandı_
  - Sizden gereken: Konfigürasyon yöneticisi rolünün sahibi ve kampanya takvimi (süreç sahipleri)
- [ ] **F1-26** Kural tabanlı bulgu görevleri: veri kalitesi ve uyum kuralları (ör. hiçbir uygulama veya sunucuyla ilişkisi olmayan veritabanı, sahipsiz üretim CI'ı) bulgu üretir, bulgu sorumlusuna görev açar; bulgu sürerken mükerrer görev açılmaz, bulgu kapanınca görev kapanır; kural tanımı panelden — _Planlandı_
- [ ] **F1-27** Kullanım kılavuzu içeriğinin son hâli: ürün oturduktan sonra her konunun ekranlarla doğrulanması, ekran görüntüleri (hedef ortamla), taslak notunun kaldırılması — _Planlandı_
  - Not: 27.11.2026 proje sahibi kararı: altyapı U8.2'de hazır (ADR-0033); içerik ekranlar değişebileceği için en sonda yazılır.

**Çıkış kriterleri**

- [ ] Beş kaynak üretimde; her biri için devre kesici taban çizgisi oluştu
- [ ] Devre kesici tatbikatı yapıldı (kısmi çalışma hiçbir CI'ı emekliye ayırmadı)
- [ ] Sağlık metrikleri haftalık raporlanıyor
- [ ] Rol–yetki matrisi süreç sahipleri ve iç denetim tarafından onaylandı
- [ ] Ürün değişimi tatbikatında kurallar dosyası değişmeden yeni kaynak gölge moddan etkin moda alındı

**Riskler**

- Kaynak API hız sınırları
- NNMi API sürüm farkları
- Yönetişim alanlarının sahipliği konusunda organizasyonel uzlaşı

## F2 · Evren ana kaynak, ServiceNow replika

_Ay 6–9 · Haz – Ağu 2027 (öneri)_ · 0/11 adım

Evren ana kaynak olur. ServiceNow CMDB'yi IRE API üzerinden besler. Task kayıtları ve business rule'lar değişmeden çalışmaya devam eder.

- [ ] **F2-01** sn-sync servisi; sınıf ve alan kapsamı — _Planlandı_
  - Sizden gereken: ServiceNow tarafında Evren veri kaynağı için platform ekibi onayı
- [ ] **F2-02** ServiceNow tarafında "Evren" veri kaynağı ve uzlaştırma kuralı — _Planlandı_
- [ ] **F2-03** ServiceNow'a doğrudan yazan eski entegrasyonların kapatılması — _Planlandı_
- [ ] **F2-04** ITOM abonelik birimi sayımının ServiceNow ile teyidi — _Planlandı_
  - Sizden gereken: ServiceNow hesap yöneticisiyle abonelik birimi görüşmesi
- [ ] **F2-05** ServiceNow uyumlu API: /api/now/table ve /identifyreconcile — _Planlandı_
- [ ] **F2-06** Olay akışı ve imzalı webhook — _Planlandı_
- [ ] **F2-07** Evren MCP sunucusu ve kurum MCP uygulaması sözleşmesi — _Planlandı_
- [ ] **F2-08** GRC Platformu (tedarik) entegrasyonu: CMDB modülünün Evren'den beslenmesi ve CMDB tabanlı otomatik kontroller — _Planlandı_
  - Sizden gereken: GRC Platformu'nda CMDB modülünün Evren bağlantısıyla yapılandırılması
- [ ] **F2-09** Yedekleme, felaket kurtarma ve yüksek erişilebilirlik: PostgreSQL PITR, Kafka/OpenSearch çoğaltma, geri yükleme tatbikatı — _Planlandı_
- [ ] **F2-10** Değişim geçmişi saklama ve bölüm yönetimi: aylık bölümlerin otomatik açılması, saklama süresi kararı (denetim ve KVKK), eski bölümlerin arşivlenmesi; zaman içinde sorgunun ufku — _Planlandı_
- [ ] **F2-11** Görev kanalının ServiceNow'dan Evren'e devri: Evren görev ekranı (Görevlerim), e-posta ve Teams bildirimi, kanal başına gölge → etkin → emekli (ADR-0009 deseni); açık görevlerin taşınması — _Planlandı_

**Çıkış kriterleri**

- [ ] 30 gün kesintisiz eşleme
- [ ] Kapsamdaki sınıflarda ServiceNow CMDB ile Evren arasındaki fark %0,1'in altında
- [ ] ServiceNow'da hiçbir business rule, ACL veya atama kuralı değişmedi
- [ ] Geri dönüş planı test edildi

**Riskler**

- ServiceNow IRE API kapasitesi
- Lisans sayımı belirsizliği
- Geçiş döneminde çift yönetim alışkanlıkları

## F3 · CI seçici ve yapay zekâ

_Ay 9–12 · Eyl – Kas 2027 (öneri)_ · 0/5 adım

ServiceNow formlarında CI seçimi Evren aramasıyla yapılır. Yapay zekâ önerileri insan onayıyla, ISO/IEC 42001 uyumlu biçimde devreye alınır.

- [ ] **F3-01** ServiceNow formlarında Evren araması (MID Server / API ağ geçidi) — _Planlandı_
  - Sizden gereken: MID Server veya API ağ geçidi kapasitesi
- [ ] **F3-02** Doğal dil ile CI sorgulama — _Planlandı_
  - Sizden gereken: Onaylı yapay zekâ modeli ve veri yerleşimi politikası
- [ ] **F3-03** Eşleşme açıklama ve öneri asistanı — _Planlandı_
- [ ] **F3-04** Anomali açıklamaları ve kök neden ipuçları — _Planlandı_
- [ ] **F3-05** Yapay zekâ etki değerlendirmesi ve ISO/IEC 42001 kontrol eşlemesi — _Planlandı_

**Çıkış kriterleri**

- [ ] CI seçici için kullanıcı kabul testi imzalandı
- [ ] İnsan onayı olmadan yapay zekâ kaynaklı hiçbir yazma yok (denetim izinden doğrulanır)
- [ ] Öneri kabul oranı ve hata oranı ölçülüyor

**Riskler**

- Model seçimi ve veri yerleşimi kararı
- SN tarafında MID Server kapasitesi

## F4 · Task tarafına hazırlık

_Ay 12+ · 2028 (öneri)_ · 0/3 adım

Aynı kimlik motoruyla olay–CI bağlama. ServiceNow task desenine uygun veri modeli ve Incident/Change pilotunun tasarımı.

- [ ] **F4-01** Olay → CI bağlama (event management) için kimlik motorunun kullanımı — _Planlandı_
- [ ] **F4-02** Task veri modeli tasarımı (ServiceNow task deseni) — _Planlandı_
- [ ] **F4-03** Incident/Change pilot kapsamı ve ServiceNow lisans çıkış planı — _Planlandı_

**Çıkış kriterleri**

- [ ] Pilot kapsamı ve başarı ölçütleri yönetim tarafından onaylandı

**Riskler**

- Task tarafının iş akışı karmaşıklığı (SLA, onay, bildirim)

## İlkeler

- Önce gölge mod: ServiceNow'a yazmadan önce ölç, karşılaştır, kanıtla.
- Kurallar ürüne değil kaynak rolüne bağlıdır: keşif ürünü değişirse yalnızca bağlayıcı, eşleme ve kaynak kaydı değişir (ADR-0009).
- Golden record'a tek yazan uzlaştırıcıdır; elle düzenleme de kurallardan geçer.
- Her faz bir çıkış kriteriyle kapanır; kriter karşılanmadan sonraki faza geçilmez.
- Güvenlik kapıları her değişiklikte çalışır; yüksek/kritik bulgu ile yayın yapılmaz.
