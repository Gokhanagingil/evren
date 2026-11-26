# ADR-0023: Kısıtlı güvenlik duruşu verisinin alım yolu (Defender → `cmdb.ci_security_posture`)

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (F1-02 kapsamı), mimari, güvenlik

## Bağlam

Uç nokta güvenliği kaynağı (bugün Microsoft Defender for Endpoint) her makine için risk puanı, maruziyet düzeyi, sağlık ve koruma durumu bildirir. Bu veri **kısıtlıdır**. Bir saldırgan için hangi sunucunun zayıf olduğunu gösteren bir haritadır. CLAUDE.md kuralı açıktır: kısıtlı veri yalnızca `cmdb.ci_security_posture` tablosunda tutulur.

Tablo, yetki ve roller 0003 göçünden beri vardı (`evren_security_reader`, `evren_auditor`). Ama bağlayıcıdan tabloya bir yol yoktu. Eşleme bu alanları bilerek almıyordu (ADR-0020 §12).

Zor kısım şudur: kaynak kaydı (`ingest.source_record`) API'nin okuyabildiği ve CI 360°'ta gösterilen bir tablodur. Duruş verisi normal öznitelik olarak gelseydi bu tabloya, CI özniteliklerine, arama dizinine ve olaylara sızardı.

## Karar

1. **Zarfta ayrı bölüm:** `IngestRecord.posture`. Alanlar:
   - koruma durumu;
   - sağlık durumu;
   - maruziyet düzeyi (`None`/`Low`/`Medium`/`High`);
   - risk puanı (`None`/`Informational`/`Low`/`Medium`/`High`);
   - kritik ve yüksek zafiyet sayısı;
   - son görülme.

   Bölüm isteğe bağlıdır ve şema sürümü değişmez (geriye uyumlu ek). Eşleme dosyası `posture:` bölümüyle doldurur. Bilinmeyen değer `map` ile boşa düşer, kayıt reddedilmez.

2. **Duruş verisinin yazılmadığı yerler:**
   - `ingest.source_record`: yalnızca öznitelik, lookup, dış kimlik ve ilişki saklanır;
   - CI öznitelikleri ve `audit.ci_change`;
   - arama dizini: dizinleyici tabloyu okuyamaz (0013);
   - outbox ve CI olayları.

   İçerik özetine dahildir. Yalnızca duruş değişirse kayıt yeniden işlenir. Duruşu olmayan kayıtların özeti değişmez, mevcut özetler geçerli kalır.

3. **Yazar uzlaştırıcıdır, yazma yetkisi role bağlıdır (ADR-0009):**
   - Yalnızca `posture_source: true` işaretli rol yazar; bugün bu rol **EndpointSecurity**'dir. Kaynağın yaşam döngüsü etkin veya devrediliyor olmalıdır.
   - Gölgedeki ürün yazmaz. Başka rolden gelen duruş bölümü yok sayılır ve günlüğe yazılır.
   - Satır, kayıt bir CI'a bağlandıktan sonra yazılır; bu bağ kural, skor, ilk yükleme veya kuyruk kararıyla kurulmuş olabilir. Bağı olmayan kayıt (inceleme, yetersiz kimlik) duruş yazmaz. Kuyruk kararından sonraki ilk çalışmada yazılır, çünkü değişmeyen kayıtta da bağlı CI'ın duruşu tazelenir.
   - Aynı CI için iki yazar varsa (devir dönemi) rol içi sırası küçük olan kaynak kazanır.

4. **Okuma yalnızca `restricted` hassasiyet yetkisiyledir:**
   - CI ayrıntısı API yanıtına `security_posture` bölümü yalnızca sınıf için `read` yetkisi `restricted` düzeyindeyse girer. Bu yetki Güvenlik okuyucu, Denetçi ve break-glass rollerinde vardır.
   - Diğer kullanıcılara bölümün varlığı bile gösterilmez.
   - Her gösterim `audit.security_event`'e `restricted_read` olarak yazılır (kim, hangi CI).

5. **Arayüz:** CI ayrıntısında "Güvenlik duruşu (kısıtlı)" bölümü görünür. Bölümde şunlar yer alır:
   - maruziyet ve risk (renkli);
   - koruma ve sağlık durumu;
   - son görülme, kaynak ve gözlem zamanı;
   - "bu bilgi kısıtlıdır, paylaşmayın" notu.

   Demo modunda (statik sunucu) bölüm yoktur.

## Değerlendirilen seçenekler

- **Duruş alanlarını CI özniteliği olarak `sensitivity: restricted` ile tutmak:** Alan hassasiyeti API'de süzülür. Ama değer golden record'da, kaynak kaydında, değişim geçmişinde ve olaylarda dolaşır; her yeni okuyucu (dizin, MCP, GRC beslemesi, replika) ayrıca süzmek zorunda kalır. Reddedildi.
- **Ayrı Kafka konusu (`evren.ingest.posture.v1`):** Konu düzeyinde ACL verir. Ama aynı makinenin kaydı iki konuya bölünür ve CI çözümlemesi iki kez yapılır. Reddedildi. Konunun kendisi zaten TLS ve ACL ile korunur (U10). Duruş verisi taşınırken Kafka'dadır; bu satır tehdit modelinde yazılıdır.
- **Duruş verisini hiç saklamamak, Defender'a canlı sormak:** Kontrol ve rapor (GRC, korumasız sunucu) her seferinde dış API'ye bağımlı olur; hız sınırı dakikada 100 çağrıdır. Reddedildi.

## Sonuçlar

- **Olumlu:**
  - Kısıtlı veri tek tabloda durur.
  - Kim okuduysa denetim izinde görünür.
  - Ürün değişirse (ör. başka bir EDR) yalnızca bağlayıcı ve eşleme değişir; rol aynı kalır.
- **Olumsuz:** Zafiyet sayıları `/api/machines`'te yoktur; ayrı zafiyet API'si gerekir (sonraki adım). Satır, makine Defender'dan ayrılınca silinmez; gözlem zamanı eskir.
- **Riskler:**
  - Kafka konusunda ve DLQ'da kısıtlı veri taşınır. Üretimde TLS, konu ACL'i ve saklama süresi gerekir (U10).
  - Ayırma (split) sonrası eski CI'daki duruş satırı yeni çalışmaya kadar kalır.

## Güvenlik ve uyum etkisi

- Tehdit modeline şu satırlar eklenir:
  - kısıtlı verinin kaynak kaydına, CI'a, dizine ve olaya sızmaması (testli);
  - okuma denetimi;
  - rol dışı kaynaktan gelen duruşun yok sayılması.
- OWASP ASVS 2: V1.8 (veri sınıflandırma), V4.2 (nesne düzeyi yetki), V7.1 (denetim izi), V8.1 (hassas veri koruma).

## Doğrulama

- **Birim:**
  - Defender eşlemesi duruşu `posture` bölümüne koyar, özniteliklere koymaz.
  - Bilinmeyen değer boşa düşer.
  - Zarf şeması güncel.
- **PostgreSQL:**
  - Bağlı CI için satır yazılır; kaynak kaydında duruş yoktur.
  - Değişmeyen kayıt tazeler.
  - Gölge veya başka rol yazamaz.
  - Daha öncelikli kaynağın satırı ezilmez.
  - Okuyucu rolü tabloyu okuyamaz (0003).
- **API:**
  - Okuyucu duruşu görmez.
  - Güvenlik okuyucu görür ve okuma denetim izine yazılır.
- **Tarayıcı:** Güvenlik okuyucu CI ayrıntısında bölümü görür.
- **Yığın duman testi:** Defender simülatöründen sonra ServiceNow'dan gelen CI'ın duruşu API'de görünür.
