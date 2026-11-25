# ADR-0013: İnsan istekleri tablolardan, PostgreSQL bildirimiyle uyanan uzlaştırıcıyla uygulanır

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

Kuyruk kararı, ayırma ve elle düzenleme API'de istek olarak kaydediliyor; golden record'a uzlaştırıcı yazıyor (ADR-0010–0012). Bu istekleri sürekli uygulayan bir süreç gerekiyor. Kaynak kayıtlarının alımı için Kafka kararı (ADR-0002) geçerli; ancak hedef çalışma ortamı henüz belirsiz ve insan istekleri düşük hacimli, işlemsel ve sıralı.

## Karar

1. İnsan istekleri **PostgreSQL tablolarında** kalır (tek doğruluk kaynağı, işlemsel, denetlenebilir). Kafka'ya taşınmaz.
2. Uzlaştırıcı çalışanı (`python -m evren_reconciler.worker`) `LISTEN evren_work` ile **hemen uyanır**; tetikleyiciler (göç 0009) yeni istekte bildirim gönderir. Bildirim yük taşımaz; kaçan bildirime karşı `--poll` saniyede bir tarama yapılır.
3. Her istek kendi işleminde ve `FOR UPDATE SKIP LOCKED` ile alınır; birden fazla çalışan güvenle paralel çalışır.
4. **Hata yalıtımı:** beklenmeyen hata yalnızca o isteği geri alır ve hata kimliğiyle reddeder (kuyruk kilitlenmez). Geçici veritabanı hataları (`OperationalError`) isteği beklemede bırakır, çalışan yeniden dener.
5. **Görünürlük:** `ingest.worker_heartbeat` nabzı ve bekleyen istek sayıları Genel Bakış'ta gösterilir ("Uzlaştırıcı çalışıyor / çalışmıyor").

## Değerlendirilen seçenekler

- **İstekleri Kafka'ya yazmak:** API'nin hem veritabanına hem Kafka'ya yazması (çift yazım) veya outbox yayıncısı gerekir; düşük hacimli insan işi için gereksiz karmaşıklık.
- **Yalnızca periyodik tarama:** basit ama kullanıcı kararının etkisi tarama aralığı kadar gecikir.
- **API'nin isteği senkron uygulaması:** kural 1'i (tek yazan uzlaştırıcı) ihlal eder.

## Sonuçlar

- Kararın etkisi genellikle bir saniyenin altında görünür; çalışan yoksa arayüz uyarır.
- Kafka tüketicisi (kaynak kayıtlarının alımı) hedef ortam kararından sonra aynı çalışana eklenecek.
- Çalışan durumu şimdilik veritabanı nabzıyla izlenir; OpenTelemetry metrikleri F1'de.
