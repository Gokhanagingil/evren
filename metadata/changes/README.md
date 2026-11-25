# metadata/changes — yayınlanmış değişiklik setleri (ADR-0015)

Bu klasördeki `*.yaml` dosyaları Evren arayüzünde **taslak → onay → yayın** akışından geçmiş değişiklik setleridir
(Şema ve Değişiklikler sayfası). `load_metadata` temel dosyaları (`../classes.yaml`, `../data_sources.yaml`…)
yükledikten sonra bu dosyaları **ad sırasıyla** uygular. Dosya adı yayın zamanıyla başlar.

- Elle düzenlemeyin: düzeltme için yeni bir değişiklik seti açın. Geri alma `git revert`'tür.
- Dosya, hazırlayanı ve onaylayanı (farklı kişiler; veritabanı kısıtı) başlıkta taşır.
- Dosyayı repoya ekleyen PR, CI'da `python tools/load_metadata.py --check` ile doğrulanır.
- Veri kaynağı yaşam döngüsü değişikliğinden sonra: `python -m evren_reconciler.worker --rereconcile <kaynak>`.
