# CI eklemek ve düzeltmek

Kayıtların çoğu kaynaklardan kendiliğinden gelir. Elle giriş, keşfin göremediği şeyler ve düzeltmeler içindir. Elle girilen her bilgi de aynı kurallardan geçer.

## Tek bir CI eklemek

1. [Yönetim Paneli](#/yonetim/yeni-ci)'nde **Yeni CI ekle**'yi ya da CI sayfasında **Yeni CI…** düğmesini seçin.
2. Sınıfı (ör. Linux sunucu) ve bildiğiniz alanları girin. Seri numarası veya FQDN gibi kimlik alanları eşleşme için önemlidir.
3. **Oluştur**'a basın. Evren önce bu kaydın zaten var olup olmadığına bakar.

Evren benzer bir kayıt bulursa sorar: aynı şey ise var olana bağlayın, değilse gerekçe yazıp yeni CI açın.

## CSV ile toplu içe aktarmak

1. **CSV içe aktar…** ile dosyayı seçin ve **Önizle**'ye basın. Önizleme hiçbir şey yazmaz.
2. Satır satır sonucu inceleyin: açılacak, var olanla eşleşen, karar gereken.
3. **İçe aktar** ile onaylayın. Karar gereken satırlar tek tek açılır.

## Bir değeri düzeltmek

CI panelinde **Düzenle…** ile değiştirin. Elle girilen değer de bir kaynak gibi uzlaştırılır: yönetişim alanlarında (sahip, destek grubu) elle giriş önceliklidir. Keşfin gördüğü teknik alanlarda ise keşif önceliklidir.

## İlişki eklemek

CI panelinde **İlişki ekle…** ile hedef CI'ı ve ilişki tipini seçin. İzin verilmeyen ilişkiler (ör. sunucunun uygulama üzerinde çalışması) reddedilir ve nedeni yazılır.

> **Neden hemen görünmüyor?** İstekleri uzlaştırıcı uygular; genellikle birkaç saniye sürer. Uzlaştırıcı çalışmıyorsa istek kaybolmaz, sırada bekler.

## Teknik ayrıntı

- [Elle girdi (karar kaydı)](belge:adr/0026-elle-girdi)
- [Kimlik çözümleme ve uzlaştırma](belge:03-kimlik-ve-uzlastirma)
