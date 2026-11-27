# Raporlar ve kontroller

## Gölge mod raporu

[Gölge Mod Raporu](#/golge-mod), mevcut CMDB (ServiceNow) ile Evren'in gözlemlerini karşılaştırır: keşfin görmediği kayıtlar, CMDB'de olmayan keşif kayıtları, olası mükerrerler. Rapor değiştirilemez kanıt olarak kaydedilebilir.

## GRC kontrolleri

[GRC Kontrolleri](#/kontroller) GRC Platformu'na giden CMDB kontrollerinin sonucudur. Bir kaynağın verisi eksik toplandıysa sonuç "kesin değil" olur; bu bilerek böyledir.

## Yetkisiz değişiklikler

Onaylı bir değişiklik kaydı olmadan yapılan değişiklikler CI listesinde **Yetkisiz değişiklik** kapsamında görünür. Veri sorumlusu inceleyip kapatır veya **Yanlış alarm…** olarak işaretler.

## Teknik ayrıntı

- [Gölge mod raporu (karar kaydı)](belge:adr/0014-golge-mod-raporu-ve-kanit)
- [Yetkisiz değişiklik tespiti (karar kaydı)](belge:adr/0025-yetkisiz-degisiklik-tespiti)
