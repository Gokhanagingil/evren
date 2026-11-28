# Veri kaynakları ve entegrasyonlar

Evren'e veri bağlayıcılarla gelir. Her kaynak bir **rolü** doldurur (keşif, sanallaştırma, ağ, izleme, güvenlik, ITSM). Kurallar ürüne değil role bağlıdır: araç değişse de kurallar değişmez.

## Yeni entegrasyon kurmak

> Taslak. Ayrıntılar en sonda tamamlanacak (F1-27).

[Yönetim Paneli](#/yonetim)'nde **Yeni entegrasyon kur** sihirbazı adım adım ilerler: kaynak türü, bağlantı, zamanlama, geçit kimliği, kurulum paketi ve doğrulama. Sır (parola, belirteç) hiçbir adımda yazılmaz; yalnızca kurum sır kasasındaki değerin çalıştırıcıya bağlanacağı dosya yolu verilir. Sihirbazın ürettiği blok çalıştırıcının örnek dosyasına eklenir; çalıştırıcı örneği bildirince deneme çalışmasıyla doğrulanır.

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

## ServiceNow replikası

Evren ana kaynaktır; ServiceNow CMDB'si Evren'in kopyasıdır. [ServiceNow Replikası](#/replika) sayfasında replika önce **gölge** modda çalışır: ServiceNow'a yazılmaz, kayıtların nasıl tanınacağı denenir. Çelişki yoksa **etkin** moda geçilir. ServiceNow bir kaydı beklenenden başka bir kayıtla eşleştirirse yazılmaz; **Karar bekleyenler** listesinde kabul edin, dışarıda tutun veya yeniden deneyin. Çok sayıda değişiklik birden gidecekse Evren onayınızı bekler.

## Yapay zekâ ajanları (MCP)

Kurumun yapay zekâ ajanları Evren'i yalnızca sizin yetkinizle sorgular; Evren'e yazamaz. Bir düzeltme önerirse öneri [Öneriler](#/oneriler) sayfasına düşer. Gerekçesini okuyun, kabul edin veya reddedin. Kendi önerinize karar veremezsiniz.

## ServiceNow'u okuyan ve ServiceNow'a yazan eski araçlar

ServiceNow CMDB'sini Table API ile okuyan betikler Evren'i aynı biçimle okuyabilir. Betiğin adresi ve kimliği değişir; sorgu ve yanıt aynı kalır. Betik yalnızca kendi kimliğine verilen yetkiyle görür. ServiceNow'a IRE ile veri yazan araçlar da aynı yükü Evren'e gönderebilir; araç önce bir veri kaynağı olarak tanımlanır ve verisi diğer kaynaklar gibi uzlaştırmadan geçer.

## Teknik ayrıntı

- [Kaynaklar](belge:04-kaynaklar)
- [Kaynak rolleri (karar kaydı)](belge:adr/0009-kaynak-rolleri-urun-bagimsizligi)
- [API ve entegrasyon sözleşmesi](belge:08-api-ve-entegrasyon)
- [ServiceNow uyumlu Table API (karar kaydı)](belge:adr/0044-servicenow-uyumlu-table-api)
- [IRE uyumlu alım ucu (karar kaydı)](belge:adr/0045-ire-uyumlu-alim-ucu)
