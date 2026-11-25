# ADR-0001: Kayıt sistemi PostgreSQL, hibrit şema, metadata sözlüğü

- Durum: Kabul edildi
- Tarih: 25.11.2026
- Karar verenler: Gökhan Ağıngil (proje sahibi)

## Bağlam

İsterler:

- ServiceNow veri deseni.
- 3–5 milyon CI.
- Çalışma zamanında tablo ve alan ekleme/silme (ister 5).
- Kurumsal güvenlik.
- Lisans bağımlılığının azaltılması.

## Karar

Kayıt sistemi PostgreSQL 16+.

- `cmdb.cmdb_ci` tek tablodur: 35 çekirdek kolon + sınıfa özgü alanlar için `class_attributes` JSONB.
- Sınıf ve alan tanımları `meta.sys_db_object`, `meta.sys_dictionary` ve `meta.sys_choice` tablolarında tutulur.
- Tetikleyici JSONB içeriğini sözlüğe göre doğrular.
- `sn.<sınıf>` görünümleri ServiceNow adlarıyla tipli erişim sağlar.

## Değerlendirilen seçenekler

- **Sınıf başına fiziksel tablo** (ServiceNow'un iç yapısına benzer): her alan değişikliği DDL ister. Çalışma zamanı şema düzenleme riskli ve yavaş olur.
- **PostgreSQL tablo kalıtımı (INHERITS):** benzersizlik ve indeks kalıtılmaz; bakım zor.
- **Doküman veritabanı:** işlem bütünlüğü, ilişki sorguları ve rol tabanlı güvenlik zayıf.
- **Graf veritabanı (kayıt sistemi olarak):** lisans ve operasyon yükü. Etki analizi için gerekirse ileride projeksiyon olarak eklenebilir.

## Sonuçlar

- **Olumlu:** Alan ekleme DDL'siz; indeksli çekirdek alanlar hızlı; tek tabloda sınıf değişimi (reclassification) kolay.
- **Olumsuz:** JSONB alanlarında tip sistemini uygulama ve tetikleyici sağlar. Sık sorgulanan alana ifade indeksi gerekir (`indexed: true`).
- **Risk:** tetikleyici maliyeti. İlk toplu yüklemede ölçülecek; gerekirse yükleme sırasında oturum bazlı devre dışı bırakılır.

## Doğrulama

`tests/test_postgres.py`: sözlük dışı alan, yanlış tip ve kardeş sınıf alanı reddi; görünümlerde tipli kolon; INSTANCEOF sorgusu.
