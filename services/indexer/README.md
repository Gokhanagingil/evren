# services/indexer — Arama dizinleyicisi (U3, ADR-0018)

CI değişiklik olaylarını (`evren.cmdb.ci.changed.v1`) okur, CI'ın **güncel** hâlini PostgreSQL'den alır ve OpenSearch'teki `evren-cmdb-ci` takma adına yazar.

```bash
python -m evren_indexer            # sürekli (açılışta dizini doğrular/kurar)
python -m evren_indexer --reindex  # tam yeniden dizinleme (yeni sürümlü dizin + atomik takma ad) ve çık
# Konteynerde: evren indexer [--reindex]
```

- **Teslim:** En az bir kez okunur. Ofset ancak OpenSearch yazımı başarılı olunca saklanır. Hata durumunda bölümler geri sarılır ve üstel beklemeyle yeniden denenir. Olay içeriği değil güncel CI dizinlendiği için sıra ve tekrar sorun olmaz.
- **Dizin:**
  - Eşleme ve sorgu `evren_core/search.py`'dedir (Türkçe katlama, önek, ayırıcısız yazım, bulanık eşleşme).
  - Eşleme değişince `MAPPING_VERSION` artırılır; dizinleyici açılışta kesintisiz yeniden dizinler.
  - Gizli hassasiyetteki alanlar dizine girmez.
- **Yetki (göç 0013):** `evren_app_indexer` rolü okuyucu haklarına sahiptir. Kaynak bağından yalnızca `ci_sys_id, data_source, active` sütunlarını okur; yazabildiği tek tablo kendi nabzıdır (`kind='indexer'`). Giriş rolü `evren_indexer_svc`'dir.
- **Ortam:** `EVREN_DATABASE_URL_FILE`, `EVREN_KAFKA_*`, `EVREN_OPENSEARCH_*`, `EVREN_WORKER_HEALTH_FILE`, `EVREN_KAFKA_CONSUMER_GROUP` (varsayılan `evren-indexer`).
- **Testler:**
  - `services/indexer/tests`: gerçek PostgreSQL, taklit OpenSearch ve Kafka. Yeniden dizinleme, ofset ve geri sarma, salt okunur rol sınırları sınanır.
  - Gerçek motorla: `packages/evren-core/tests/test_search_os.py` ve `tools/dev_stack.py smoke`.
