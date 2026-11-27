# Kullanıcılar, roller ve yetkiler

Evren'e kurum hesabınızla (Entra ID) girersiniz. Ne yapabileceğinizi rolleriniz belirler. Rol verilmemiş bir kullanıcı hiçbir şey göremez.

## Roller ne yapar?

| Rol | Ne yapar |
| --- | --- |
| Okuyucu | CI'ları ve ilişkileri okur. |
| CI sahibi | Sorumlu olduğu CI'ları düzenli aralıklarla onaylar. |
| Konfigürasyon analisti | CI ekler, düzeltir, ilişki kurar. |
| Eşleştirme analisti | Eşleştirme kuyruğunu karara bağlar. |
| Veri sorumlusu | Kaynakları ve kuralları yönetir, eskime kararlarını verir. |
| Şema tasarımcısı / onaylayıcı | Veri modeli değişikliğini hazırlar / onaylar. |
| Platform operatörü | Sistem durumunu izler. |
| Denetçi | Her şeyi ve denetim izini salt okunur görür. |
| Yetki yöneticisi | Rol atar. |

## Yetki nasıl verilir?

Roller bugün Entra uygulama rolleri ve grup atamalarıyla verilir. Yönetim Paneli'nden kullanıcı ekleme ve rol verme ekranı hazırlanıyor; geldiğinde bu sayfa güncellenecek.

## Görevler ayrılığı

Bazı roller aynı kişide birleşemez: şema değişikliğini hazırlayan onaylayamaz, denetçi denetlediği veriyi değiştiremez, kimse kendine rol veremez. Bu kurallar veritabanında zorlanır.

## Teknik ayrıntı

- [Yetkilendirme ve rol matrisi](belge:07-yetkilendirme)
- [Güvenlik](belge:06-guvenlik)
