# 12 · Operasyon ve ölçek

## Hacim tahmini (40.000 sunucu + 100.000 ağ cihazı)

| Veri | Tahmin | Not |
| --- | --- | --- |
| Ana CI (sunucu + ağ cihazı + VM örneği) | ~180.000 | VM'ler ayrı CI |
| Ağ adaptörü + IP CI | 0,5–1 M | Sunucu adaptörleri ve IP'ler |
| Switch portu | 2–4 M | Başlangıçta CI değil (docs/02) |
| Yazılım kurulumu | 8–10 M | `cmdb_sam_sw_install`, hash bölümlü |
| İlişki | 10–20 M | |
| Günlük kaynak gözlemi | 5–10 M | Büyük kısmı değişmez → süzgeç |
| Günlük uzlaştırmaya giren | 50.000–500.000 | %1–5 |
| Denetim satırı | ~100 M/yıl | Aylık bölüm |

## Başlangıç kapasitesi (öneri)

Üretim öncesi yük testiyle doğrulanacak.

| Bileşen | Düğüm | Kaynak |
| --- | --- | --- |
| PostgreSQL | 1 birincil + 1 senkron yedek (+ okuma replikası) | 16–32 vCPU, 128 GB RAM, NVMe, ≥ 2 TB |
| Kafka | 3 broker (KRaft) | 8 vCPU, 32 GB, 1 TB |
| OpenSearch | 3 düğüm | 8 vCPU, 32 GB, 1 TB |
| Valkey | 1 + yedek | 2 vCPU, 8 GB |
| Uygulama servisleri | api ×2, reconciler ×2–4, gateway ×2, sn-sync ×1, mcp ×1 | Yatay ölçeklenir |

## Yüksek erişilebilirlik ve felaket kurtarma

- PostgreSQL: senkron yedek ve otomatik yük devretme (kurum standardı: Patroni veya eşdeğeri). Şifreli PITR yedekleri; aylık geri yükleme tatbikatı.
- Kafka: 3 replika, `min.insync.replicas=2`.
- OpenSearch kayıt sistemi değildir; kaybedilirse golden record'dan yeniden dizinlenir.
- RPO ve RTO iş etki analiziyle belirlenir (ISO 22301). Başlangıç önerisi: RPO 15 dk, RTO 4 saat. CMDB çökerse ServiceNow replika çalışmaya devam eder.

## Gözlemlenebilirlik

- **Metrikler (OpenTelemetry):**
  - Kaynak başına kayıt, değişim ve hata.
  - Devre kesici durumu.
  - Uzlaştırma gecikmesi ve kuyruk derinliği.
  - Eşleştirme kuyruğu yaşı.
  - sn-sync bekleyen ve hata sayısı.
  - API gecikmesi (p95).
- **Loglar:** yapılandırılmış JSON, korelasyon kimliği; sır ve kişisel veri yok.
- **Alarmlar:**
  - Devre kesici OPEN.
  - Eşleştirme kuyruğu 3 günden eski.
  - sn-sync hata oranı.
  - Denetim tetikleyicisi ihlal denemesi.
  - Break-glass kullanımı.
- Evren kendi altyapısını kendi CMDB'sinde CI olarak tutar.

## Dağıtım

- Hedef ortam açık soru: Kubernetes/OpenShift mi, VM üzerinde Docker mı? İki durumda da:
  - imaj imzalı ve taranmış;
  - root olmayan kullanıcı, salt okunur kök dosya sistemi;
  - sırlar dosya olarak bağlanır;
  - ağ politikaları servis bazında tanımlanır.
- Migrasyonlar sürüm kontrollü ve geri alınabilir yazılır. Yıkıcı değişiklikler iki adımda yapılır: önce genişlet, sonra daralt.
- Ortamlar: geliştirme → test → ön üretim → üretim. Ön üretimde anonimleştirilmiş veri kullanılır.

## Yük testi (F0 sonu)

Sentetik 5 milyon kaynak kaydı ve 3 milyon CI ile ölçülecekler:

- İlk yükleme süresi.
- Günlük artımlı işleme süresi.
- Arama gecikmesi.
- Etki analizi gecikmesi (derinlik 4).

Sonuçlar `DURUM.md`'ye kaydedilir.
