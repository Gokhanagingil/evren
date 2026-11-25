# 05 · ServiceNow ile geçiş deseni

## Hedef

ServiceNow CMDB'yi bir süre canlı ve eşlenik tutmak. Incident ve Change gibi süreçlerde CI seçiminin önce ServiceNow'dan, sonra Evren aramasıyla yapılabilmesi. Tüm bunların **ServiceNow'da business rule, ACL veya task alanı değiştirmeden** gerçekleşmesi.

## Seçilen desen: Evren ana kaynak, ServiceNow CMDB replika

```
Evren golden record ──► sn-sync ──► SN IRE API ──► SN CMDB (replika) ◄── task.cmdb_ci (değişmez)
        ▲                                               │
        └──── değişiklik çekme (sys_updated_on) ◄───────┘  (geçiş döneminde SN'de elle düzenleme)
```

- `task.cmdb_ci` ve benzeri reference alanları ServiceNow'un `cmdb_ci` tablosunu göstermeye devam eder. Atama kuralları, CI tabanlı onaylar, change conflict ve etki hesaplamaları aynen çalışır.
- SN replikasına yalnızca süreçlerin ihtiyaç duyduğu sınıflar ve alanlar gider (`sync.sn_sync_scope`): sunucu, ağ cihazı, uygulama, servis. Port ve yazılım kurulumu gibi yüksek hacimli kayıtlar gitmez.
- SN tarafında "Evren" bir veri kaynağı olarak tanımlanır. Kapsamdaki alanlarda en yüksek öncelikli uzlaştırma kuralı ona verilir. SN'e doğrudan yazan eski entegrasyonlar (SG bağlayıcıları, import set'ler) kapatılır (F2-03).

## Neden Remote Tables değil?

ServiceNow Remote Tables, CI referanslarının yerine geçmek için uygun değil:

- Dot-walk desteklenmez.
- Dış kaynak için Integration Hub aboneliği gerekir.
- "Full remote" yaklaşımı formların, reference alanların, reference qualifier'ların, UI action'ların ve script'lerin güncellenmesini gerektirir.

Kaçındığımız değişiklik tam olarak budur.

## Fazlar

| Faz | ServiceNow'a yazma | Ne olur |
| --- | --- | --- |
| F0 gölge | **Yok** | SN CMDB aynı sys_id'lerle Evren'e alınır; kaynaklar eşleştirilir; mükerrer ve kapsam raporları çıkar |
| F1 | Yok | Tüm kaynaklar ve uzlaştırma; SN'deki elle değişiklikler çekilir |
| F2 ana kaynak | IRE API ile | Evren SN CMDB'yi besler; eski entegrasyonlar kapanır |
| F3 CI seçici | — | SN formunda Evren araması; seçim replikadaki sys_id'ye çözülür |
| F4 | — | Task tarafı tasarımı; SN lisans çıkış planı |

## sys_id stratejisi

- **SN kökenli CI'lar:** Evren'de aynı sys_id. `sync.sn_ci_map` kaydında iki değer eşittir.
- **Evren'de doğan CI'lar:** IRE API ile gönderilir; ServiceNow'un verdiği sys_id eşleme tablosuna yazılır. Table API ile sys_id'yi önceden belirleyerek ekleme seçeneği F2'de bir PoC ile değerlendirilecek; o zamana kadar eşleme tablosu esastır.

## Geçiş dönemi kuralları

- **Hangi alan nerede yönetilir:** uzlaştırma kuralları belirler. Sahiplik ve destek grubu başlangıçta ServiceNow'da kalabilir ("ServiceNow" kaynağı). Evren arayüzü olgunlaşınca Manual kaynağına geçer.
- **Çift yönetimi önlemek:** bir alanın ana kaynağı Evren'e geçtiğinde, SN formunda o alan salt okunur yapılabilir. Bu bir UI policy veya sözlük ayarıdır, business rule değildir. Proje sahibinin onayıyla yapılır.

## Ağ ve güvenlik

- **Evren → ServiceNow:** giden HTTPS ve OAuth. Ayrı entegrasyon kullanıcısı, en az yetki.
- **ServiceNow → Evren:** CI seçici gibi geri çağrılar SaaS'ta MID Server veya DMZ'deki bir API ağ geçidi gerektirir. Kimlik doğrulama ve IP kısıtlaması uygulanır.

## Açık konular

- ITOM abonelik birimlerinin üçüncü taraf kaynaklı CI'larda nasıl sayıldığı (F2-04).
- ServiceNow IRE API kapasitesi. İlk itme büyük olacağından sınıf sınıf ve gece pencerelerinde yapılır.
