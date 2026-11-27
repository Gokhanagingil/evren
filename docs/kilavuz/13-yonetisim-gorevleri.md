# Yönetişim görevleri

> Taslak. Tek seferlik görev, zamanlanmış kampanyalar, görevleri izleme ve görev kanalı hazır; kurala bağlı görevler hazırlanıyor (plan F1-26). Kılavuz içeriği ekranlar oturduktan sonra tamamlanacak (F1-27).

Evren, verinin güncel kalması için sorumlulara görev açar:

- **Zamanlanmış görevler (kampanyalar):** örneğin uygulama sahiplerine her çeyrek veri güncelliği teyidi veya erişim gözden geçirme.
- **Kural görevleri:** örneğin hiçbir uygulama veya sunucuyla ilişkisi olmayan bir veritabanı bulunduğunda, veritabanının sahibine görev.

Görevler şimdilik ServiceNow'da açılır; ileride Evren'in kendi görev ekranından da yürütülebilir. Tanımlar [yönetim panelinden](kilavuz:yonetim-paneli) yapılır.

## Zamanlanmış kampanyalar

Konfigürasyon yöneticisi **Görevler → Kampanyalar** sekmesinden kampanya tanımlar: ne istenecek, hangi CI'lar için, ne sıklıkla ve kaç günde. Kapsamdaki her CI için sorumlusuna (sahip → yöneten → destek grubu) bir görev açılır; aynı CI için açık görev varsa yenisi açılmaz.

### Koşul örnekleri

| Ne istiyorsunuz? | Sınıf | Koşul |
|---|---|---|
| Üretimdeki bütün uygulamalar | Uygulama | `used_for=Production` |
| Çalışır durumdaki sunucular | Sunucu | `operational_status=1` |
| Adı "ist-" ile başlayan CI'lar | Konfigürasyon Öğesi | `nameSTARTSWITHist-` |

## Teknik ayrıntı

- [Yönetişim görevleri ve kanallar](belge:adr/0035-yonetisim-gorevleri-ve-kanallar)
- [Zamanlanmış yönetişim kampanyaları](belge:adr/0036-zamanlanmis-yonetisim-kampanyalari)
