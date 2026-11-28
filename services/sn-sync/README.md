# services/sn-sync — ServiceNow replikası (U9.1, F2-01, ADR-0042)

Evren ana kaynaktır, ServiceNow CMDB replikadır. sn-sync kapsamdaki CI'ları ServiceNow'a **Identification and
Reconciliation API** (IRE) ile yazar. ServiceNow'da iş kuralı, ACL veya görev alanı değişikliği gerekmez.

ServiceNow'dan Evren'e okuma bu serviste değildir:

- İlk yükleme ve değişiklik çekme ServiceNow bağlayıcısıyla yapılır (`connectors/servicenow`, ADR-0020).
- Değişiklik kayıtları ayrı bağlayıcıyla gelir (ADR-0025).
- Replikadan geri gelen kayıt mükerrer CI açmaz (`sync.sn_ci_map`).

## Çalıştırma

```bash
python -m evren_sn_sync              # sürekli (PYTHONPATH: packages/evren-core/src:services/sn-sync)
python -m evren_sn_sync --once       # tek tur
python -m evren_sn_sync --check      # yalnızca yapılandırma
evren sn-sync                        # konteyner (deploy/evren.py)
```

Ortam: `EVREN_DATABASE_URL(_FILE)`, `EVREN_DB_ROLE` (`evren_app_sync`).

| Değişken | Anlamı |
| --- | --- |
| `EVREN_SN_REPLICA_URL` | ServiceNow örneğinin adresi |
| `EVREN_SN_REPLICA_TOKEN_FILE` | OAuth belirteci (dosya) |
| `EVREN_SN_REPLICA_USER` + `EVREN_SN_REPLICA_PASSWORD_FILE` | Belirteç yerine kullanıcı ve parola (dosya) |
| `EVREN_SN_REPLICA_CA_FILE` | Kurum sertifika yetkilisi dosyası |
| `EVREN_SN_REPLICA_SIMULATE=1` | Yalnızca geliştirme |
| `EVREN_SN_SOURCE_NAME` | Veri kaynağı adı (varsayılan `metadata/sn_sync.yaml`) |
| `EVREN_REPLICA_BATCH` | İstek başına CI (varsayılan 25) |
| `EVREN_REPLICA_LIMIT` | Tur başına CI (varsayılan 500) |
| `EVREN_WORKER_POLL_SECONDS` | Tur aralığı, saniye (varsayılan 60) |

Mod ve tur sınırı veritabanındadır; ServiceNow Replikası sayfasından değiştirilir.

## Tur

1. **Mod** (`sync.replica_setting`): kapalı, gölge veya etkin. Kapalıyken yalnızca nabız yazılır.
2. **Değişen CI'lar.**
   - Kapsamdaki sınıflar ve alt sınıfları alınır.
   - Değişiklik CI sürümüyle (`golden_version`) algılanır.
   - Yük özeti aynıysa gönderilmez; yalnızca görülen sürüm ilerler.
3. **Gölge.** Yük `/identifyreconcile/query`'ye gider ve hiçbir şey yazılmaz. Sonuç (uygun, çelişki veya hata) eşlemeye kaydedilir.
4. **Etkin.**
   - **Toplu değişiklik freni:** gerçekten değişen CI sayısı tur sınırını aşarsa tur bekletilir; yetkili kişinin onayından sonra gönderilir.
   - **Önce doğrula, sonra yaz:** eşlemesi doğrulanmamış CI önce denenir. Beklenen sonuç ServiceNow kökenli CI için aynı sys_id, Evren'de doğan CI için yeni kayıttır. Beklenmeyen sonuç çelişki olur ve yazılmaz.
   - Aynı seri numarasını veya sınıf + adı taşıyan ikinci CI aynı turda gönderilmez.
5. **İlişkiler** (etkin modda). İki ucu da eşit olan ilişki gönderilir. Evren'de kalkan ilişki "kaldırıldı" diye işaretlenir; IRE ilişki silmez.
6. **Hata.** Kayıt 1, 2, 4… dakikada, en çok 6 saatte bir yeniden denenir. Hata iletisi sır ve sunucu gövdesi içermez.

## Güvenlik

- **Veritabanı rolü** `evren_app_sync`. CMDB'ye, replika ayarına ve onay sütunlarına yazamaz. Yalnızca eşleme, çalışma ve nabız yazar.
- **ServiceNow entegrasyon kullanıcısı** ayrıdır ve en az yetkilidir: IRE ile yazma ve kapsamdaki tabloları okuma. Kimlik bilgisi dosyadan okunur ve görev kanalının kimliğinden ayrıdır.
- **Gönderilen alanlar** sözlükte `sn_sync` işaretli, ServiceNow'da da var olan ve kısıtlı olmayan alanlardır.
- **Kişi ve grup referansı** yalnızca kayıt ServiceNow kaynaklıysa gönderilir.

## ServiceNow tarafında gerekenler (F2-02, canlı kabulde)

1. `cmdb_ci.discovery_source` değer listesine **Evren** eklenir.
2. Kapsamdaki sınıf ve alanlar için "Evren" veri kaynağına en yüksek öncelikli uzlaştırma kuralı verilir.
3. Entegrasyon kullanıcısı açılır.
4. Eski doğrudan yazan entegrasyonlar kapatılır (F2-03).

## Açık konular

- ITOM abonelik birimlerinin üçüncü taraf kaynaklı CI'larda nasıl sayıldığı ServiceNow ile teyit edilecek (F2-04).
- ServiceNow'da ilişki kaldırma şimdilik işletim prosedürüdür.
- Sınamalar bellek içi simülatörle yapıldı. Gerçek ServiceNow kimlik kurallarıyla kabul U10'dadır.
