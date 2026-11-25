# ADR-0003: Kimlik çözümleme — IRE semantiği + skor + çelişki denetimi + geri alınabilirlik

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

Aynı makine kaynaklarda farklı adlarla görünür: `abc_1`, `abc-1`, `abc_1@local`. Bunlar tekilleştirilmeli (ister 11). ServiceNow IRE yalnızca deterministik kurallarla çalışır; ad eşleşmesinde seri veya OS çelişkisini dikkate almaz, birleştirmeyi kolayca geri almaz.

## Karar

1. ServiceNow IRE tanımlama kuralları aynı semantikle korunur: öncelik, kalıtım, girdi atlama, çoklu eşleşme.
2. Kanonik normalizasyon eklenir. Çöp değerler kimlik sayılmaz.
3. Kural eşleşse bile güçlü çelişki varsa (seri, UUID, OS, sınıf, FQDN) otomatik birleştirme yapılmaz.
4. Kurallar sonuç vermezse olasılıksal skor devreye girer. Eşikler: otomatik ≥ 0,97 ve 0,10 fark; inceleme ≥ 0,35.
5. Birleştirme fiziksel değildir. `ingest.identity_link` bağlarıyla tutulur ve geri alınabilir (split).
6. Bağımlı sınıflar yalnızca üst CI bağlamında tanımlanır. Bu uygulanana kadar güvenli varsayılan `insufficient`'tır.

## Sonuçlar

- **Olumlu:** Yanlış birleştirme riski düşer, kararlar açıklanabilir. İnsan kararlarıyla ağırlıklar kalibre edilebilir.
- **Olumsuz:** İnceleme kuyruğu iş yükü oluşturur. Toplu karar ve yapay zekâ önerisiyle hafifletilir.

## Doğrulama

`tests/test_identity.py` (15 senaryo), `tests/test_mapping.py` (üç kaynaktan tek CI).
