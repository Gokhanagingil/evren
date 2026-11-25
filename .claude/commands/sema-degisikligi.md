---
description: CMDB'ye sınıf veya alan ekle/değiştir/kaldır
argument-hint: <değişiklik açıklaması>
---
İstenen şema değişikliği: $ARGUMENTS

1. **ServiceNow karşılığını kontrol et.** Varsa aynı adı ve aynı üst sınıfı kullan (`metadata/imported/` veya SN sözlüğü). Kurum özel alanlar için ServiceNow'daki `u_` önek kuralını izle.
2. **Değişikliği yap.**
   - Sınıf alanı `metadata/classes.yaml` dosyasına eklenir. `storage: column` yalnızca `cmdb_ci` alanları içindir; bunlar DDL değişikliği ister (ayrı migrasyon + ADR).
   - Her alana `label_tr` zorunlu. Hassas alan ise `sensitivity` belirle.
3. **Kaldırma.** Asla doğrudan silme. Akış: `deprecated` → arayüzden gizle → saklama süresi sonunda `drop_field` değişiklik seti.
4. **Doğrula ve yükle.**
   - `python tools/load_metadata.py --check`
   - Tohum SQL'i üret ve test veritabanında uygula.
   - `sn.*` görünümlerini kontrol et.
5. **Etkileri gözden geçir.**
   - Uzlaştırma kuralları alanı kapsıyor mu?
   - ServiceNow eşleme kapsamı (`sn_sync_scope`) etkileniyor mu?
   - İzinler ve alan hassasiyeti doğru mu?
   - Eşleme dosyaları alanı dolduruyor mu?
6. **Test ekle.** Örnekler: yeni alanın tetikleyici doğrulaması, görünümde tipli kolon.
7. **Üretimde** bu işlem şema değişiklik setiyle yapılır: taslak → onay → yayın; hazırlayan onaylayamaz. Bu komut yalnızca geliştirme tohumunu değiştirir.
