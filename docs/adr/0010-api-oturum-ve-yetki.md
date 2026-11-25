# ADR-0010: API oturumu, yetki ve karar akışı

- Durum: Kabul edildi
- Tarih: 25.11.2026

## Bağlam

F0-09 ile Evren'in ilk dış arayüzü (REST API + arayüz sunucusu) açıldı. Gereksinimler: Entra ID ile kurumsal oturum (GRC Platformu ile aynı desen), RBAC + alan hassasiyeti + görevler ayrılığı (docs/07), golden record'a tek yazanın uzlaştırıcı olması (kural 1), GRC Platformu ile ortak sözleşme (`idempotency_key`, `provenance`, `inconclusive`).

## Karar

1. **Oturum:** Entra ID OIDC yetkilendirme kodu + PKCE, MFA kimlik doğrulama bağlamı zorunlu. Belirteçler sunucudan çıkmaz. Oturum, Fernet ile şifreli ve imzalı, süreli bir `__Host-` çerezidir (sunucuda oturum tablosu yok). `dev` modu yalnızca geri döngü adresinde açılır.
2. **Roller:** Entra uygulama rolleri (`Evren.*`) IAM rollerine eşlenir; buna `iam.principal_role` içindeki kullanıcı ve grup atamaları eklenir. Bileşik rol kümesi girişte görevler ayrılığı kurallarıyla denetlenir; çatışmada giriş reddedilir.
3. **Yetki:** `iam.permission` + rol kalıtımı + sınıf kalıtımı; `deny` kazanır; izin verilen en yüksek hassasiyetin üstündeki alanlar yanıta konmaz. Koşullu (ABAC) izinler koşul değerlendirmesi gelene kadar verilmez (güvenli varsayılan). Girdi doğrulaması yetki denetiminden önce yapılır.
4. **Yazma:** API golden record'a ve kimlik bağına yazamaz (veritabanı yetkisiyle zorlanır). Kuyruk kararı `match_candidate` karar sütunlarına + `events.outbox`'a yazılır; uzlaştırıcı `apply_decisions` ile uygular. Yazma istekleri `idempotency_key` ister.
5. **CSRF:** Özel başlık (`X-Evren-Request: 1`) + aynı köken + `SameSite=Strict`; CORS yok.
6. **Arayüz modları:** Arayüz `content/backend.json` ile API modunu (FastAPI) veya demo modunu (statik sunucu) seçer; ekranlar aynı veri biçimini kullanır.

## Değerlendirilen seçenekler

- **Sunucu tarafında oturum tablosu:** iptal kolaylığı sağlar; ancak ek tablo ve temizleme gerektirir. Oturum süresi 8 saat ve Entra koşullu erişim tek oturum açma noktası olduğu için ilk sürümde şifreli çerez yeterli görüldü. Anında iptal gerekirse oturum kimliği + iptal listesi eklenir.
- **API'nin karar anında kimlik bağını yazması:** daha az hareketli parça; ancak kural 1'i ihlal eder ve API rolüne kimlik bağı yazma yetkisi gerekir.
- **Bearer belirteci (SPA):** tarayıcıda belirteç saklamayı gerektirir; XSS etkisini büyütür.

## Sonuçlar

- **Olumlu:** Golden record yazımı tek noktada kalır; kararlar insan kimliğiyle izlenir; yetki dışı alanlar sunucuda süzülür; Entra akışı ağsız testlerle (sahte kimlik sağlayıcı) doğrulanır.
- **Olumsuz:** Kararın etkisi uzlaştırıcı çalışınca görünür (asenkron). Oturum iptali çerez süresine bağlıdır.
- Canlı kabul için kurumun Entra uygulama kaydı (kiracı, istemci, uygulama rolleri, MFA bağlamı) gerekir.
