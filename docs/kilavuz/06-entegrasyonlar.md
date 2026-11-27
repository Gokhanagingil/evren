# Veri kaynakları ve entegrasyonlar

Evren'e veri bağlayıcılarla gelir. Her kaynak bir **rolü** doldurur (keşif, sanallaştırma, ağ, izleme, güvenlik, ITSM). Kurallar ürüne değil role bağlıdır: araç değişse de kurallar değişmez.

## Bir kaynağı çalıştırmak

1. [Veri Kaynakları](#/kaynaklar) sayfasında kaynağı seçin.
2. **Şimdi çalıştır** ile tam liste veya yalnızca değişenler için çalışma başlatın. Zamanlanmış çalışmalar da burada görünür.

## Bağlayıcıya kimlik vermek

Bağlayıcı Evren'e yalnızca kendi kaynağı adına yazabilen bir belirteçle bağlanır.

1. Kaynağın panelinde **Kimlik ver** deyin.
2. Belirteç bir kez gösterilir. Kurum sır kasasına koyun ve **Sır kasasına koydum, kapat** deyin.
3. Gerektiğinde **İptal et** ile hemen geçersiz kılın.

## Yeni bir kaynak bağlamak

Yeni kaynak, veri modeli gibi değişiklik setiyle eklenir: [Yönetim Paneli](#/yonetim)'nde **Yeni veri kaynağı ekle**. Yeni ürün önce **gölge** modda çalışır; verisi raporlanır ama kayıtlara yazılmaz. Sonuç tatmin edici olunca **etkin** yapılır.

> **Devre kesici:** Bir çalışma eksik kalırsa veya kayıt sayısı birden düşerse Evren o çalışmada hiçbir şeyi emekliye ayırmaz ve hiçbir ilişkiyi kaldırmaz.

## Teknik ayrıntı

- [Kaynaklar](belge:04-kaynaklar)
- [Kaynak rolleri (karar kaydı)](belge:adr/0009-kaynak-rolleri-urun-bagimsizligi)
- [API ve entegrasyon sözleşmesi](belge:08-api-ve-entegrasyon)
