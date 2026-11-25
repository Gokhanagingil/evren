# ADR-0004: Evren ana kaynak, ServiceNow CMDB replika

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

ServiceNow CMDB bir süre canlı kalmalı. CI seçimi önce ServiceNow'dan, sonra Evren'den yapılabilmeli. ServiceNow'daki business rule'lar düzenlenmemeli.

## Karar

Gölge fazından (F0–F1) sonra Evren ana kaynak olur.

- ServiceNow CMDB, IRE API üzerinden kapsamlı bir replika olarak beslenir.
- `task.cmdb_ci` alanları değişmez.
- ServiceNow kökenli CI'lar aynı sys_id'yi korur.
- CI seçici (F3), Evren aramasıyla replikadaki sys_id'ye çözülür.

## Değerlendirilen seçenekler

- **Remote Tables:** dot-walk yok, Integration Hub aboneliği gerekiyor; reference alanlarının ve script'lerin güncellenmesi şart. Reddedildi.
- **İlk günden Evren ve SN'de reference alanı değişikliği:** business rule, ACL ve atama kurallarında geniş değişiklik. Reddedildi.
- **Çift ana kaynak (iki yönlü serbest yazma):** çatışma ve veri kaybı riski. Reddedildi. Bunun yerine öznitelik bazında tek ana kaynak uygulanır (uzlaştırma kuralları).

## Sonuçlar

- **Olumlu:** ServiceNow süreçleri kesintisiz çalışır; geçiş geri alınabilir.
- **Olumsuz:** Geçiş döneminde iki sistemin işletimi gerekir; IRE API kapasitesi planlanmalı.
- **Lisans:** ITOM abonelik birimi sayımı teyit edilmeli (F2-04).
