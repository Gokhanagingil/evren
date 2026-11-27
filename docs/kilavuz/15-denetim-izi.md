# Denetim izi

> Taslak. Ekranlar hazır; kılavuz içeriği ekranlar oturduktan sonra tamamlanacak (F1-27).

**Denetim İzi** sayfası Evren'de kimin, ne zaman, neyi, nereden ve hangi sonuçla yaptığını tek yerde gösterir. Yalnızca **Denetçi** görür; Platform yöneticisi kendi denetçisi olamaz.

- **Olaylar:** yönetim ve güvenlik olayları (oturum, yetki reddi, rol verme, şema, entegrasyon, yönetişim), CI öznitelik değişiklikleri ve görev olayları. Dönem, akış, kategori ve sonuçla süzülür; bir olaydan "Bu kişinin olayları", "Bu nesnenin olayları" veya "Aynı istekteki olaylar" ile iz sürülür. CSV veya NDJSON olarak indirilir; dışa aktarmanın kendisi de olaydır.
- **Bütünlük:** her akış saatlik dönemlerde zincirli mühürle kapatılır. Bir kayıt sonradan değiştirilirse, silinirse veya eklenirse doğrulama sorun bulur. Görev çalışanı günde bir son yedi günü kendiliğinden doğrular; Denetçi istediği dönemi elle doğrular.
- **İncelemeler:** Denetçi bir dönemi gözden geçirir, "Bulgu yok" veya "Bulgu var" kararıyla not yazar. Mühür sorunu varken "Bulgu yok" kaydedilmez. İnceleme değiştirilemez.

Bütün olaylar, mühürler ve doğrulamalar olay omurgasıyla kurum SIEM'ine (`evren.audit.v1`) de gider.

## Teknik ayrıntı

- [Denetim izi, mühürler ve SIEM aktarımı](belge:adr/0039-denetim-izi-ve-siem)
- [Güvenlik ve tehdit modeli](belge:06-guvenlik)
