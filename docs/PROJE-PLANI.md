# Evren CMDB — proje planı

> Bu dosya `apps/web/content/roadmap.json` dosyasından üretilir (`python tools/build_plan.py`); elle düzenlemeyin. Aynı plan uygulamadaki **Yol Haritası** sayfasında, mimari ise **Büyük Resim** ve **Mimari Harita** sayfalarındadır.

**Son güncelleme:** 25.11.2026 · **Etkin faz:** F0 — Temel ve gölge mod · **İlerleme:** 9/45 adım tamamlandı

## Nasıl izlenir

- Adımlar faz içinde **yapılış sırasıyla** listelenir. İşaretli kutu tamamlanan adımdır; durum etiketi devam eden, planlanan veya engelli adımı gösterir.
- **Sizden gereken** satırı, adımın başlaması için kurumdan gelmesi gereken karar veya erişimdir. Bunlar zamanında gelmezse ilgili adım bekler; diğer adımlar sürer.
- Bir faz, çıkış kriterlerinin tamamı karşılanınca kapanır.

## Sıradaki adımlar

1. **F0-07** ServiceNow metadata dışa aktarımı; işaretli 18 sınıf/alanın doğrulanması — _Planlandı_  
   Sizden gereken: ServiceNow salt okunur servis hesabı: sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma
1. **F0-09** API servisi, Entra ID oturumu ve Entra grup → rol eşlemesi — _Planlandı_  
   Sizden gereken: Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı
1. **F0-08** ServiceNow CMDB ve referans verilerinin ilk yüklemesi (sys_id korunarak, salt okunur) — _Planlandı_  
   Sizden gereken: ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi
1. **F0-10** Alım geçidi, Kafka ve uzlaştırıcı servisi — _Planlandı_  
   Sizden gereken: Hedef çalışma ortamı kararı: Kubernetes/OpenShift mi, sanal makinede Docker mı
1. **F0-11** vCenter ve Zabbix bağlayıcıları — _Planlandı_  
   Sizden gereken: vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi
1. **F0-12** Eşleştirme kuyruğu ekranı (yan yana karşılaştırma, birleştir/ayır) — _Planlandı_

## Sizden gerekenler (karar ve erişim)

| Adım | Gereken | Durum |
| --- | --- | --- |
| F0-07 | ServiceNow salt okunur servis hesabı: sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma | Planlandı |
| F0-09 | Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı | Planlandı |
| F0-08 | ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi | Planlandı |
| F0-10 | Hedef çalışma ortamı kararı: Kubernetes/OpenShift mi, sanal makinede Docker mı | Planlandı |
| F0-11 | vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi | Planlandı |
| F0-13 | Gölge mod raporunu değerlendirecek CMDB sahibi ve süreç sahipleri | Planlandı |
| F1-01 | BMC Helix Discovery API kullanıcısı; kurulum on-prem mi SaaS mı | Planlandı |
| F1-02 | Entra uygulama izni Machine.Read.All (yalnızca okuma); vekil sunucu çıkış izni | Planlandı |
| F1-03 | NNMi web servis kullanıcısı ve NNMi sürümü | Planlandı |
| F1-04 | ServiceNow change_request ve task_ci okuma izni | Planlandı |
| F1-10 | Süreç sahipleri ve iç denetimin rol–yetki matrisi onayı | Planlandı |
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

_Ay 0–3 · Ara 2026 – Şub 2027 (öneri)_ · 9/16 adım

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
- [ ] **F0-07** ServiceNow metadata dışa aktarımı; işaretli 18 sınıf/alanın doğrulanması — _Planlandı_
  - Sizden gereken: ServiceNow salt okunur servis hesabı: sys_db_object, sys_dictionary, sys_choice, cmdb_identifier okuma
- [ ] **F0-09** API servisi, Entra ID oturumu ve Entra grup → rol eşlemesi — _Planlandı_
  - Sizden gereken: Entra ID uygulama kaydı (kiracı ve istemci kimliği), rol grupları, MFA kimlik doğrulama bağlamı
- [ ] **F0-08** ServiceNow CMDB ve referans verilerinin ilk yüklemesi (sys_id korunarak, salt okunur) — _Planlandı_
  - Not: Eşleme dosyası ve sys_id koruyan ilk yükleme mantığı hazır (connectors/servicenow_cmdb, IdentityEngine.import_existing).
  - Sizden gereken: ServiceNow salt okunur hesap ve Evren ortamından SN'ye ağ erişimi; SaaS/MID Server bilgisi
- [ ] **F0-10** Alım geçidi, Kafka ve uzlaştırıcı servisi — _Planlandı_
  - Sizden gereken: Hedef çalışma ortamı kararı: Kubernetes/OpenShift mi, sanal makinede Docker mı
- [ ] **F0-11** vCenter ve Zabbix bağlayıcıları — _Planlandı_
  - Sizden gereken: vCenter ve Zabbix salt okunur servis hesapları; ağ erişimi
- [ ] **F0-12** Eşleştirme kuyruğu ekranı (yan yana karşılaştırma, birleştir/ayır) — _Planlandı_
  - Not: Önizleme ekranı F0-16 ile geldi; kararların kalıcı kaydı API ve uzlaştırıcıyla.
- [ ] **F0-13** Gölge mod raporları: SN CMDB'deki mükerrerler ve kaynaklar arası kapsam boşlukları — _Planlandı_
  - Sizden gereken: Gölge mod raporunu değerlendirecek CMDB sahibi ve süreç sahipleri

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

_Ay 3–6 · Mar – May 2027 (öneri)_ · 0/13 adım

Beş kaynağın tamamı canlı. Öznitelik düzeyinde uzlaştırma, devre kesici, eskime, kaynak bilgisi ve sağlık ölçümü çalışır.

- [ ] **F1-01** BMC Helix Discovery bağlayıcısı ve alan adlarının kurum örneğinde doğrulanması — _Planlandı_
  - Not: Eşleme hazır (connectors/bmc_discovery); on-prem ve SaaS aynı REST API'yi kullanır.
  - Sizden gereken: BMC Helix Discovery API kullanıcısı; kurulum on-prem mi SaaS mı
- [ ] **F1-02** Defender bağlayıcısı; kısıtlı güvenlik duruşu tablosu — _Planlandı_
  - Sizden gereken: Entra uygulama izni Machine.Read.All (yalnızca okuma); vekil sunucu çıkış izni
- [ ] **F1-03** NNMi: düğüm, arayüz ve L2 bağlantıları — _Planlandı_
  - Sizden gereken: NNMi web servis kullanıcısı ve NNMi sürümü
- [ ] **F1-04** ServiceNow Change bağlayıcısı ve yetkisiz değişiklik tespiti — _Planlandı_
  - Sizden gereken: ServiceNow change_request ve task_ci okuma izni
- [ ] **F1-05** Uzlaştırma kuralları ve "bu değer nereden geldi?" görünümü — _Planlandı_
- [ ] **F1-06** Devre kesici ve eskime işletimi; tatbikat — _Planlandı_
- [ ] **F1-07** Bağımlı CI kimliği (uygulama, ağ adaptörü) — _Planlandı_
- [ ] **F1-08** OpenSearch araması ve CI 360° ekranı — _Planlandı_
- [ ] **F1-09** CMDB sağlık panosu: tamlık, doğruluk, uyum, kapsam — _Planlandı_
- [ ] **F1-10** Rol–yetki matrisi ekranı (RBAC + ABAC) — _Planlandı_
  - Sizden gereken: Süreç sahipleri ve iç denetimin rol–yetki matrisi onayı
- [ ] **F1-11** Şema tasarımcısı ve değişiklik setleri — _Planlandı_
- [ ] **F1-12** ServiceNow'da elle yapılan değişikliklerin çekilmesi — _Planlandı_
- [ ] **F1-13** Ürün değişimi tatbikatı: ikinci bir keşif kaynağını gölge modda çalıştırıp kapsam ve değer farkını raporlama — _Planlandı_

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

_Ay 6–9 · Haz – Ağu 2027 (öneri)_ · 0/8 adım

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
