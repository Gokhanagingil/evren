# Sahiplik onayı ve eskime

Bir kaydın doğru kalması için iki düzenli iş vardır: sahibinin kaydı onaylaması ve artık görülmeyen kayıtların emekliye ayrılması.

## Sahiplik onayı

Sahibi veya yöneticisi olduğunuz CI'lar belirli aralıklarla onay ister (üretimdekiler 180 günde, diğerleri 365 günde bir).

1. [Sahiplik Onayı](#/sahiplik-onayi) sayfasında **Bende** görünümünü açın.
2. Doğru olanları işaretleyip **Seçilenleri onayla** deyin.
3. Size ait değilse veya bilgi yanlışsa **Reddet…** ile neden yazın; Veri sorumlusu ele alır.

## Eskime

Keşif, sanallaştırma, ağ, izleme ve güvenlik araçlarının hiçbiri bir CI'ı uzun süredir görmüyorsa Evren onu önce **eskimiş**, sonra **emekliye ayırma adayı** olarak işaretler. Kaynakların son çalışmaları sağlıklı değilse öneri ilerlemez.

1. [Eskime](#/eskime) sayfasında adaylara bakın.
2. **Emekliye ayır…** ya da bilerek kapalıysa **Koru…** seçin; gerekçe zorunludur.

Emekliye ayrılan CI silinmez. Yeniden görülürse işaretlenir ve geri alınabilir.

## Teknik ayrıntı

- [Sahiplik onayı (karar kaydı)](belge:adr/0027-sahiplik-onayi)
- [Eskime işletimi (karar kaydı)](belge:adr/0028-eskime-isletimi)
