# CMDB sağlığı ve kalite kuralları

> Taslak. Ekranlar hazır; kılavuz içeriği ekranlar oturduktan sonra tamamlanacak (F1-27).

**CMDB Sağlığı** sayfası CMDB'nin ne kadar eksiksiz, doğru, kurallara uygun ve kapsamlı olduğunu tek puanla ve dört boyutla gösterir: tamlık, doğruluk, uyum, kapsam.

**Kurallar** sekmesinde Konfigürasyon yöneticisi kalite ve uyum kuralı tanımlar. Kural, kapsamdaki CI'lardan hangilerinin **bulgu** olduğunu söyler. Örnek: hiçbir uygulama veya sunucuyla ilişkisi olmayan veritabanı.

- Bulgunun sorumlusuna (sahip → yöneten → destek grubu) tek görev açılır.
- Sorumlu düzeltince bulgu da görev de kendiliğinden kapanır.
- "Geçerli değil" kararı bulguyu süreli istisna yapar.
- "Reddedildi" (sorumlusu ben değilim) kararında CI'ın sahibi düzeltilince görev yeni sorumluya gider.

Kural türleri: ilişkisi olmayan CI, boş alan, bir kaynak rolünce görülmeyen CI, koşula uyan CI, izinli olmayan ilişki. Kural ürüne değil role bağlıdır.

## Teknik ayrıntı

- [Kalite kuralları, bulgular ve sağlık panosu](belge:adr/0037-kalite-kurallari-ve-bulgular)
- [Yönetişim görevleri ve kanallar](belge:adr/0035-yonetisim-gorevleri-ve-kanallar)
