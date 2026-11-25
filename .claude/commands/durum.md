---
description: Oturum sonu — DURUM.md ve roadmap.json'u güncelle
---
Bu oturumda yapılan işleri kayda geçir:

1. `git diff --stat` ve `git log` ile bu oturumdaki değişiklikleri çıkar.
2. `DURUM.md`'yi güncelle:
   - "Son güncelleme" tarihi.
   - Etkin faz ve ilerleme sayısı (`roadmap.json`'dan hesapla).
   - Yeni kararlar: tarih, karar, kaynak (ADR veya kullanıcı onayı).
   - Yapılanlar: çalıştırılan doğrulamaların gerçek sonuçlarıyla (test sayısı, hangi ortam).
   - Bilinen eksikler: dürüstçe.
   - Sıradaki adımlar: sıralı, yol haritası kimlikleriyle.
3. `apps/web/content/roadmap.json`:
   - İş kalemi durumlarını eşitle: `done` / `in_progress` / `planned` / `blocked` / `at_risk`.
   - Devam eden, engelli veya riskli kalemlere `note` yaz.
   - Karşılanan çıkış kriterlerini `met: true` yap; yalnızca kanıt varsa.
   - `updated_at` alanını güncelle.
4. Mimari etkilendiyse `apps/web/content/architecture.yaml`'ı güncelle ve `python tools/build_architecture.py` çalıştır.
5. `cd packages/evren-core && python -m pytest -q tests/test_web_content.py` ile tutarlılığı doğrula.

Sonunda kullanıcıya en fazla 5 satırlık özet ver: ne yapıldı, ne kaldı, sıradaki adım.
