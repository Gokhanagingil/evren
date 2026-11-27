# Sistemin sağlığını izlemek

[Sistem Durumu](#/sistem-durumu) sayfası Evren'in bütün parçalarını tek ekranda gösterir. Platform operatörü, Veri sorumlusu ve Denetçi görür.

## Durumları okumak

- **Çalışıyor:** sorun yok.
- **Dikkat:** çalışıyor ama ilgilenilmesi gereken bir şey var; kutudaki not ne yapılacağını söyler.
- **Sorun var:** bir parça çalışmıyor veya veri akmıyor.
- **Bu kurulumda yok:** o parça bu ortamda kurulu değil veya hiç başlatılmadı.

## Sık görülen durumlar

- **Uzlaştırıcı yanıt vermiyor:** elle yapılan değişiklikler ve kuyruk kararları uygulanmaz, sırada bekler. Servisi yeniden başlatın.
- **Kafka'ya ulaşılamıyor:** kaynaklardan veri alınamaz. Ağ ve Kafka kümesini denetleyin.
- **Dizin geride:** arama eski sonuç gösterebilir. Dizinleyiciyi denetleyin; gerekirse tam yeniden dizinleme yapın.
- **Devre kesici açık:** bir kaynağın son çalışması eksik veya şüpheli. O çalışmada yıkıcı işlem yapılmadı; [kaynağı](#/kaynaklar) inceleyin.

## Teknik ayrıntı

- [Operasyon ve ölçek](belge:12-operasyon-ve-olcek)
- [Sistem durumu (karar kaydı)](belge:adr/0032-sistem-durumu)
