# Eşleştirme kararları

Evren gelen bir kaydı mevcut bir CI ile eşleştirmekten emin değilse kendisi karar vermez, [Eşleştirme Kuyruğu](#/kuyruk)'na koyar. Örneğin ad aynıdır ama seri numarası farklıdır.

## Hızlıca

1. Kuyrukta bir kayda tıklayın. Kaynak kaydı ve adaylar yan yana açılır; her kanıtın ağırlığı yazar.
2. Karar verin:
   - **Adayla birleştir:** ikisi aynı şey.
   - **Ret: yeni CI aç:** ikisi farklı şeyler.
   - **Kaynakta düzeltilsin:** kaynak hatalı bilgi gönderiyor.
   - **Ertele:** şimdilik karar verilemiyor.
3. Karar uzlaştırıcı tarafından uygulanır.

## Yanlış karar verdiyseniz

**Karar verilenler** sekmesinde **Kararı geri al** kullanılabilir. Uygulanmış yanlış bir birleştirme ise CI panelindeki **Ayır…** ile geri alınır; kaynak kaydı ayrı bir CI'a taşınır.

> **İpucu:** Ad tek başına kanıt değildir. Seri numarası, UUID veya işletim sistemi çelişiyorsa birleştirmeyin.

## Teknik ayrıntı

- [Kimlik çözümleme](belge:03-kimlik-ve-uzlastirma)
- [Birleştirmeyi ayırma (karar kaydı)](belge:adr/0011-birlestirmeyi-ayirma)
