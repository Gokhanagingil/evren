# Kavramlar sözlüğü

- **CI (konfigürasyon öğesi):** Evren'de kaydı tutulan her şey: sunucu, sanal makine, ağ cihazı, uygulama, hizmet.
- **Golden record:** bir CI'ın bütün kaynaklardan birleştirilmiş tek ve güvenilir kaydı.
- **Kaynak:** Evren'e veri gönderen araç (ör. vCenter, Zabbix).
- **Kaynak rolü:** kaynağın gördüğü işin türü: keşif, sanallaştırma, ağ, izleme, uç nokta güvenliği, ITSM.
- **Bağlayıcı:** kaynaktan veriyi çekip Evren'e gönderen küçük program. Kayıtlara kendisi yazmaz.
- **Kimlik çözümleme:** gelen kaydın hangi CI olduğuna karar verilmesi (seri numarası, UUID, FQDN gibi kanıtlarla).
- **Uzlaştırma:** kaynaklar farklı değer bildirdiğinde hangisinin geçerli olacağının kurallarla seçilmesi.
- **Uzlaştırıcı:** kayıtlara yazan tek servis. Elle yapılan değişiklikler de ondan geçer.
- **Eşleştirme kuyruğu:** Evren'in emin olmadığı ve insan kararı beklediği eşleşmeler.
- **Devre kesici:** eksik veya şüpheli bir çalışmada yıkıcı işlemleri (emekliye ayırma, ilişki kaldırma) durduran koruma.
- **Gölge mod:** yeni bir kaynağın kayıtlara yazmadan, yalnızca karşılaştırma için çalışması.
- **Değişiklik seti:** veri modeli değişikliklerinin taslak, onay ve yayın adımlarından geçen paketi.
- **Eskime:** bir CI'ın uzun süredir hiçbir gözlemci kaynak tarafından görülmemesi.
- **Sahiplik onayı:** CI sorumlusunun kaydı düzenli aralıklarla doğrulaması.
- **İlişki:** iki CI arasındaki bağ (ör. uygulama sunucuda çalışır). Etki analizi bunlardan çıkar.
