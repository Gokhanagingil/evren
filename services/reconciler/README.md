# services/reconciler — Uzlaştırıcı (F0-10)

## Durum (25.11.2026)

| Parça | Durum | Nerede |
| --- | --- | --- |
| PostgreSQL deposu: süzgeç → kimlik → bağ/kuyruk/yeni CI → uzlaştırma → denetim + outbox (tek işlem) | ✓ | `evren_reconciler/store.py` |
| Aday dizini (kesin anahtarlar) | ✓ | `evren_reconciler/pg_identity.py`, göç `0005_identity_index.sql` |
| Çalışma sonu: devre kesici, `missing` işaretleme (yalnızca OK), anomali kaydı, ilişkiler | ✓ | `ReconcilerStore.finish_run` |
| Ürün yaşam döngüsü: gölge kaynak yazmaz ve CI açmaz; devredilen kaynak CI açmaz | ✓ | `SourceRegistry` + `_process` |
| Kafka tüketicisi (kaynak kayıtları), outbox yayıncısı, Valkey kilidi | Planlandı | F0-10 ikinci adım (hedef ortam kararı bekleniyor) |
| Bulanık ad adayları (OpenSearch) | Planlandı | F1-08 |
| İnsan kararları: kuyruk kararları (`apply_decisions`), birleştirmeyi ayırma (`apply_splits`, ADR-0011), elle düzenleme (`apply_manual_edits`, ADR-0012) | ✓ | göç `0006`–`0008` |
| Sürekli çalışan: bildirimle uyanma, istek başına hata yalıtımı, nabız, düzgün kapanış (ADR-0013) | ✓ | `evren_reconciler/worker.py`, göç `0009` |
| Ürün değişimi: yaşam döngüsü/rol sırası değişince bağlı CI'ları yeniden uzlaştırma (`rereconcile_source`; emekli kaynağın tek başına verdiği alanlar silinmez, sayılır) | ✓ | F1-13, `tests/test_product_swap_pg.py` |
| Eskime önerileri | Planlandı | F1-06 |

Testler: `services/reconciler/tests` (gerçek PostgreSQL, `evren_app_reconciler` rolüyle). `tools/load_demo_pg.py`
aynı sentetik envanteri bu depodan geçirir; test, sonucun bellek içi hatla (demo.json) öznitelik düzeyinde
aynı olduğunu doğrular.

Devre kesici notu: değişim oranı yalnızca **önceden var olup içeriği değişen** kayıtları sayar. İlk kez görülen
kayıtlar (ilk yükleme, yeni ürün) sayılmaz; aksi hâlde her yeni kaynak karantinaya düşerdi. Elle giriş ve platform
çalışmalarına devre kesici uygulanmaz.

## Çalıştırma

```bash
export EVREN_DATABASE_URL_FILE=/run/secrets/evren_reconciler_db   # bağlantı adresi dosyadan; günlüğe yazılmaz
python -m evren_reconciler.worker                 # sürekli: bildirimle uyanır, 30 sn'de bir tarar
python -m evren_reconciler.worker --once          # bekleyen istekleri bir tur uygula ve çık
```

| Ortam değişkeni | Varsayılan | Açıklama |
| --- | --- | --- |
| `EVREN_DATABASE_URL_FILE` / `EVREN_DATABASE_URL` | libpq ortamı | Bağlantı adresi (dosya tercih edilir) |
| `EVREN_DB_ROLE` | `evren_app_reconciler` | Bağlantıdan sonra geçilen rol (en az yetki) |
| `EVREN_WORKER_POLL_SECONDS` | `30` | Bildirim kaçarsa tarama aralığı (1–3600) |
| `EVREN_LOG_LEVEL` | `INFO` | Günlük düzeyi |
| `EVREN_WORKER_HEALTH_FILE` | — | Her turda dokunulan nabız dosyası; konteyner sağlık denetimi (`evren healthcheck worker`) bunu okur |

Konteynerde: `python tools/dev_stack.py up` ile `worker` servisi `evren_worker_svc` giriş rolüyle çalışır (ADR-0016).

- SIGTERM/SIGINT: elindeki turu bitirir, nabza `stopped_at` yazar, çıkar (en geç ~1 sn).
- Birden fazla örnek güvenle çalışır (`FOR UPDATE SKIP LOCKED`).
- Uygulanamayan istek hata kimliğiyle reddedilir; aynı kimlik günlükte yığın iziyle bulunur.
- Arayüz: Genel Bakış'ta "Uzlaştırıcı çalışıyor / çalışmıyor" ve bekleyen istek sayısı (`/api/v1/summary` → `worker`).

Golden record'un **tek yazarı**dır.

## Akış

Kafka `ingest.record` mesajları kaynak nesne anahtarına göre bölümlenir, böylece aynı nesnenin sırası korunur. Her kayıt için:

1. **Değişim süzgeci:** normalleştirilmiş içerik özeti `ingest.source_record.content_hash` ile aynıysa yalnızca `last_seen` güncellenir.
2. **Kimlik:** `evren_core.identity.IdentityResolver`. Aday dizininin üretim uygulaması iki kaynağı kullanır:
   - Kesin anahtarlar için PostgreSQL: seri numarası, UUID, MAC, FQDN, ad.
   - Bulanık ad adayları için OpenSearch.
3. **Karar:**
   - `rule_match`, `score_match`, `linked` → `ingest.identity_link`.
   - `review` → `ingest.match_candidate`.
   - `insufficient` → kayıt park edilir.
4. **Uzlaştırma:** CI'ın tüm etkin kaynak kayıtları `evren_core.reconcile.Reconciler`'dan geçer. Değişen öznitelikler yazılır:
   - `cmdb.cmdb_ci`
   - `cmdb.ci_reconciliation_state`
   - `audit.ci_change`
   - `events.outbox`
   Hepsi tek işlemde.
5. **Eşzamanlılık:** aynı CI için Valkey kilidi veya `pg_advisory_xact_lock(hashtext(sys_id))`.

## Çalışma sonu

`ingest.run.completed` olayında:

1. `evren_core.circuit_breaker.evaluate_run` çalışır. Taban çizgisi aynı kaynağın son başarılı çalışmalarıdır.
2. Yalnızca `OK` ise görülmeyen kaynak kayıtları `missing` olur. Eskime kuralları (`meta.cmdb_staleness_rule`) **tüm** yetkili kaynaklar CI'ı görmüyorsa durum önerisi üretir.
3. `WARN` veya `OPEN` → `insight.anomaly` kaydı açılır ve bildirim gider.

## Yeniden işleme

Kural değişikliğinden sonra etkilenen sınıfların CI'ları yeniden uzlaştırılabilir. Gerekirse ham olaylar Kafka'dan yeniden oynatılır.

## Performans hedefleri

- Normal günlük değişim (%1–5): dakikada en az 5.000 kayıt.
- İlk yükleme: tetikleyici açık, toplu işlem, 3 milyon CI için saatler mertebesi.
