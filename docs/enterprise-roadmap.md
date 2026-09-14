# Kurumsallaşma yol haritası — 14 Eylül 2026

**İkinci paket / 0.6.0:** tekrarlanabilir doğrudan/transitif sürüm lock'ları,
varsayılan dry-run ve açık apply ile sınırlı temizlik, Online Backup API CLI,
manifest/integrity/FK denetimi, kalıcı karantinalı geri yükleme, ortak yerel doğrulama
ve Windows/Linux GitHub Actions workflow'u eklendi. Yeni tek tablo `recovery_guard`,
migration `20260914_0005`. Son ZIP temiz kurulum kanıtı [0.6.0 raporunda](acceptance-enterprise.md);
[0.5.0 kanıtları](acceptance-enterprise-0.5.0.md) tarihsel olarak korunur.

Bu paket P1 veri/teslim satırının bir bölümünü tamamlar. Gerçek kurum saklama süreleri,
outbox/audit arşiv stratejisi, hacim/performans, kurumsal RTO/RPO, servis/watchdog,
tarayıcı/Excel/ağ/SMTP kabulü, bağımlılık güvenlik taraması ve uzakta CI çalışması
henüz tamamlanmış sayılmaz. Aşağıdaki başlangıç tablosu 0.4.0→0.5.0 planının tarihsel
bağlamını korur; 0.6.0 durumu için bu paragraf ve kabul raporu esas alınır.

Başlangıç: temiz Git `1dffe65`, uygulama 0.4.0, şema `20260912_0003`.
Kodda FastAPI, SQLAlchemy ve SQLite; zamanlayıcı Python `asyncio` ile yazılmış
sabit gecikmeli döngüdür. `requirements.txt` içinde APScheduler yoktur; kurulu
bir APScheduler sürümüne dayanan koordinasyon varsayılmamıştır. Başlangıçta izleme
duraklatılır, SQLite dosyası süreç kilidiyle tek uygulama sürecine ayrılır.

**Paket sonrası durum:** 0.5.0'da P0 outbox/retry, bakım/susturma, bitiş uzlaştırması,
izleme sağlığı ve Türkçe panel uygulandı. 162 test, yerel mock HTTP senaryosu ve
geçici DB migration/restore doğrulandı; [0.5.0 kabul raporu](acceptance-enterprise-0.5.0.md).
Tablonun mevcut durum sütunu inceleme başlangıcını, hedef/kabul sütunları paket
sırasını gösterir. P1/P2 tamamlandı sayılmaz.

Müşteri sayısı, cihaz sayısı, saklama süresi, RTO/RPO ve erişilebilirlik hedefi
bilinmiyor. İlk paket için **tek kurum, tek uygulama süreci, yerel SQLite ve
ölçülmemiş küçük envanter** varsayılmıştır. Bu bir kapasite veya SLA taahhüdü değildir.
Birden fazla müşteriyi merkezi sistemden izlemek ayrı güvenlik ve işletim hedefidir;
bu sürüm o kullanım için hazır kabul edilmez.

P0: bu oturumdaki 0.5.0 paketi; P1: tek kurum pilotunu kapatan sonraki sınırlı
paketler; P2: ihtiyaç ve ölçüm sonrası mimari dönüşüm. Çok müşteri gerekiyorsa
müşteri ayrımı P2'den o dağıtımın **yayına çıkış önkoşuluna** yükselir.

| Alan | Mevcut durum | Eksik / hedef | Öncelik | Bağımlılık | Kabul ölçütü |
|---|---|---|---|---|---|
| Alarm operasyonu | Kalıcı open/resolved/closed, görüldü; bildirim yok | Transactional outbox, sınırlı retry, bakım ve süreli susturma; sonra eskalasyon | P0; eskalasyon P1 | Alarm işlemi, ayrı gönderici, UTC | Alarm/outbox birlikte rollback; DB deduplikasyonu; sıra, crash/retry, kapalı/mock izolasyonu; bakım sonunda taze veriyle tek özet; gerçek SMTP ayrı yetkili kabul |
| İşletim | `/health`, tek süreç kilidi, başlangıçta duraklatma | Zamanlayıcı/veri/kuyruk sağlığı, güvenli JSON log; merkezi toplama, servis ve bağımsız watchdog | P0 görünürlük; P1 servis | Servis hesabı, log hedefi, kurum işletim politikası | Panel teknik hata/yanıtsızlık/eski veri/duraklamayı ayırır; servis çökmesi dışarıdan algılanır; kontrollü stop/start ve geri yükleme tatbikatı |
| Kimlik ve yetki | Admin/viewer, CLI hesap yönetimi, sunucu oturumu, CSRF/audit | Operatör rolü, hesap/oturum ekranı; IdP ile SSO/MFA | P1; SSO P2 | Rol matrisi, IdP seçimi | Operatör yalnız tanımlı işlemleri yapar; backend negatif testler, son admin koruması, oturum iptali; SSO issuer/audience/state/nonce ve MFA politikası doğrulanır |
| Kapasite ve süreklilik | SQLite, sınırlı eşzamanlı probe; ölçüm yok | Yük testi; gerekirse PostgreSQL, ayrı zamanlayıcı/kontrol çalışanları | P1 ölçüm; P2 dönüşüm | Cihaz/adres sayısı, aralık, SLA, RTO/RPO | Sentetik yükte p95 kontrol gecikmesi/kuyruk yaşı/DB kilit süresi ölçülür; geçiş eşikleri belirlenir; taşıma ve geri dönüş tatbikatı; tek görev sahibi, lease/fencing, tekrar işleme ve hız sınırı testleri |
| Müşteri ve lokasyon | Serbest lokasyon metni; IP global benzersiz | Lokasyon/ağ kapsamı envanteri; merkezi çok müşteride üyelik ve uçtan uca veri/ağ ayrımı | Tek kurum P1; çok müşteri dağıtım önkoşulu | Üyelik modeli, erişim bağlantıları, tehdit modeli | Aşağıdaki kapsam matrisi ve çapraz müşteri negatif testleri tam geçer; aynı özel IP iki yetkili ağ kapsamında kullanılabilir |
| Ağ görünürlüğü | Allowlist ICMP ve ağsız mock | Salt okunur SNMPv3 authPriv, arayüz/link/trafik/kaynak ölçümleri; uzak toplayıcı | P2 | Yazılı ağ izni, salt okunur hesaplar, secret store, kapsam | Mock sözleşme testleri; yetkili labda sayaç taşması/reset ve interface kimliği; mTLS toplayıcı kimliği, kapsamı ve çevrimdışı backlog sınırı |
| Veri ve teslim | Alembic, yedek/restore belgesi, 0.4.0 ZIP kanıtı | Saklama/temizleme, yeni restore tatbikatı, CI, sürümlü paket ve lock/SBOM/secret taraması | P1 | Saklama/RPO, CI sağlayıcısı | Yeni geçici DB'de migration ve restore eşitliği; CI ağsız test/lint/migration; paket izin listesi ve SHA-256; gizli bilgi ve bağımlılık taraması; eski ZIP korunur |

## Paket sınırları ve sıra

1. **0.5.0 / P0:** güvenilir bildirim, bakım/susturma, izleme sağlığı ve Türkçe panel.
   Varsayılan mock ölçüm ve kapalı bildirim; başlangıçta duraklatma değişmez.
2. **Tek kurum P1:** kalan tarayıcı/CSV/Excel kabulü, yazılı izinle şirket/lab ağı,
   yük ve saklama ölçümü, servis/merkezi log/dış watchdog, restore tatbikatı, CI.
   Gözetimsiz kullanım için yeniden başlatmadan sonra otomatik devam etme ayrı ve
   açık bir politika değişikliği olmalı; yanlışlıkla ağ taraması başlatmamalı.
3. **Kimlik P1:** operatör ve kullanıcı/oturum ekranları, ayrı kabul paketi.
4. **Çok müşteri veya kapasite P2:** aşağıdaki sınırlar tasarlanıp test edilmeden
   ikinci müşteri alınmaz; SNMP/toplayıcı ve SSO ayrı sınırlı paketlerdir.

## Çok müşteri kabul kapısı

Yalnız `devices.customer_id` yeterli değildir. Müşteri seçimi cookie/header/formdan
gelse de sunucu oturumdaki kimliğin güncel üyeliğini doğrular; istemci tenant değeri
yetki kanıtı değildir. Varsayılan bağlamsız erişim reddedilir. Kaynak sahipliği ve
işlem yetkisi birlikte denetlenir. [OWASP çok kiracılı güvenlik rehberi](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html).

Sonraki paketin zorunlu kapsam matrisi: cihaz API'si; ölçüm/geçmiş/grafik; rapor ve
CSV; alarm/görüldü/bakım/susturma; bildirim hedefi/outbox; audit; arka plan kontrol,
retry, export ve temizleme işleri. Her yol için A/B müşterisi, üyelik iptali,
değiştirilmiş ID, yetkisiz müşteri seçimi ve eksik bağlam negatif testleri gerekir.
Kuyruk ve cache anahtarları müşteri/ağ kapsamı taşır; worker bu kapsamı yeniden
doğrular. Müşteriler arası kapasite tüketimi için kota/hız sınırı uygulanır.

IP benzersizliği `(network_scope_id, canonical_ip)` düzeyinde tasarlanır; kapsamın
müşteri/lokasyon ilişkisi sunucuda doğrulanır. İki farklı müşterinin `10.0.0.1`
cihazları çakışmaz; aynı kapsamda mükerrer adres reddedilir. Bir müşteri/lokasyon
etiketi o ağa bağlantı sağlamaz. Uzak erişim ayrıca yetkilendirilmiş VPN/tünel veya
kimliği ve ağ kapsamı doğrulanan toplayıcı gerektirir; allowlist her katmanda korunur.

## PostgreSQL ve çalışan ayrımı

Önce yük sonuçları ve işletim ihtiyacıyla karar verilir. UTC metin alanları,
SQLite'a özgü `BEGIN IMMEDIATE`, tarih default'ları ve kısmi indeksler dahil SQL
uyumluluğu ele alınır. Taşıma: tutarlı SQLite yedeği, yeni PostgreSQL şeması,
FK sıralı aktarım, satır sayısı/hash ve zaman/tip kontrolleri, sequence ayarı,
salt okunur karşılaştırma, kontrollü yazma kesintisi ve bağlantı değişimi.
Geri dönüş: yeni yazmalar başlamadan eski yedeğe dönüş; başladıysa PostgreSQL
yazmalarını kaybetmeden ters aktarım/uzlaştırma tatbikatı ve açık RPO gerekir.
İki veritabanına denetimsiz çift yazma yapılmaz.

RLS seçilirse gerçek PostgreSQL üzerinde uygulamanın kullandığı rolün superuser,
`BYPASSRLS` veya politikayı atlayan tablo sahibi olmadığı doğrulanır. Gerekirse
`FORCE ROW LEVEL SECURITY`; transaction kapsamlı tenant bağlamı, havuz bağlantısı
yeniden kullanımı, eksik/yanlış bağlam, SELECT/INSERT/UPDATE/DELETE ve arka plan
işleri test edilir. SQLite testleri RLS kanıtı sayılmaz.
[PostgreSQL satır güvenliği](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

Uvicorn worker sayısını artırmak koordinasyon çözümü değildir. Mevcut `asyncio`
döngüsü tek sahip olarak kalır. Ayrım paketinde iş kimliği, atomik claim,
süreli lease/fencing, süreç çökmesi, geç sonuç, per-device sıralama, toplam ve
kapsam başına hız sınırı, idempotent sonuç/outbox ve retry politikası birlikte
tasarlanır. APScheduler'a geçilecekse sürüm ayrıca sabitlenir: **3.x ortak job store
süreçler arası koordinasyon sağlamaz**.
[APScheduler 3.x FAQ](https://apscheduler.readthedocs.io/en/3.x/faq.html).

Bildirim atomikliği için [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html),
güvenli işlem/olay logları için [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
referans alınmıştır. SMTP kabulü gelen kutusu teslimi değildir; tam olarak bir kez
teslim garantisi yoktur.
