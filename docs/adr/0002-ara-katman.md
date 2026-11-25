# ADR-0002: Ara katman — Kafka + uzlaştırma motoru + OpenSearch

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

Çok sayıda kaynaktan milyonlarca gözlem gelir. Bunlar CMDB'ye doğrudan basılmak yerine anlamlandırılmalı, karşılaştırılmalı, trend ve anomali analizi yapılmalı. Ara katman için Elasticsearch ve Redis önerildi.

## Karar

Ara katman üç rolden oluşur:

1. **Olay omurgası — Apache Kafka (KRaft):** kaynakları ve işleyicileri ayırır, yeniden oynatma sağlar, bölüm başına sırayı korur.
2. **Anlamlandırma motoru — Evren uzlaştırıcısı:** normalizasyon, kimlik, uzlaştırma, devre kesici. İşin kalbi budur.
3. **Arama ve analiz — OpenSearch:** CI seçici araması, bulanık aday bulma, trend toplama, Random Cut Forest anomali tespiti (Apache 2.0).

**Redis yalnızca önbellek, kilit ve hız sınırı içindir.** Lisans nedeniyle Valkey kullanılır.

**Telemetri CMDB'ye girmez.** Metrik ve alarm kaynakta kalır; yalnızca konfigürasyon verisi ve özet sinyal gelir. Değişmeyen gözlemler içerik özetiyle elenir.

## Değerlendirilen seçenekler

- **Elasticsearch:** teknik olarak uygun. Ancak makine öğrenmesi anomali tespiti ücretli abonelik gerektiriyor ve lisans bağımlılığını azaltma hedefiyle çelişiyor. Kurumda Elastic aboneliği zaten varsa arama katmanı soyutlandığı için değiştirilebilir.
- **Ara katman olarak Redis:** bellek içi anahtar-değer deposudur. Geçmiş, trend ve karmaşık sorgu için uygun değil.
- **Yalnızca PostgreSQL:** küçük ölçekte yeterli olurdu. Ancak yeniden oynatma, kaynak ayrıştırma ve bulanık arama için ek bileşen gerekir.

## Sonuçlar

- **Olumlu:** Keşif aracı değişince yalnızca bağlayıcı değişir. Kural değişikliğinden sonra geçmiş yeniden işlenebilir.
- **Olumsuz:** Kafka ve OpenSearch işletim bilgisi gerekir. Ekip yetkinliği ve işletim sorumluluğu F0'da netleşmeli.
