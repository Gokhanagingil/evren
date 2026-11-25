---
description: Değişikliği güvenlik açısından gözden geçir (yayın öncesi)
---
Bu dalın ana dala göre değişikliklerini (`git diff main...HEAD`) şu listeye göre incele. Her bulguyu dosya ve satırıyla raporla.

1. **Sır.** Kod, test, log ve örnek dosyalarda parola, belirteç veya anahtar var mı? `gitleaks` çalıştır.
2. **SQL.** Parametresiz SQL, string birleştirme var mı? Kullanıcıdan gelen alan adı sözlük dışından SQL'e giriyor mu?
3. **Yetki.**
   - Yeni uç noktada rol, ABAC ve alan hassasiyeti kontrolü var mı?
   - Makine hesabı insan işlemleri yapabiliyor mu?
   - Golden record'a uzlaştırıcı dışında yazan bir yol açıldı mı?
4. **Arayüz.** `innerHTML`, satır içi betik veya stil, dış kaynak var mı? CSP ihlali konsolda görünüyor mu?
5. **Veritabanı.** Yeni tablonun `0003_security.sql` yetkileri ve yetki testleri var mı? Denetim izi etkileniyor mu?
6. **Bağımlılık.** Yeni paket hash kilitli mi? ADR'si var mı? `pip-audit` temiz mi?
7. **Yapay zekâ ve MCP.**
   - Yapay zekâ çıktısı doğrudan yazıyor mu?
   - CMDB içeriği istem talimatı gibi işleniyor mu? İstem enjeksiyonuna karşı içerik veri olarak ele alınmalı.
   - Araç yetkileri en az yetki ilkesine uygun mu?
8. **Kişisel veri.** Gereksiz kişisel veri toplanıyor veya loglanıyor mu (KVKK)?
9. **Hata yönetimi.** Hata mesajları iç ayrıntı, yığın izi veya sır sızdırıyor mu?
10. **Tehdit modeli.** `docs/06-guvenlik.md` güncellenmesi gerekiyor mu?
