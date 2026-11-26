# ADR-0022: ServiceNow referans verileri bağlayıcı ve uzlaştırıcı yolundan yüklenir

- Durum: Kabul edildi
- Tarih: 26.11.2026
- Karar verenler: Proje sahibi (F0-08 kapsamı), mimari

## Bağlam

ServiceNow CI kayıtlarındaki referans alanları (`support_group`, `owned_by`, `managed_by`, `location`, `company`, `cost_center`, `model_id`) sys_id olarak gelir (ADR-0020 §10). API bu değerlerin adını `ref.*` tablolarından çözer. Ancak bu tablolar yalnızca demo yükleyiciyle doluyordu. Gerçek yüklemede arayüz sys_id gösterir. Ayrıca yönetişim akışları (sahiplik onayı, ABAC "kendi grubum") grup ve kişi verisine dayanır.

Plandaki yazar `sn-sync` servisidir (`evren_app_sync`, U9). O servis replika yönünü (Evren → ServiceNow) de taşıyacak. Ama F0-08 ilk yüklemesi şimdi gerekiyor ve ServiceNow okuma yolu zaten bağlayıcıda (Table API, anahtar sıralı sayfalama, simülatör, geçit, Kafka). Okuma için ikinci bir yol açmak, ikinci bir kimlik bilgisi, ikinci bir sayfalama kodu ve ikinci bir denetim yüzeyi demektir.

## Karar

1. **Referans tabloları tek listede tanımlanır:** `evren_core/reference.py` içindeki `REFERENCE_TABLES`. Her tablonun izinli sütunları ve türü (sys_id referansı, metin, mantıksal) burada tanımlıdır. Bağlayıcının çektiği alanlar, eşleme dosyasının doğrulaması ve uzlaştırıcının yazdığı sütunlar bu listeden gelir. Kapsam şu tablolardır:
   - `sys_user`
   - `sys_user_group`
   - `cmn_location`
   - `core_company`
   - `cmn_department`
   - `cmn_cost_center`
   - `cmdb_model`

2. **Kişisel veri en azdır.** `sys_user` yalnızca `user_name`, `name`, `email`, `department` ve `active` sütunlarını taşır. Telefon, unvan, yönetici ve adres alanları istenmez (KVKK veri en aza indirme). Grup üyeliği (`sys_user_grmember`) bu adımda yoktur; ABAC ile gelir (U8).

3. **Bağlayıcı:**
   - ServiceNow istemcisi CI tablolarından sonra referans tablolarını da **aynı çalışmada** çeker. Sayfalama ve sıralama güvencesi CI tablolarıyla aynıdır.
   - Her tablo için yalnızca kendi alanları istenir (`sysparm_fields`).
   - Eşleme dosyası `connectors/servicenow_cmdb/reference.mapping.yaml` kaydı tablo adını sınıf ipucu olarak taşır. `class_hint` değeri `sys_user_group` gibi tablo adıdır.
   - Sunucu eşlemesi artık yalnızca CI tablolarındaki kayıtları alır (`require`).

4. **Yetki role bağlıdır (ADR-0009):** Referans verisini yalnızca `reference_source: true` işaretli rol yazar; bugün bu rol **ITSM**'dir. Kaynağın yaşam döngüsü etkin veya devrediliyor olmalıdır. Gölgedeki ServiceNow kaynağı referans yazmaz; kaydı sayılır.

5. **Uzlaştırıcı yazar, ama CMDB'nin değil referansın yazarı olarak:**
   - Referans kaydı kimlik çözümlemesine ve CI uzlaştırmasına girmez.
   - `ingest.source_record`'a yine yazılır; çalışma sayaçları, içerik özeti ile değişmeyen kaydı atlama ve çalışma kapanışı CI kayıtlarıyla aynıdır.
   - Satır aynı sys_id ile `ref.<tablo>`'ya eklenir veya güncellenir. Sütun düzeyi yetkiyle (göç 0020) uzlaştırıcı yalnızca listedeki sütunları yazabilir: `source`, `entra_oid`, `entra_group_id` ve grup üyeliği yazılamaz.
   - Entra'dan veya Evren'den gelen satır (`source <> 'servicenow'`) ezilmez.
   - Kayıt silinmiş gelirse `active` sütunu olan tabloda satır pasifleşir; satır fiziksel olarak silinmez.
   - Değer türü uymayan kayıt yazılmaz ve sonucu `reference_invalid` olur. Bozuk sys_id ve 1000 karakteri aşan metin bu türdendir.

6. **Eksik kayıt:** Tam listede görülmeyen referans kaydı, CI kayıtları gibi yalnızca `source_record.status='missing'` olarak işaretlenir. Referans satırı pasifleştirilmez. Eskime kararı U7'de yaşam döngüsü kurallarıyla verilir.

## Değerlendirilen seçenekler

- **sn-sync servisinin şimdi kurulması (`evren_app_sync` doğrudan yazar):** İkinci okuma yolu, ikinci kimlik bilgisi ve ikinci sayfalama gerektirir; devre kesici ve çalışma geçmişi dışında kalır. sn-sync replika yönü için U9'da kurulacak; referans okuması bağlayıcıda kalır. Reddedildi.
- **Referans kayıtlarını ayrı bir Kafka konusuna ve ayrı tüketiciye göndermek:** Ayrı rol ve ayrı tüketici sayaçları getirir. Ama aynı çalışmanın kayıtları iki konuya bölünür, kapanış sayımı karmaşıklaşır. Reddedildi; tek konu, kayıt türü sınıf ipucundan.
- **Uzlaştırıcıya `ref` şemasında genel yazma yetkisi:** Sütun düzeyi yetki aynı işi daha dar yapar. Reddedildi.
- **Referans adlarını CI özniteliğine kopyalamak (görünen değer):** ServiceNow modeline aykırıdır (referans sys_id taşır), ad değişince bütün CI'lar güncellenir. Reddedildi.

## Sonuçlar

- **Olumlu:**
  - İlk yüklemeden sonra arayüz ve API grup, kişi, konum, şirket ve maliyet merkezi adlarını gösterir.
  - Referans verisi de çalışma geçmişinde, devre kesicide ve "şimdi çalıştır"da görünür.
- **Olumsuz:** ServiceNow çalışmasının kayıt sayısı artar; kullanıcı tablosu büyük kurumlarda on binlerce satırdır. İçerik özeti değişmeyen kaydı atladığı için sonraki çalışmalar ucuzdur.
- **Riskler:**
  - Kayıtlar Kafka'da anahtar bölümlerine dağıldığı için referans ile CI kaydının sırası garanti değildir. Referans adı CI'dan sonra gelebilir. API adı her okumada çözdüğü için bu yalnızca kısa süreli bir görünüm farkıdır.
  - Aynı `user_name` farklı sys_id ile Evren'de varsa (ör. demo verisi) satır yazılmaz ve sonuç `reference_conflict` olur. Bu durum gerçek kurulumda beklenmez.

## Güvenlik ve uyum etkisi

- Tehdit modeline iki satır eklenir:
  - Kişisel veri en azdır.
  - Referans yolundan CMDB'ye, kimlik eşlemesine veya Entra alanlarına yazılamaz (sütun yetkisi testli).
- ServiceNow entegrasyon hesabı bu tablolar için salt okunur `read` ACL'ine ihtiyaç duyar. ServiceNow'da ACL değiştirilmez; hesaba var olan okuma rolü verilir (kural 11).

## Doğrulama

- **Birim:**
  - Değer türü denetimi (sys_id, metin sınırı, mantıksal).
  - Eşleme dosyasının yalnızca `REFERENCE_TABLES` sütunlarını üretmesi.
  - İstemcinin tablo başına yalnızca kendi alanlarını istemesi.
- **PostgreSQL:**
  - Uzlaştırıcı referans satırı yazar ve adlar CI ayrıntısında çözülür.
  - İkinci çalışma değişmeyen kaydı atlar.
  - Gölge kaynak yazmaz.
  - Evren kaynaklı satır ezilmez.
  - Silinen kayıt pasifleşir.
  - Bozuk kayıt yazılmaz.
  - Uzlaştırıcı `source`/`entra_oid` yazamaz.
- **Yığın duman testi:** ServiceNow simülatörünün referans tabloları geçit ve Kafka üzerinden yüklenir; CI ayrıntısında destek grubu adı görünür.
