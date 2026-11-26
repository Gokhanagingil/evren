# ADR-0030: Uygulama ve hizmet katmanı ServiceNow'dan, etki analizi ilişki tiplerinin etki yönüyle

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (U7.3), mimari

## Bağlam

CMDB'nin asıl değeri "bu sunucu düşerse hangi hizmet etkilenir" sorusunu yanıtlamasıdır. CSDM'de bu zincir şöyledir:

- iş uygulaması "Consumes" uygulama hizmeti;
- iş hizmeti ve hizmet sunumu "Depends on" uygulama hizmeti;
- uygulama hizmeti "Depends on" sunucu, veritabanı ve ağ cihazı.

Evren'de bu sınıflar ve ilişki kuralları U4'te kuruldu. Ancak iki eksik vardı. ServiceNow bağlayıcısı yalnızca sunucu ve ağ cihazı tablolarını okuyordu, yani hizmet katmanı hiç gelmiyordu. İlişki tiplerinin hangi ucunun diğerine bağımlı olduğu da tanımlı değildi.

Kurumda iş uygulamaları ve hizmetler keşifle bulunmaz; insanlar tutar ve bugün ServiceNow'dadır. Sunucu ve ağ cihazı ise keşiften gelir. Etki analizi bu iki dünyayı birleştirmek zorundadır.

## Karar

1. **Hizmet katmanı ServiceNow'dan, sys_id korunarak gelir.**
   - Bağlayıcı `cmdb_ci_business_app` ve `cmdb_ci_service` tablolarını (iş, teknik ve uygulama hizmeti, hizmet sunumu; alt sınıflarıyla) okur.
   - Kayıtlar sunucularla aynı ilk yükleme yolundan geçer: aynı sys_id, kimlik çözümlemesi yok (ADR-0011).
   - Eşleme dosyası `connectors/servicenow_cmdb/csdm.mapping.yaml`'dır. Yönetişim alanları ITSM rolüyle yazılır.

2. **İlişkiler `cmdb_rel_ci`'dan yalnızca bağımlılık tipleriyle gelir.**
   - İstemci CI tablolarından önce `cmdb_rel_type` ve `typeIN<bağımlılık tipleri>` süzgeciyle `cmdb_rel_ci` tablosunu okur. İlişkileri ebeveyn kaydına `_relations` olarak ekler.
   - Eşleme motorunda yeni `relations_from` anahtarı vardır.
   - Hedef aynı kaynağın sys_id'sidir; uzlaştırıcı onu kimlik bağından çözer. ServiceNow'daki sunucu keşifle birleştiyse hizmet, golden record'daki sunucuya bağlanır.
   - Sahiplik ve fiziksel bağlantı gibi bağımlılık taşımayan tipler okunmaz; bunlar keşiften gelir.
   - Gerçek ServiceNow'da `cmdb_rel_ci` çok büyük olabilir. Süzgeç sunucu tarafındadır, istemci ilişkileri bellekte ebeveyne göre gruplar.

3. **Etki yönü metadata'dadır (kural 6).** `relationship_types.yaml` dosyasında her tipin `impact` değeri vardır:
   - `parent`: ebeveyn çocuğa bağımlıdır. Örnekler: `Depends on`, `Runs on`, `Consumes`, `Uses`, `Hosted on`, `Virtualized by`, `Registered on`.
   - `child`: çocuk ebeveyne bağımlıdır. Örnekler: `Contains` (veritabanı örneğin içinde), `Cluster of`, `Sends data to`.
   - `none`: bağımlılık yoktur. Örnekler: `Connects to`, `IP Connection`, `Owns`.
   - Doğrulayıcı her tipte bu değeri ister.

4. **Etki analizi.** `GET /api/v1/ci/{id}/impact` iki yönde yürür: **yukarı**, bu CI'a bağımlı olanlar (bu CI düşerse etkilenenler); **aşağı**, bu CI'ın bağımlılıkları.
   - Yürüyüş özyinelemeli SQL'dir. Döngüye girmez, derinlik (varsayılan 6, en çok 10) ve düğüm sayısı (500) sınırı vardır.
   - Her düğümde düzey, ilişki fiili ve aracı CI bulunur. Hizmet katmanı (hizmet, iş uygulaması, iş yeteneği) ayrıca özetlenir; kaç hizmetin ve kaçının üretimde etkilendiği de verilir.
   - Okuma izni olmayan sınıftaki CI gösterilmez, adı sızmaz, yalnızca sayısı döner. Yürüyüş o CI'ın üzerinden devam eder: gizli bir ara katman, okunabilir bir hizmetin etkilendiğini saklamamalıdır.
   - CI panelinde "Etki" bölümü bulunur.

## Sonuçlar

- "Sunucu düşerse hangi hizmet etkilenir" sorusu ServiceNow'daki hizmet haritası ile keşfin altyapısı birleştirilerek yanıtlanır.
- Hizmet katmanının doğruluğu ServiceNow'daki bakıma bağlıdır. Evren bunu sahiplik onayıyla izler (ADR-0027); hizmetler onay kapsamındadır.
- Delta çalışmasında ilişkiler de yalnızca güncellenenlerden gelir. ServiceNow'da silinen ilişki tam çalışmada kalkar.
- Etki yönü ServiceNow'un "Dependency Views" yorumuyla aynı değildir; kurum farklı yorumluyorsa değiştirmek yalnızca metadata işidir.

## Alternatifler

- **Hizmet katmanını Evren'de sıfırdan elle kurmak:** geçiş döneminde iki yerde bakım demektir. Reddedildi. Elle girdi (ADR-0026) eksik halkalar içindir.
- **Bütün `cmdb_rel_ci`'ı okumak:** milyonlarca satır ve sahiplik/fiziksel ilişki gürültüsü getirir. Reddedildi; bağımlılık tipleri yeterlidir.
- **Yönü koda gömmek:** yeni tip veya kurum yorumu kod değişikliği gerektirirdi. Reddedildi.
