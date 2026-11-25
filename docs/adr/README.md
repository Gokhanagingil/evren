# Mimari karar kayıtları (ADR)

| No | Karar | Durum | Tarih |
| --- | --- | --- | --- |
| [0001](0001-kayit-sistemi-postgresql-hibrit-sema.md) | Kayıt sistemi PostgreSQL, hibrit şema, metadata sözlüğü | Kabul edildi | 25.11.2026 |
| [0002](0002-ara-katman.md) | Ara katman: Kafka + uzlaştırma motoru + OpenSearch; Redis yalnızca önbellek; telemetri CMDB'ye girmez | Kabul edildi | 25.11.2026 |
| [0003](0003-kimlik-cozumleme.md) | IRE semantiği + olasılıksal skor + çelişki denetimi + geri alınabilir birleştirme | Kabul edildi | 25.11.2026 |
| [0004](0004-servicenow-replika-deseni.md) | Evren ana kaynak, ServiceNow CMDB replika; sys_id korunur | Kabul edildi | 25.11.2026 |
| [0005](0005-uygulama-yigini.md) | Python 3.12, FastAPI, psycopg 3; GRC Platformu ile uyum | Kabul edildi | 25.11.2026 |
| [0006](0006-derlemesiz-arayuz.md) | Derlemesiz arayüz; mimari harita ve yol haritası uygulama içinde | Kabul edildi | 25.11.2026 |
| [0007](0007-mcp-kurum-uygulamasi.md) | MCP: kurumun kendi uygulaması + Evren MCP sunucusu | Kabul edildi | 25.11.2026 |
| [0008](0008-guvenlik-temeli.md) | Güvenlik temeli: tek yazar, en az yetkili roller, salt ekleme denetim izi | Kabul edildi | 25.11.2026 |
| [0009](0009-kaynak-rolleri-urun-bagimsizligi.md) | Kaynak rolleri: kurallar ürüne değil role bağlı; yaşam döngüsüyle ürün değişimi | Kabul edildi | 25.11.2026 |
| [0010](0010-api-oturum-ve-yetki.md) | API: Entra OIDC + PKCE, şifreli çerez oturumu, RBAC + hassasiyet + SoD, kararı uzlaştırıcı uygular | Kabul edildi | 25.11.2026 |
| [0011](0011-birlestirmeyi-ayirma.md) | Birleştirmeyi ayırma: istek API'de, uygulama uzlaştırıcıda; kimlik profili yeniden kurulur, ayrılan kaynağın değerleri temizlenir | Kabul edildi | 25.11.2026 |
| [0012](0012-elle-duzenleme.md) | Elle düzenleme: istek tablosu, CI başına tek Manual kaydı, rol önceliği ve alan başına sonuç; API kaynak kaydı yazmaz | Kabul edildi | 25.11.2026 |
| [0013](0013-insan-istekleri-postgresql-bildirimi.md) | İnsan istekleri tablolarda; uzlaştırıcı çalışanı PostgreSQL bildirimiyle uyanır, istek başına hata yalıtımı, nabız | Kabul edildi | 25.11.2026 |
| [0014](0014-golge-mod-raporu-ve-kanit.md) | Gölge mod raporu (rol tabanlı kapsam, kimlik kurallarına uyan mükerrer sınıfı, değer farkları) ve değiştirilemez SHA-256 kanıt | Kabul edildi | 25.11.2026 |
| [0015](0015-degisiklik-setleri-git-ile-yayin.md) | Metadata değişiklik setleri Evren'de onaylanır (hazırlayan onaylayamaz), yayın metadata/changes/ dosyasıdır ve Git'ten dağıtılır | Kabul edildi | 25.11.2026 |
| [0016](0016-tek-imaj-ve-gelistirme-yigini.md) | Tek konteyner imajı (uid 10001, salt okunur kök), izlenen göçler (SHA-256 defteri, sürüklenme durdurur), servis giriş rolleri, sırlar yalnızca dosyadan, compose geliştirme yığını ve CI'da uçtan uca yığın sınaması | Kabul edildi | 25.11.2026 |

Yeni karar: `/adr <başlık>` komutu.
