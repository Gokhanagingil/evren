# CI bulmak ve incelemek

CI (konfigürasyon öğesi), Evren'de kaydı tutulan her şeydir: sunucu, sanal makine, ağ cihazı, uygulama, hizmet.

## Hızlıca

1. Üst çubuktaki arama kutusuna (Ctrl+K) adın, IP adresinin veya FQDN'in bir parçasını yazın.
2. Sonuçlardan birini seçin; sağda CI paneli açılır.
3. Panelde bir özniteliğe tıklayın: değeri hangi kaynağın verdiğini ve hangi kuralın seçtiğini görürsünüz.

Daha ayrıntılı süzmek için [Konfigürasyon Öğeleri](#/ci) sayfasında sınıf, kaynak ve kapsama göre süzün. Kapsam süzgeçleri hazır sorular sunar: izlenmeyen sunucular, korunmayan sunucular, ServiceNow'da kaydı olmayanlar.

## CI panelinde neler var?

- **Öznitelikler ve kaynakları:** her değerin kaynağı. Turuncu nokta, kaynakların farklı değer bildirdiği anlamına gelir.
- **Etki:** bu CI düşerse hangi hizmetlerin etkileneceği.
- **İlişkiler:** bağlı olduğu ve ona bağlı olan CI'lar, bunları hangi kaynağın bildirdiği.
- **Sahiplik onayı ve Eskime:** kaydın güncel olup olmadığı.
- **Geçmiş bir an:** CI'ın seçtiğiniz tarihteki hâli ve bugünle farkı.
- **Zaman çizelgesi:** ne zaman, hangi kaynaktan, neyin değiştiği.

## İlişki haritası

Paneldeki **İlişki haritası** bağlantısı CI'ın çevresini çizer: ona bağımlı olanlar üstte, bağımlılıkları altta. Bir kutuyu seçip **Merkeze al** ile haritayı o CI'a taşıyabilir, **An** ile geçmiş bir tarihteki ilişkileri görebilirsiniz.

> **İpucu:** Bir sunucunun bakımı planlanıyorsa önce Etki bölümüne bakın; etkilenen üretim hizmetleri orada sayılır.

## Teknik ayrıntı

- [Kimlik çözümleme ve uzlaştırma](belge:03-kimlik-ve-uzlastirma)
- [Veri modeli](belge:02-veri-modeli)
- [İlişki yaşam döngüsü ve zaman içinde sorgu (karar kaydı)](belge:adr/0031-iliski-yasam-dongusu-ve-zaman-icinde-sorgu)
