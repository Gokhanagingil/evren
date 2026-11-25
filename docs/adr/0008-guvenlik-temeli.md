# ADR-0008: Güvenlik temeli

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

Platform kurumsal standartlarda ve güvenlik öncelikli olmalı. CMDB yüksek değerli bir varlıktır.

## Karar

1. **Golden record'un tek yazarı uzlaştırıcıdır.** API'deki elle düzenleme Manual kaynak kaydı olarak yazılır. Veritabanı rolleri bunu zorlar.
2. **Servis başına NOLOGIN grup rolü** (`0003_security.sql`). Oturum açan hesaplar ve sırları operasyon tarafından kasadan yönetilir.
3. **Denetim izi salt eklemelidir.** UPDATE/DELETE/TRUNCATE tetikleyiciyle engellenir; saklama süresi dolan bölümleri yalnızca DBA ayırır. Rol değişiklikleri otomatik denetlenir.
4. **Görevler ayrılığı veritabanında zorlanır.** Kendine yetki verilemez; şema setini hazırlayan onaylayamaz.
5. **Kısıtlı güvenlik verisi ayrı tabloda, ayrı yetkiyle tutulur.**
6. **Tedarik zinciri:** hash kilitli bağımlılık, SHA ile sabitlenmiş eylemler, gitleaks, bandit, pip-audit, Trivy, SBOM.
7. **Hedef OWASP ASVS L2.** Tehdit modeli `docs/06-guvenlik.md`.

## Doğrulama

`tests/test_postgres.py`: yetki sınırları, salt ekleme, SoD. `apps/web/tests`: güvenlik başlıkları, CSP ihlali yok. `.github/workflows/security.yml`.
