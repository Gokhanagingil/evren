# 03 · Kimlik çözümleme ve uzlaştırma

Kod: `evren_core.normalize`, `evren_core.identity`, `evren_core.reconcile`, `evren_core.circuit_breaker`.
Kurallar: `metadata/identification_rules.yaml`, `metadata/reconciliation_rules.yaml`.

## 1. Normalizasyon (eşleştirme anahtarları)

| Tanımlayıcı | Kural | Örnek |
| --- | --- | --- |
| Hostname | NetBIOS öneki ve `@realm` atılır; ayraçlar `-`; Türkçe karakterler katlanır; sondaki `(…)` atılır | `abc_1`, `abc-1`, `abc_1@local`, `CORP\ABC_1` → `abc-1` |
| Ayraçsız ad | Yalnızca aday bulmak için; tek başına otomatik birleştirme nedeni değildir | `abc1` |
| Seri numarası | Büyük harf, boşluksuz; çöp değerler reddedilir | `To be filled by O.E.M.` → yok |
| VMware seri | `VMware-42 1a…` → BIOS UUID türetilir | VM ↔ misafir sunucu bağı |
| UUID | Biçim; sıfır ve bilinen fabrika UUID'leri reddedilir; **bayt sırası çevrilmiş** biçim de denenir | SMBIOS 2.6 öncesi farkı |
| MAC | Tek biçim; multicast reddedilir; HSRP/VRRP, VPN ve yerel yönetimli MAC **zayıf** | Paylaşılan sanal MAC iki yönlendiriciyi birleştirmez |
| IP | Loopback, link-local, multicast reddedilir; IP zayıf kanıttır | DHCP, NAT |
| Sınıf anahtarı (`attr:<alan>`) | Kurum genelinde tekil sınıf özniteliği: bulut hesap kimliği, küme kimliği, alt ağ. Küçük harf; alt ağ CIDR'ye çevrilir | `10.1.2.5/24` → `subnet=10.1.2.0/24` |

**Türkçe tuzağı:** Python'da `"İ".lower()` iki karakter döndürür, JavaScript'te `toLowerCase('tr')` ise `I`'yı `ı` yapar. Kimlik anahtarlarında yerel ayardan bağımsız `ascii_fold` kullanılır.

## 2. Karar sırası

1. **Yapışkan bağlantı.** Kaynak kaydı daha önce bir CI'ya bağlandıysa bağ korunur. Güçlü çelişki varsa (UUID/seri/OS değişti, ör. aynı adla yeniden kurulan VM) kayıt incelemeye alınır.
2. **Tanımlama kuralları (IRE semantiği).** Öncelik sırasıyla denenir:

   | Öncelik | Öznitelik | Not |
   | --- | --- | --- |
   | 100 | seri + tip | `cmdb_serial_number` |
   | 150 | BIOS UUID | Evren uzantısı |
   | 200 | seri | |
   | 280 | FQDN | Evren uzantısı |
   | 300 | ad | |
   | 400 | MAC + ad | |

   - Bu tablo donanım kuralıdır. Sınıfa göre kurallar (ADR-0019):

     | Sınıf | Kural |
     | --- | --- |
     | VM örneği | korelasyon kimliği; vCenter UUID + nesne kimliği |
     | Bulut hizmet hesabı | hesap kimliği (`account_id`); korelasyon kimliği |
     | Küme (Kubernetes dahil) | küme kimliği (`cluster_id`); korelasyon kimliği. Ad tek başına yetmez |
     | IP ağı | alt ağ (CIDR) |
     | Konteyner | korelasyon kimliği (çalışma zamanı kimliği) |
     | Servis, iş uygulaması, iş yeteneği, iş süreci, bilgi nesnesi | korelasyon kimliği, sonra ad (sınıf ailesi içinde) |
     | **Bağımlı:** uygulama, ağ adaptörü, Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu | üst CI bağlamında (ör. küme + ad alanı + ad; bilgisayar + bağlama noktası) |

   - Öznitelik kayıtta yoksa girdi atlanır.
   - Tek eşleşme varsa çelişki denetimine geçilir.
   - Birden fazla eşleşme "CMDB'de olası mükerrer" olarak incelemeye düşer.
3. **Çelişki denetimi.** Seri, UUID, işletim sistemi ailesi, sınıf anahtarı (aynı alanda farklı değer: iki ayrı küme kimliği), sınıf (kardeş sınıflar) veya iki gerçek alan adındaki FQDN çelişiyorsa kural eşleşse bile otomatik birleştirme yapılmaz. ServiceNow IRE'den temel farkımız budur.
4. **Olasılıksal skor.** Kurallar sonuç vermezse aday dizininden gelen CI'lar skorlanır (log-odds toplamı → lojistik):

   | Kanıt | Ağırlık |
   | --- | --- |
   | UUID / harici kimlik / korelasyon | +9 |
   | Sınıf anahtarı (hesap, küme kimliği, alt ağ) | +8 |
   | VMware seri | +8 |
   | Seri | +7 |
   | FQDN | +5 |
   | Kanonik ad | +4,5 |
   | Ayraçsız ad | +2,6 |
   | Benzer ad (Jaro-Winkler ≥ 0,93) | +1 |
   | Ortak güçlü MAC | +3, her ek MAC +1 (en fazla +5) |
   | IP | +1 |
   | Seri çelişkisi | −4 |
   | UUID çelişkisi | −5 |
   | Sınıf anahtarı çelişkisi | −5 |
   | OS çelişkisi | −3 |
   | Sınıf çelişkisi | −3 |
   | FQDN çelişkisi | −2,5 |
   | **Yalnızca numarada farklı ad (`srv-01`/`srv-02`)** | −3,5 |
   | Belirgin ad farkı | −1,5 |

   Başlangıç (prior) −3.
5. **Eşikler.**
   - Otomatik birleştirme: ≥ 0,97 **ve** ikinci adaydan en az 0,10 fark.
   - İnceleme: ≥ 0,35.
   - Altı: yeni CI.
6. **Bağımlı sınıflar** (uygulama, ağ adaptörü, Kubernetes bileşeni, dosya sistemi, küme düğümü, bulut bölgesi, veritabanı kataloğu) yalnızca üst CI bağlamında tanımlanır. Bu uygulanana kadar `insufficient` döner (F1-07, U7). Bağlam öznitelikleri (ad alanı, bağlama noktası, bölge, VLAN) küresel aday dizinine girmez: `default` ad alanı yüzlerce kümede vardır.
7. **Aile.** Adaylar sınıf ailesi içinde aranır. Aile listesinde olmayan dal, `cmdb_ci`'ın ilk alt sınıfıyla kendi ailesini kurar (ADR-0019).

Ağırlıklar başlangıç değeridir. Eşleştirme kuyruğundaki insan kararlarıyla kalibre edilir; hedef ölçüt yanlış birleştirme oranı < %0,5.

## 3. Eşleştirme kuyruğu

- Kaynak kaydı ve aday CI'lar yan yana gösterilir.
- Her kanıt Türkçe açıklanır (`Feature.detail`).
- Kararlar:
  - **Kabul:** bağ kurulur.
  - **Ret + yeni CI:** yeni CI oluşturulur.
  - **Ret + başka aday:** seçilen adayla bağ kurulur.
- Birleştirme geri alınabilir (split, ADR-0011). CI 360° panelinde her kaynak kaydı için **Ayır** eylemi:
  1. API isteği `ingest.split_request`'e yazar (yetki `cmdb_ci/split`, `idempotency_key`).
  2. Uzlaştırıcı bağı sonlandırır (geçmiş kalır), kaydı yeni CI'a (veya seçilen CI'a) `split` yöntemiyle, isteyen kişinin adıyla bağlar.
  3. Asıl CI'ın kimlik profili kalan kaynak kayıtlarından **yeniden kurulur**: ayrılan kaydın seri, UUID, MAC… anahtarları artık bu CI'a aday üretmez.
  4. İki CI yeniden uzlaştırılır. Asıl CI'da önceki kazananı ayrılan kaynak olan ve başka kaynağın bildirmediği değerler temizlenir (denetimde neden `split`); başka kaynak bildiriyorsa onun değeri geçer.
  - Bağ kalıcıdır: kayıt sonraki çalışmalarda asıl CI ile otomatik yeniden birleşmez.
  - Ayrılamayanlar: CI'ın tek kaynak kaydı; ServiceNow ilk yükleme kaydı (CI'ın sys_id'si ondan gelir — diğer kayıtlar ayrılır); gölge/devir kaynağının kaydı (hedef CI seçilmeden, çünkü yeni CI açamaz).
  - Asıl CI'da ayrılan kayıttan türemiş ilişki satırları silinmez; yıkıcı işlem olduğu için eskime kuralına bırakılır (kural 4).

## 4. Uzlaştırma

- Kurallar **kaynak rolüne** yazılır (`Discovery`, `Virtualization`, `Network`…); ürün adı kurala girmez (ADR-0009). Veri kaynağı → rol eşlemesi `metadata/data_sources.yaml`, kod `evren_core.sources.SourceRegistry`.
- (sınıf, öznitelik) için kural varsa yalnızca yetkili rollerdeki kaynaklar yazar. Öncelik numarası küçük olan kazanır; aynı roldeki kaynaklar `role_rank` ile sıralanır.
- Kurallar alt sınıflara kalıtılır. Alt sınıfta aynı öznitelik için kural varsa en özel seviye kazanır; örneğin ağ cihazlarında ağ keşfi rolü (NNMi).
- **Veri tazeleme:** yüksek öncelikli rolün verisi süreyi aştıysa (ör. sanallaştırma 3 gün) sıradaki taze kaynak kabul edilir.
- **Gölge kaynak** (`lifecycle: shadow`) karar vermez; değeri karşılaştırma için `AttributeDecision.shadow` içinde döner. Emekli kaynak yok sayılır.
- **Yetkisiz deneme:** yetkili hiçbir kaynak değer vermediğinde reddedilen değer kaybolmaz; `Reconciler.reconcile_report().rejected` ile raporlanır (CI 360° ekranında görünür).
- Kural yoksa en güncel gözlem kazanır. `null` hiçbir zaman değer silmez.
- **Yönetişim alanları** (destek grubu, sahip, yaşam döngüsü) yalnızca Manual ve geçiş döneminde ITSM rolü (ServiceNow) tarafından yazılır.
- **Keşif sahipli alanlarda Manual en düşük önceliktedir (900):** elle girilen değer yalnızca keşif kaynağı yoksa geçerlidir.
- **Elle düzenleme (ADR-0012):** CI başına tek `Manual` kaynak kaydı (`source_key` = CI sys_id). Arayüzdeki Düzenle penceresi yalnızca değişen alanları gönderir; uzlaştırıcı kayda ekler ve uzlaştırır. Sonuç alan başına: uygulandı, yedek (daha öncelikli kaynak var) veya kaldırıldı. Manual kayıt kimlik anahtarı eklemez.

## 5. Devre kesici ve eskime

- Taban çizgisi son başarılı çalışmaların medyanı ve MAD'ıdır.
- **OPEN** durumuna geçiren koşullar:
  - Tamamlanmamış çalışma.
  - Sıfır kayıt.
  - Hata oranı ≥ %2.
  - Değişim oranı ≥ %35 (eşleme hatası belirtisi).
  - Taban çizgisine göre ≥ %15 düşüş.
- Yeterli geçmiş yoksa **WARN** verilir ve eskime bekletilir.
- Eskime yalnızca `OK` çalışmalarda ve **tüm** yetkili kaynaklar CI'ı görmüyorsa durum önerisi üretir. CI silinmez.

## 6. Yetkisiz değişiklik

Şu durumda anomali kaydı açılır:

- Golden record'da izlenen bir öznitelik değişti (RAM, CPU, OS, seri), **ve**
- CI için o zaman aralığında onaylı bir Change kaydı (`ref.sn_change_request` + `ref.sn_task_ci`) yok.

Bu kayıt GRC Platformu'nda "yetkisiz değişiklik" kontrolüne kanıt olur.
