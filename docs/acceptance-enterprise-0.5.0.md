# 0.5.0 ilk kurumsallaşma paketi — 13 Eylül 2026

Doğrulanan kapsam: **tek kurum / tek süreç / SQLite / ağsız mock ölçüm ve bildirim**;
transactional outbox, bakım, süreli susturma, sağlık API'si ve panel entegrasyonu.
Gerçek SMTP, şirket ağı ve etkileşimli tarayıcı kabulü yapılmadı. Bu rapor genel
kurumsal üretim hazır oluşu, kapasite veya erişilebilirlik garantisi vermez.

## Kaynak ve ortam

- Başlangıç Git: temiz `main`, `1dffe65`; mevcut değişiklik/çözülmemiş dosya yoktu.
  Stage, commit, push veya yayın yapılmadı.
- Python 3.12.12, SQLite 3.50.4; FastAPI 0.141.1, SQLAlchemy 2.0.52,
  Alembic 1.19.2, Uvicorn 0.52.4; pytest 9.1.1 ve Ruff 0.16.7.
- Mevcut çalışan `.venv` kullanıldı; yeni bağımlılık eklenmedi. Gerçek `.env`,
  mevcut veritabanları ve `.venv.broken` değiştirilmedi. Migration/restore testleri
  pytest geçici dizinlerinde; demo/HTTP yardımcıları benzersiz `build` dosyalarında.
- Başlangıç testleri: **126 passed, 1 warning, 53.34 sn**. Eski üç migration
  değişmedi; yeni head **`20260913_0004`**. Alembic `env.py` yalnız uygulama
  logger'larının migration sırasında kapatılmasını önleyecek şekilde güncellendi.
- Eski doğrulanmış ZIP: `dist/AvITData-delivery-20260912-b682eb9d.zip`, SHA-256
  `6e70af691ac7e7649c448036b002071d29d7902834c1916120f0d70206015ddd`.
  Bu değer eski verification JSON'ıyla eşleşti. Eski ZIP 0.4.0 kanıtıdır.

## Gerçekten çalıştırılan kontroller

| Kontrol | Gerçek sonuç / kanıt |
|---|---|
| Tam regresyon | `.venv\Scripts\python.exe -m pytest -q`: **162 passed**, 85.64 sn, 1 mevcut Starlette/AnyIO deprecation uyarısı |
| Yeni arıza testleri | `tests/test_enterprise.py`: 36 parametrik senaryo; gerçek ağ/ping çağrıları testte engellenir, SMTP protokolü sahte stream'lerle sınanır |
| Atomiklik/deduplikasyon | Enqueue sonrası hata bütün ölçüm/alarm/outbox'ı rollback eder; aynı geçiş eklemesi etkisiz; DB UNIQUE ihlali reddedilir |
| Gönderim hataları | SMTP timeout/hata, toplam deadline/cancellation, 30/60 gibi artan bekleme, max deneme, süreç yeniden açılması, belirsiz kabul sonrası aynı Message-ID kopyası, commit hatasından süreç içinde kurtarma |
| Kuyruk izolasyonu/sıra | Off/mock geçmişi SMTP'ye gitmez; mock ölçüm SMTP'ye gitmez; rota değişimi eski kaydı kapatır; aynı alarmda retry sonraki olayı tutar; 2 eşzamanlı gönderim sırasında probe tamamlanır |
| Bakım/susturma | Başlangıç dahil/bitiş hariç; gönderim öncesi tekrar kontrol; çakışan iki bakım+susturma; erken iptal; başlamadan iptal; bütün bakım boyunca worker kapalı; açık/çözülen/süren alarm; stale ve teknik hata; bitiş sonrası normal çözümle özetin çoğalmaması |
| Yetki/CSRF | Yeni tüm yazma yollarında viewer 403, eksik token ve yabancı Origin 403; admin zaman/gerekçe doğrulama, audit; görülme/susturma/bakım/çözülme bağımsızlığı |
| Sağlık | Yanıtsızlık, teknik hata, stale veri ve duraklama ayrı; heartbeat/kuyruk; `/health` davranışı korundu; asgari `/ready` ve korumalı ayrıntılar |
| Migration | Temiz head; v1/v2 ve v3 veri koruması; v3 cihaz/ölçüm/kullanıcı/oturum/alarm/audit sütunlarının tam eşitliği; geçici DB'de upgrade/downgrade/re-upgrade; Alembic `check` şema farkı bulmadı |
| Backup/restore | Yeni demo DB'sinin tüm tabloları SQLite Backup API ile ayrı dosyaya geri yüklendi; tam satır eşitliği, integrity=ok, FK temiz; kullanılabilir varsayılan hesap yok |
| TLS | STARTTLS ve implicit TLS'te CA/hostname doğrulama; STARTTLS yokken düz bağlantıya düşmeme; SMTP DATA kabul sınırı; adres başlığı enjeksiyonu/yasak TLS ayarı ve hata metninde sır saklama |
| Kod/bağımlılık | `python -m ruff check app tests alembic`: geçti; `python -m pip check`: bozuk bağımlılık yok |
| JavaScript | `node --check` ile `app.js`, `operations.js`, `reports.js`: geçti. Bu görsel tarayıcı kabulü değildir |
| Diff/koruma | `git diff --check`: whitespace hatası yok; önceki üç migration için diff yok; eski ZIP SHA-256 aynı |
| Ağsız CLI demo | `python -m app.enterprise_demo --path build/enterprise-demo-20260913-01.db`: geçti; `current_status` ve `resolved_summary` tamamlanan iki mock olay |
| Gerçek yerel HTTP | `python build/verify_enterprise_runtime.py`: tek Uvicorn worker, benzersiz loopback port, yeni DB, yalnız mock. Admin giriş/CSRF, bakım+susturma+iptal+tek özet, başlat/durdur, metrics/CSV HTTP, viewer okuma ve 403 geçti |

HTTP ham kanıtı: `build/enterprise-http-f6076858/evidence.json`; server log ve gerçek
HTTP yanıtından yazılan CSV aynı dizinde. Yardımcı kendi başlattığı sunucuyu sonlandırdı.
Bu CSV'nin Python/HTTP doğrulaması, Excel içe aktarımı veya tarayıcıdan indirme kanıtı
olarak gösterilmez. HTTP hesabının rastgele parolaları kanıta/loga yazılmadı.

Test geliştirme sırasında Alembic'in uygulama logger'larını devre dışı bırakması
giderildi; UTC JSON `+00:00` gösterimi ile DB `Z` saklaması ayrı doğrulandı. Ping
programının kendi süre sınırını aşması teknik `error` yapıldı; güvenilir ping
çıktısındaki hedef yanıtsızlığı `no_reply` kalır. Önceki ölçümler değiştirilmedi.

## Değişen dosyalar

| Grup | Dosyalar |
|---|---|
| Şema/ayar | `app/models.py`, `app/config.py`, `.env.example`, `pyproject.toml`, `alembic/env.py`; yeni `alembic/versions/20260913_0004_notifications_maintenance.py` |
| Servis/akış | `app/main.py`, `app/services/alarms.py`, `monitoring.py`, `scheduler.py`; yeni `notifications.py`, `maintenance.py`, `event_logging.py`, `app/enterprise_demo.py` |
| API/panel | `app/api/monitoring.py`, yeni `app/api/operations.py`; `app/templates/index.html`, `app/static/app.js`, `styles.css`, yeni `operations.js` |
| Test | Yeni `tests/test_enterprise.py`; `tests/conftest.py`, `test_migration.py`, `test_monitoring.py` |
| Belge | `README.md`, `docs/architecture.md`, tarihsel kabul/demo bağlantıları; yeni `enterprise-roadmap.md`, `operations.md`, `demo-enterprise.md`, bu rapor |

Kaynak teslimi için yeni, ayrı **0.5.0 source ZIP** hazırlanır; CRC ve arşivdeki her
dosyanın SHA-256 değeri çalışma kaynağıyla karşılaştırılır. Yanındaki verification
JSON bu paket kontrolünün kanıtıdır. Testler mevcut venv'de ve yerel kaynakta yapıldı;
ZIP'ten yeni bir sanal ortama temiz bağımlılık kurulumu bu sürüm için yapılmadı.
Önceki ZIP'in temiz kurulum kanıtı yeni pakete devredilmez.

## Açık kabul ve sınırlamalar

- **ÇALIŞTIRILMADI:** son grafik/filtre tarayıcı kabulü, CSV düğmesiyle diske indirme,
  Excel fiilî içe aktarımı ve sayısal RTT doğrulaması, dış internet istekleri
  engellenmiş tarayıcı kontrolü. Yeni bakım/susturma formunun görsel ve klavye
  etkileşimi de manuel bekliyor. Escape ile durdurulan bilgisayar kontrolü açılmadı.
- **ÇALIŞTIRILMADI:** bu sürümle loopback ICMP, şirket/lab ağı ve gerçek SMTP sunucu/
  alıcı kabulü. Hiçbir gerçek SMTP denemesi veya şirket ağı trafiği yapılmadı.
- Kapasite/yük ölçümü, merkezi log toplayıcısı/servis kurulumu, bağımsız watchdog,
  gerçek işletim verisiyle RTO/RPO restore tatbikatı, CI, dependency vulnerability
  taraması ve özel secret scanner bu paket dışında ve açık.
- Tek süreç/SQLite; müşteri izolasyonu, operatör/SSO/MFA, PostgreSQL, ayrı çalışanlar,
  SNMP ve toplayıcılar yalnız yol haritasında. IP benzersizliği mevcut sürümde global.
- SMTP tek sabit alıcı grubu, AUTH PLAIN veya kimlik doğrulamasız **TLS** bağlantısı;
  OAuth2/AUTH LOGIN/SMTPUTF8, bounce/teslim teyidi ve manuel failed-event yeniden
  gönderme ekranı yok. Tam olarak bir kez teslim garantisi yok.
- Bakım bitişi yeni ölçüm gerektirebilir; duraklatılmış izleme yeni ölçüm üretmez.
  Bildirim worker'ı çalışsa bile uygulama tümüyle durduğunda uyarı gönderemez.
- Saklama/otomatik temizleme yok; kuyruk ve audit büyümesi kapasite çalışmasına dahil
  edilmelidir. Grafik/rapor/CSV sınırları önceki sürümle aynı kalır.

**Hazırlık seviyesi:** yerel mock demo, kalıcı kuyruk arıza senaryoları, backend
yetki/CSRF, migration ve yerel HTTP operasyon akışı doğrulandı. Tek kurum gerçek
pilotunun kabul kapıları ve çok müşteri hedefi [yol haritasında](enterprise-roadmap.md),
PowerShell kurulum/demo komutları README'de, manuel adımlar
[demo belgesinde](demo-enterprise.md) bulunur.
