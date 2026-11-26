# 13 · CMDB evreni — gerekli yetenekler ve sıra

Bu belge, kurumsal bir CMDB evreninin uçtan uca ihtiyaç duyduğu yetenekleri bir mimar ve konfigürasyon yönetimi uzmanı gözüyle sıralar. Dayanak çerçeveler şunlardır: ITIL 4 hizmet konfigürasyon yönetimi, ISO/IEC 20000-1 8.2.6, ServiceNow CMDB ve CSDM.

Her madde için bugünkü durum ve plan bağı verilir. Yüzdeler `apps/web/content/components.json`'dan gelir. Sıra bağımlılığa göredir: **önce model, sonra akıl, sonra akış**; çevre işler en sonda.

> İlke: CMDB bir veri deposu değil, **güvenilir bir karar zeminidir**. Bir CI'ın ne olduğu, kimin sorumluluğunda olduğu, neye bağlı olduğu ve bu bilginin nereden geldiği her an açıklanabilmelidir.

## A. Veri modeli — CMDB'nin kendisi (U4)

| # | Yetenek | Neden | Bugün | Plan |
| --- | --- | --- | --- | --- |
| A1 | **Sınıf kütüphanesi** (ServiceNow temel uyumlu): donanım, uç nokta, ağ, depolama, sanallaştırma, bulut, Kubernetes, yazılım/ara katman, veritabanı, mantıksal ağ (IP ağı, VLAN, DNS), raf | Bağlayıcılar ancak hedef sınıf varsa doğru yazar. Sınıf yoksa veri yanlış sınıfa düşer (ör. dizüstü bilgisayarın "sunucu" olması) | 36 sınıf; uç nokta, depolama, bulut, Kubernetes, ara katman yazılımı yok | U4, F0-17 |
| A2 | **CSDM katmanları**: iş yeteneği, iş uygulaması, iş süreci, iş/teknik hizmet ve sunumları, uygulama hizmeti, dinamik CI grubu, bilgi nesnesi | Altyapıyı iş hizmetine bağlayan omurga. Etki analizi ve GRC kontrolleri buna dayanır | Hizmet, iş uygulaması, sunum var; yetenek, süreç, dinamik grup, bilgi nesnesi yok | U4, F0-17 |
| A3 | **Sahiplik ve yönetişim öznitelikleri**: sahip, yöneten, destek/onay grubu, ortam, iş kritikliği, maliyet merkezi, sertifikasyon durumu | "Bu CI kimin?" sorusunun cevabı. Keşif araçları bunları ezemez (kural 5) | Sahip ve gruplar var; maliyet merkezi ve sertifikasyon yok | U4, F1-17 |
| A4 | **Yaşam döngüsü standardı**: CSDM aşama ve aşama durumu; operasyonel ve kurulum durumu eşlemesi | Emekliye ayırma, eskime ve raporların ortak dili | Alanlar var, değer listesi yok | U4, F0-17 |
| A5 | **İlişki modeli**: ilişki tipleri ve izinli sınıf çiftleri | ServiceNow yalnız önerir; Evren zorlarsa "sunucu, uygulamada çalışır" gibi ters ilişki oluşmaz (daha iyisi) | 14 tip; sınıf çifti kuralı yok | U4 (tanım), U7 (zorlama) |
| A6 | **Referans verileri**: kişi, grup, konum, şirket, departman, ürün modeli kataloğu, maliyet merkezi | Sahiplik ve model bilgisi bunlarsız serbest metne döner | 6 referans tablosu; maliyet merkezi yok; ServiceNow'dan yükleme yok | U4, F0-08 |
| A7 | **Sınıf başına kimlik kuralları** (IRE) ve bağımlı CI kuralları | Mükerrerin kökü. Her sınıfın kimliği farklıdır (seri, UUID, hesap kimliği…) | Donanım, VM, uygulama, adaptör var | U4, U7, F1-07 |
| A8 | **Sınıf ağacı ve alan gezgini**, yönetişimli şema evrimi (değişiklik setleri) | Model görünür ve denetimli olmalı | Değişiklik setleri var; gezgin yok | U4 |

## B. Akıl katmanı — anlamlandırma (var olan çekirdek + U6–U8)

| # | Yetenek | Bugün | Plan |
| --- | --- | --- | --- |
| B1 | Normalizasyon, kimlik çözümleme, çelişki denetimi, eşleştirme kuyruğu, geri alınabilir birleştirme | %80–95 | U7 (bağımlı CI), U10 (kalibrasyon) |
| B2 | Rol tabanlı uzlaştırma, veri tazeleme, öznitelik başına kaynak bilgisi | %75–90 | U7 |
| B3 | Devre kesici (kısmi, eksik, terk edilmiş ve delta çalışma) | %90 | U7 |
| B4 | **Eskime ve emekliye ayırma işletimi** (devre kesici onayıyla) | %15 | U7, F1-06 |
| B5 | **Elle girdi**: CI oluşturma, ilişki, CSV; hepsi uzlaştırmadan geçer | %45 | U6 |
| B6 | **Sahiplik sertifikasyonu**: CI sahibinin periyodik onayı ("bu CI hâlâ benim ve doğru") | yok | U6, F1-17 |
| B7 | **Uyum denetimi (beklenen–gerçek)**: ör. "üretimdeki her sunucunun destek grubu, izlemesi ve uç nokta koruması olmalı" | Kapsam kartları kısmen | U8, F1-18 |
| B8 | Yetkisiz değişiklik (onaylı Change penceresi dışı) | %10 | U5, F1-04 |
| B9 | Kalite ve sağlık ölçümü (model ve veri oturduktan sonra) | %50 | U8, F1-09 |
| B10 | Yapay zekâ önerileri (insan onaylı, kayıtlı) | %0 | F3 |

## C. Veri akış ortamı (U5)

| # | Yetenek | Bugün | Plan |
| --- | --- | --- | --- |
| C1 | **Bağlayıcı çerçevesi**: çalıştırıcı, sayfalama, yeniden deneme, zamanlama, kimlik bilgisi, "şimdi çalıştır" | %15 | U5 |
| C2 | Altı kaynak bağlayıcısı + kaydedilmiş örnek yanıtla test | %5–35 | U5, U10 |
| C3 | Alım geçidi, Kafka, DLQ, outbox | %80–100 | U10 (üretim güvenliği) |
| C4 | Ham yük arşivi ve yeniden işleme | %0 | U5, F1-14 |
| C5 | Kaynak yönetimi ekranı: kaynak sağlığı, çalışma geçmişi, bağlayıcı kimlikleri | kısmen | U5 |

## D. Tüketim (U7–U9)

| # | Yetenek | Bugün | Plan |
| --- | --- | --- | --- |
| D1 | CI 360°: öznitelik, kaynak, geçmiş, ilişkiler, sahiplik | kısmen | U7 |
| D2 | **Hizmet haritası ve etki analizi** (CSDM üzerinden yukarıdan aşağıya) | yok | U7 |
| D3 | **Zaman içinde sorgu**: "bu CI 3 ay önce nasıldı?", değişiklik zaman çizelgesi (daha iyisi) | Geçmiş var, as-of sorgu yok | U7, F1-19 |
| D4 | Arama (var), raporlama ve dışa aktarma | %75 | U7 |
| D5 | API: yerel REST (var), ServiceNow uyumlu Table/IRE, olay/webhook, MCP | %0–50 | U9, F2 |
| D6 | GRC Platformu beslemesi | %15 | F2-08 |

## E. ServiceNow geçişi (U9)

| # | Yetenek | Bugün | Plan |
| --- | --- | --- | --- |
| E1 | ServiceNow ilk yükleme (sys_id korunur) ve referans verileri | %35 | F0-08, U10 |
| E2 | sn-sync replika (IRE); ServiceNow'daki elle değişikliklerin geri çekilmesi | %0–5 | U9, F2 |
| E3 | CI seçici (ServiceNow formunda Evren araması) | %0 | F3-01 |

## F. Platform ve yönetişim (U8, U10)

| # | Yetenek | Bugün | Plan |
| --- | --- | --- | --- |
| F1 | Kimlik ve yetki: Entra, RBAC + ABAC, görevler ayrılığı, rol–yetki matrisi | %70–90 | U8, U10 |
| F2 | Denetim izi, SIEM aktarımı | %70 | U8, F1-16 |
| F3 | Gözlemlenebilirlik (metrik, gecikme, uyarı) | yok | U8, F1-15 |
| F4 | **Ölçek ve yük testi** (40.000 sunucu + 100.000 ağ cihazı hedefi) | yok | U8, F1-20 |
| F5 | Yedekleme, felaket kurtarma, yüksek erişilebilirlik | %5 | U10, F2-09 |
| F6 | Tedarik zinciri, sırlar, en az yetki | %55–95 | U10 |

## Uygulama sırası

1. **U4 — Model:** A1–A8. Bağlayıcıların yazacağı sınıflar, CSDM omurgası, yaşam döngüsü dili, ilişki kuralları ve bunları gösteren sınıf gezgini.
2. **U5 — Akış:** C1–C5. Gerçek kaynak istemcileri, kayıtlı örneklerle; ham yük arşivi.
3. **U6 — Elle girdi ve sahiplik:** B5, B6.
4. **U7 — İlişkiler ve yaşam döngüsü:** B4, A5'in zorlanması, A7 bağımlı CI, D1–D4.
5. **U8 — Yönetim, uyum, gözlemlenebilirlik ve ölçek:** B7, B9, F1–F4.
6. **U9 — ServiceNow replika ve MCP:** E2, D5.
7. **U10 — Canlı kabul:** dış girdilerle; F5.

## Bilinçli sınırlar

- ServiceNow sınıf ve alan adları kurum örneğinden doğrulanmadan kesin sayılmaz (`verify: true`, F0-07). Doğrulama `tools/sn_metadata_export.py` ile salt okunur yapılır.
- Varlık yönetimi (alm_asset, sözleşme, lisans) bu evrenin dışındadır. CI → varlık bağı için yalnızca `asset_tag` tutulur.
