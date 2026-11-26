# ADR-0027: Sahiplik onayı — CI sahibinin dönemsel beyanı, onay akışı üzerinden ve uzlaştırmadan geçerek

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U6.4, F1-17), mimari, güvenlik

## Bağlam

Keşif bir sunucunun var olduğunu ve donanımını söyler. Sahibini, hangi iş için kullanıldığını ve hâlâ gerekli olup olmadığını ise söyleyemez. Bu yönetişim alanları zamanla eskir: sahip ayrılır, sunucu başka ekibe geçer, kullanılmayan CI kayıtta kalır. ServiceNow'da bunun karşılığı CMDB Data Manager'ın **attestation** (sahiplik onayı) politikasıdır. Politika kapsamındaki CI için sorumluya görev açılır; sorumlu kaydı "doğru" (Attested) ya da "yanlış" (Rejected) olarak işaretler.

Alanlar sözlükte zaten var (U4.3, göç 0016–0017): `attested`, `attested_by`, `attested_date` ve `attestation_status` (Attested / Rejected / Not Yet Reviewed). ServiceNow'da asıl durum `attestation_status` alanıdır; `attested` boole alanı eskidir ve onaydan sonra güncellenmez (F0-07 kanıt defteri). Bu alanlar elle düzenleme ve oluşturma formlarında yer almaz (`SYSTEM_MANAGED`). Uzlaştırma kuralında da yalnızca `Manual` önceliği vardır; hiçbir kaynak bu alanları yazamaz (testli).

Eksik olan üç şey var: kimin, hangi CI için, ne zaman onay vermesi gerektiği; onayın golden record'a nasıl yazılacağı; süresi dolan, reddedilen ve sahipsiz CI'ın kime nasıl görüneceği.

## Karar

1. **Kapsam ve süre metadata ile tanımlanır:** `metadata/attestation.yaml`.
   - `policies` sıralı bir listedir. Her politikada sınıf listesi (`applies_to`, alt sınıflara kalıtılır), isteğe bağlı koşul (`where`: kök sınıftaki seçim alanı → izinli değerler) ve onay süresi (`period_days`, 30–730) bulunur. CI'a ilk uyan politika uygulanır. Hiçbir politikaya uymayan CI onay kapsamı dışındadır.
   - `owner_fields` (varsayılan `owned_by`, `managed_by`) onay verebilecek kişileri tutan alanlardır. `reminder_days` süre bitmeden önceki uyarı aralığıdır.
   - Doğrulayıcı (`load_metadata --check`) sınıfı, alanı ve değeri sözlükten denetler. Ürün adı reddedilir (kural 8). Koşul alanı yalnızca kök sınıfta sütun olarak saklanan bir alan olabilir; SQL'e yalnızca doğrulanmış ad girer.

2. **Onay veren kişi CI'ın kendi sorumlusudur.**
   - Kişi `owned_by` veya `managed_by` alanındaki kullanıcı olmalıdır. Ayrıca `attest` izni gerekir (yeni işlem, "CI sahibi" rolü). Veri sorumlusu başkası adına onay veremez; onay kişisel bir beyandır.
   - Oturumdaki kişi `ref.sys_user` kaydına eşlenir. Entra oturumunda eşleme yalnızca değişmez nesne kimliğiyle yapılır (`entra_oid`). Değişebilen UPN veya e-posta yetki kararında kullanılmaz (Microsoft yönergesi). Geliştirme oturumunda eşleme kullanıcı adıyla yapılır.
   - Eşlenemeyen kişiye "kullanıcı kaydınız yok" denir ve işlem yapılmaz.

3. **Karar iki türlüdür.**
   - **Onayla:** "Bu kayıt doğru ve sorumlusu benim." Tek tek ya da toplu verilebilir; bir istekte en çok 200 CI.
   - **Reddet:** neden kodu zorunludur, gerekçe de zorunludur. Kodlar: `not_owner` (sahibi ben değilim), `not_exists` (CI artık yok), `data_wrong` (kayıttaki bilgiler yanlış).
   - Ret Veri sorumlusuna bir `attestation_rejected` anomalisi açar (orta önem). Aynı CI sonradan onaylanırsa anomali kapanır, kanıt korunur. Ret CI'ı emekliye ayırmaz; yıkıcı işlem insanın ayrı kararıdır (kural 4 ve 10).

4. **Onayı uygulayan uzlaştırıcıdır (kural 1).**
   - API yalnızca `ingest.attestation_request`'e istek sütunlarıyla yazar. Bu sütunlar CI, karar, neden, gerekçe, eşlenen kullanıcı ve isteyendir.
   - Uzlaştırıcı, isteği uygulama anında yeniden doğrular: kişi hâlâ sorumlu mu, CI hâlâ kapsamda mı? Doğrulama geçerse dört alanı CI'ın `Manual` kaynak kaydına yazar ve kaydı uzlaştırır:
     - `attestation_status`: Attested veya Rejected;
     - `attested`;
     - `attested_by`: `sys_user` sys_id'si;
     - `attested_date`.
   - Değişim geçmişinde neden `attestation`, aktör isteyen kişidir. `audit.security_event`'e `ci_attest` kaydı düşer.

5. **Süre dolumu ve sahip değişikliği onayı geçersiz kılar.**
   - Uzlaştırıcı dönemsel olarak tarar. İki durumda CI'ı "Henüz incelenmedi" (Not Yet Reviewed, `attested` = false) yapar:
     - onay tarihine politika süresi eklendiğinde bugün geçilmişse;
     - onaylayan kişi artık `owner_fields` alanlarının hiçbirinde değilse. Sahip değişince eski sahibin beyanı yeni sahibi bağlamaz.
   - `attested_by` ve `attested_date` son onayın kanıtı olarak kalır. Yazım yine Manual kaydından ve uzlaştırmadan geçer; neden `attestation`.
   - Bu işlem yıkıcı değildir; devre kesiciye bağlı değildir.

6. **Görünürlük.**
   - CI ayrıntısında "Sahiplik onayı" bölümü gösterilir: durum, politika, son onay, son geçerlilik tarihi, sorumlu. Sorumluya Onayla ve Reddet düğmeleri çıkar.
   - Yeni **Sahiplik Onayı** sayfası dört görünüm sunar: Onayımı bekleyenler (toplu onay), Sahipsiz CI'lar (kapsamda ama `owner_fields` boş), Reddedilenler ve Süresi dolanlar.
   - Okuma, CI sınıfının okuma iznine tabidir. Sorumlu kişinin adı ve kullanıcı adı görünür; e-postası görünmez.
   - GRC kontrolü **CMDB-06** "Sahiplik onayı geçersiz kapsamdaki CI" eklenir. Kanıt: hiç onaylanmamış, süresi dolmuş veya reddedilmiş CI'lar. CMDB-03 (sahibi olmayan üretim CI'ı) olduğu gibi kalır.

## Sonuçlar

- Yönetişim alanlarının doğruluğu ölçülebilir hâle gelir. Onay oranı ve sahipsiz CI sayısı GRC Platformu'na kontrol sonucu olarak gider.
- Sahiplik onayı ServiceNow'daki görevle değil Evren'de yürür. ServiceNow replika olduğundan alanlar oraya replikayla gider (U9). ServiceNow'da iş akışı veya business rule gerekmez (kural 11).
- Entra oturumunda onay, `ref.sys_user.entra_oid` dolduğunda çalışır. Kullanıcıların Entra ile eşitlenmesi U8'dedir; o zamana kadar Entra kullanıcısı "kullanıcı kaydınız yok" uyarısı görür.
- Görev ve e-posta bildirimi yoktur. Hatırlatma, sayfadaki "süresi yaklaşıyor" görünümüyle yapılır; bildirim kanalı U8'de.

## Alternatifler

- **Onay alanlarını elle düzenlemeyle yazmak:** kimin, neden ve hangi yetkiyle onay verdiği belirsizleşir; sahip olmayan biri onay verebilir. Reddedildi.
- **Veri sorumlusunun toplu onayı:** beyanın anlamını ortadan kaldırır. Reddedildi; veri sorumlusu raporları görür ve sahip ataması yapar.
- **Kapsamı ürün kuralıyla sabitlemek:** her kurumun kapsamı farklıdır, metadata ile ayarlanmalıdır. Reddedildi.
- **Süre dolumunu yalnızca okumada hesaplamak:** ServiceNow replikasına ve GRC kanıtına giden alan eski kalırdı. Reddedildi; okuma tarafı da aynı kuralla "süresi doldu" gösterir. Dönemsel taramaya kadar geçen kısa süre bu sayede kapanır.
