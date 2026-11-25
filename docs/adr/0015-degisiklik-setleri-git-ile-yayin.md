# ADR-0015: Metadata değişiklik setleri Evren'de onaylanır, Git'te yayınlanır

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

CLAUDE.md kural 6: şema değişikliği metadata ile yapılır, sınıf alanı için DDL yazılmaz; üretimde değişiklik setiyle yayınlanır (taslak → onay → yayın; hazırlayan onaylayamaz). Metadata'nın doğruluk kaynağı `metadata/*.yaml` dosyalarıdır; servisler açılışta bunları yükler. Veri kaynağı yaşam döngüsü (ürün değişimi, ADR-0009) de metadata'dır. İki seçenek vardı: metadata'yı veritabanına taşıyıp ServiceNow gibi anında yayınlamak, ya da Git'i doğruluk kaynağı tutmak.

## Karar

1. **Hazırlama ve onay Evren'de:** Şema ve Değişiklikler sayfasında set hazırlanır (`schema/schema_draft`), işlemler kaydedilmeden önce tüm metadata kurallarıyla doğrulanır (`evren_core.changeset.apply_changes` + `metadata.validate`), gönderilince etki analizi yapılır, hazırlayan dışında biri onaylar (`schema/schema_approve`).
2. **Görevler ayrılığı üç katmanda:** API (403 `hazirlayan_onaylayamaz`), veritabanı kısıtları (`approved_by <> created_by`, `rejected_by <> created_by`) ve tetikleyici (izinli durum geçişleri, yayınlanan/reddedilen set ve işlemleri değişmez, silinmez; işlem yalnız taslağa eklenir). Girişte rol düzeyinde SoD (tasarımcı ↔ onaylayıcı) Entra'da zaten uygulanır.
3. **Yayın = değişiklik dosyası:** yayın, `metadata/changes/<zaman>-<kimlik>-<ad>.yaml` dosyasını (hazırlayan, onaylayan, zaman, işlemler; SHA-256) üretir. Dosya PR ile repoya girer; CI `load_metadata --check` ile doğrular; dağıtımda yükleyici temel dosyalardan sonra değişiklik dosyalarını ad sırasıyla uygular. Temel YAML'ların yorumları bozulmaz; geri alma `git revert`'tür; her ortam aynı sonucu üretir.
4. **Çakışma önleme:** doğrulama, çalışan metadata'nın üzerine yayınlanmış ama henüz dağıtılmamış setler uygulanarak yapılır.
5. **İşlemler:** `add_class`, `add_field` (`u_` önekli, JSONB), `modify_field` (etiket, hassasiyet, uzunluk yalnız artırma), `deprecate_field` (değer kalır, düzenlenemez), `add_choice`, `add_data_source` (her zaman gölge başlar), `set_source_lifecycle` (izinli geçişler: gölge → etkin/emekli, etkin → devrediliyor, devrediliyor → etkin/emekli). Alan silme (`drop_field`) ve fiziksel kolona taşıma bu sürümde yok.
6. **Yaşam döngüsü değişikliğinden sonra** bağlı CI'lar `python -m evren_reconciler.worker --rereconcile <kaynak>` ile yeniden uzlaştırılır.

## Değerlendirilen seçenekler

- **Metadata'yı veritabanında tutup anında yayınlamak:** ServiceNow deneyimine daha yakın; ancak servislerin metadata'yı veritabanından okuması, sürümleme, ortamlar arası taşıma ve geri alma için ayrı mekanizma gerekir; Git geçmişi ve kod incelemesi kaybolur.
- **Değişikliği doğrudan YAML dosyalarına yazmak:** dosya yorumları ve sıralaması bozulur; inceleme zorlaşır.

## Sonuçlar

- Yayın anında etkili değildir: PR birleşip dağıtılana kadar servisler eski metadata ile çalışır. Arayüz bunu açıkça söyler ve dağıtım adımlarını gösterir.
- PR incelemesi ikinci bir kontrol katmanıdır; Evren'deki onay denetim kanıtıdır (ISO 27001 değişiklik yönetimi).
- Kurum sürekli dağıtım hattını kurduğunda (hedef ortam kararı) PR açma otomatikleştirilebilir.
