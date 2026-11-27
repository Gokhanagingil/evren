# Sınıf ve alan eklemek

Veri modeli (sınıflar, alanlar, seçim listeleri) ServiceNow ile uyumludur. Değişiklik doğrudan yapılmaz; **değişiklik seti** ile hazırlanır, başka bir kişi onaylar, sonra yayımlanır.

## Hızlıca

1. [Yönetim Paneli](#/yonetim)'nde **Yeni sınıf ekle** veya **Sınıfa alan ekle**'yi seçin. Veri Modeli sayfasında bir sınıfın içinden **Bu sınıfa alan ekle** de kullanılabilir.
2. Sihirbaz adım adım ilerler: üst sınıf veya sınıf, ad (teknik ad Türkçe addan önerilir), alanlar ve seçim değerleri. Gözden geçirme adımında Evren değişikliği kaydetmeden doğrular; **Oluştur** taslağı kaydeder ve isterseniz onaya gönderir. (Taslak — ayrıntılar en sonda tamamlanacak.)
3. **Onaya gönder**. Evren değişikliği doğrular ve etkisini (kaç CI'ı etkilediğini) gösterir. Şema onaylayıcı **Onayla** veya **Reddet** der. Hazırlayan kişi kendi setini onaylayamaz.
4. Onaylanan set **Yayınla** ile yayımlanır. Yayın, sürüm kontrolüne eklenecek bir değişiklik dosyası üretir; dağıtımda uygulanır.

## Ne değiştirilebilir?

Yeni sınıf, alan ve seçim değeri; alanın değiştirilmesi veya kullanımdan kaldırılması; yeni veri kaynağı ve veri kaynağının yaşam döngüsü (gölge, etkin, devir, emekli). İlişki tipleri bugün yalnızca geliştirme ekibince eklenir.

> **Neden bu kadar adım?** Veri modeli bütün kayıtları etkiler. Dört göz ilkesi ve dosya ile yayın, hatalı bir değişikliğin üretime sessizce girmesini önler.

## Teknik ayrıntı

- [Veri modeli](belge:02-veri-modeli)
- [Değişiklik setleri (karar kaydı)](belge:adr/0015-degisiklik-setleri-git-ile-yayin)
