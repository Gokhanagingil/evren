# ADR-0036: Zamanlanmış yönetişim kampanyaları

- Durum: Kabul edildi
- Tarih: 27.11.2026
- Karar verenler: Proje sahibi (istek 27.11.2026; U8.5, F1-25), mimari, güvenlik

## Bağlam

Proje sahibinin örneği şuydu: Konfigürasyon yöneticisi uygulama sahiplerine düzenli görev atayabilmeli. Sahipler uygulamalarının verilerinin güncel olduğunu teyit etmeli ve kullanıcı erişimlerini gözden geçirmeli. Tanım yönetim panelinden yapılmalı.

ADR-0035 görev modelini, kanalları ve görev çalışanını kurdu. Bu ADR, görevin zamanlanmış kaynağını tanımlar: **kampanya**. Bir kampanya şu dört soruyu yanıtlar:

- ne istenecek,
- hangi CI'lar için,
- ne sıklıkla,
- kaç günde.

Örnek genelleştirildi. Kampanya bir ürüne veya sınıfa bağlı değildir. Kapsam, sınıf ve ServiceNow koşul sözdizimiyle yazılır. Tür, sorumludan isteneni belirler.

## Karar

### 1. Kampanya tanımı (`gov.campaign`)

**Tür.** Üç tür vardır:

- `attestation`: veri güncelliği teyidi.
- `access_review`: erişim gözden geçirme.
- `checklist`: kontrol listesi.

Tür, görevin türü olur. Kapanış kararları ADR-0035'teki görev kararlarıdır.

**Kapsam.** Kapsam bir sınıf ve isteğe bağlı bir koşuldur (encoded query).

- Üst sınıf alt sınıflarını da kapsar. Örneğin "Sunucu" Linux ve Windows sunucularını da içerir.
- Koşuldaki alan adları yalnızca metadata sözlüğünden çözülür (`metadata_field_resolver`).
- Değerler parametre olarak geçer.
- Emekli CI kapsama girmez; bu, sahiplik onayıyla aynı kuraldır.

**Sıklık.** Dört hazır sıklık vardır: her ay, her çeyrek, altı ayda bir, her yıl. Hepsi dönemin ilk günü 09:00'da çalışır (İstanbul saati). Bunların yerine 5 alanlı cron ifadesi de yazılabilir. API kaydetmeden önce sonraki çalışma zamanını hesaplar; hesaplanamayan ifade reddedilir.

**Süre ve sınır.**

- Süre 1 ile 180 gün arasındadır.
- Kanal isteğe bağlıdır. Boşsa görev açılırken varsayılan kanal kullanılır.
- Kapsam sınırı varsayılan olarak 2000, en çok 20 000'dir. Kapsam sınırı aşarsa çalışma görev açmadan "başarısız" biter. Böylece yanlış bir koşulla binlerce görev açılmaz.

### 2. Yaşam döngüsü ve yetki

Kampanya şu durumlardan geçer:

- taslak → etkin,
- etkin ↔ duraklatıldı,
- her durumdan → emekli.

Kurallar:

- Emekli kampanya geri dönmez.
- Durum değişikliği en az 10 karakterlik gerekçe ister.
- Yalnızca taslak veya duraklatılmış kampanya düzenlenir.
- Duraklatma ve emekliye ayırma yeni görev açılmasını durdurur. Açık görevler sürer.

Yetki dağılımı:

| Kim | Ne yapabilir |
|---|---|
| Konfigürasyon yöneticisi (`governance` / `task_admin`) | Tanımlar, önizler, durum değiştirir, "şimdi çalıştır" ister |
| Denetçi | Okur |
| Diğerleri | Kampanyayı görmez |

Her yazma işlemi denetim kaydına düşer: `campaign_create`, `campaign_update`, `campaign_run`.

### 3. Önizleme önce gelir

Panel, kaydetmeden önce kapsamı önizler. Önizleme şunları gösterir:

- kaç CI'ın kapsamda olduğu,
- görevin sahibine, yönetenine, destek grubuna gideceği veya atanmamış kalacağı CI sayıları,
- kaç kişi ve grubun görev alacağı,
- örnek CI'lar,
- açık görevi olan CI sayısı.

Önizleme bir denetimdir. Geçersiz koşul 200 yanıtıyla `valid: false` döner (CSV önizlemesi gibi). Kayıt ise aynı koşulu 422 ile reddeder.

### 4. Görev çalışanı yürütür; API yalnızca ister

Görev çalışanı (`evren_app_governance`) her turda iki iş yapar:

- **Zamanı gelen etkin kampanyalar.** `next_run_at` önce ilerletilir, sonra çalışma yürütülür. Kaçırılan dönemler yeniden oynatılmaz; kampanya bir kez çalışır.
- **Elle istenen çalışmalar.** API'nin `requested` durumunda yazdığı satırlar.

"Şimdi çalıştır" zamanlamayı değiştirmez. Aynı kampanyanın beklemede veya çalışmakta olan bir çalışması varsa yeni istek 409 alır.

Kapsamdaki her CI için ADR-0035'teki sorumlu sırası uygulanır. Görev tekillik anahtarı `campaign:<kampanya>:<ci>` ile açılır. Aynı CI için açık görev varsa yenisi açılmaz; çalışma bunu "zaten açık" olarak sayar. Görev başlığı, kampanya başlığı ile CI adının birleşimidir.

Veritabanı yetkileri iki tarafı ayırır:

| Rol | Yapabilir | Yapamaz |
|---|---|---|
| Görev çalışanı | Zamanlama sütunlarını ilerletir, çalışma sonucunu yazar, görev açar | Kampanyanın tanımını ve durumunu değiştiremez |
| API | Kampanya tanımını ve durumunu yazar, çalışma isteği ekler | Çalışma sonucunu yazamaz, kampanya silemez |

### 5. Veri güncelliği teyidi sahiplik onayına bağlıdır

Sahiplik onayı (ADR-0027) verilir veya reddedilirse CI'ın açık teyit görevi kendiliğinden kapanır:

- onay → "Doğrulandı",
- ret → "Reddedildi".

Bunu bir veritabanı tetikleyicisi yapar (`gov.tg_attestation_closes_tasks`). Böylece sorumlu aynı işi iki kez yapmaz, görev çalışanına da karar yetkisi verilmez. Kapanış görev geçmişine `evren:sahiplik-onayi` olarak yazılır. Görev ServiceNow'da açıldıysa görev çalışanı o kaydı kararla kapatır (ADR-0035).

### 6. Hatırlatma ve süre aşımı

Görev çalışanı iki olay yazar, her biri görev başına bir kez:

- son tarihe 3 gün kala "hatırlatıldı",
- son tarih geçince "süresi geçti".

Süresi geçen görev sayısı kampanya listesinde görünür. Son yedi günde başarısız çalışan kampanya "Size düşen işler"e düşer.

## Sonuçlar

- Konfigürasyon yöneticisi düzenli görevleri panelden tanımlar (Yönetim → Yönetişim görevleri → Zamanlanmış görev tanımla). Kampanyalar Görevler sayfasının sekmesidir.
- Kampanya altyapısı kanaldan bağımsızdır. ServiceNow'dan Evren kanalına geçiş kampanyayı değiştirmez.
- Bulgu kuralları (U8.6, F1-26) aynı yolu kullanacak. Örnek: hiçbir uygulama veya sunucuyla ilişkisi olmayan veritabanının sahibine görev. Kaynak `rule` olur, tekillik anahtarı `rule:<kural>:<ci>` olur.

## Bilinen sınırlar

- Hatırlatma ve süre aşımı şimdilik yalnızca görev geçmişinde ve listelerde görünür. E-posta veya Teams bildirimi U8.9'da (bildirimler) gelecek.
- Kapsam koşulu metinle yazılır. Görsel koşul oluşturucu yok; önizleme ve hata mesajı yol gösterir.
- Erişim gözden geçirmede Evren erişim listesini kendisi getirmez. Sorumlu, ilgili sistemde gözden geçirip sonucu görevde kaydeder. Kaynak sistemden erişim listesi alımı ayrı bir karar konusudur.
- Bütün testler sentetik veri ve ServiceNow simülatörüyle yapıldı. Gerçek ServiceNow kabulü U10'dadır.
