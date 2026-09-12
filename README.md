# AvITData Kurumsal Ağ İzleme ve Arıza Uyarı Sistemi

Bu depo, staj projesinin üçüncü çalışan aşamasıdır: cihaz envanteri, manuel ve periyodik kontrol, kalıcı alarm yönetimi ve Türkçe genel durum paneli. Önceki kimlik doğrulama, CSRF, admin/viewer yetkileri ve denetim kayıtları korunur.

Teslim tek süreçli, yerel bir uygulamadır. Varsayılan **MOCK** modu gerçek ağ isteği göndermez. Her açılışta otomatik izleme duraklatılmıştır. Gerçek ICMP/laboratuvar doğrulaması henüz yapılmadı.

## Özellikler

- FastAPI, SQLAlchemy, SQLite, Alembic ve Jinja2
- Normalize ve benzersiz kullanıcı adları
- `pwdlib[argon2]` ile Argon2id parola hashleme; 15–128 karakter parola sınırı
- Varsayılan hesap veya parola oluşturmayan yerel kullanıcı yönetim CLI'si
- Yalnızca hash'i veritabanında tutulan rastgele sunucu oturum kimlikleri
- 8 saat mutlak, 30 dakika hareketsizlik oturum süresi
- Girişte oturum yenileme; çıkış, parola sıfırlama ve pasife almada iptal
- Oturuma bağlı CSRF tokenı ve güvenilir origin doğrulaması
- Hesap ve doğrudan bağlantı kaynağı bazlı geçici giriş hız sınırı
- `admin` ve `viewer` rol matrisi
- Başarılı/başarısız girişler, kullanıcı işlemleri, cihaz değişiklikleri ve kontroller için audit kaydı
- Deterministik mock sağlayıcı ve izinli CIDR sınırlarını koruyan ICMP adaptörü

- FastAPI lifespan içinde tek asyncio tarama döngüsü; ek zamanlayıcı bağımlılığı yok
- Kalıcı open/resolved/closed alarmları, görüldü işareti ve mode/target ayrımı
- Genel Durum ve filtreli, sayfalı Alarmlar ekranı
- Ayrı ve üzerine yazılmayan mock demo veritabanı

## Rol matrisi

| İşlem | Anonim | viewer | admin |
|---|---:|---:|---:|
| Giriş, statik dosyalar, asgari `/health` | ✓ | ✓ | ✓ |
| Cihaz ve kontrol geçmişi okuma | — | ✓ | ✓ |
| Cihaz ekleme/düzenleme/pasife alma | — | — | ✓ |
| Manuel kontrol, izlemeyi başlat/durdur, alarmı görüldü işaretle | — | — | ✓ |
| Genel özet, izleme durumu ve alarmları okuma | — | ✓ | ✓ |
| Audit kayıtlarını okuma | — | — | ✓ |
| `/docs` ve `/openapi.json` | — | — | ✓ |

Anonim API isteği `401`, rolü yetersiz kullanıcı `403` alır. Viewer ekranında değişiklik düğmeleri üretilmez; yetki ayrıca bütün ilgili API uçlarında sunucu tarafında denetlenir.

## Gerekli programlar

- Windows 10/11 ve PowerShell
- 64 bit Python 3.12
- İlk bağımlılık kurulumu için internet bağlantısı

Python launcher (`py`) gerekli değildir. Aşağıdaki örnek doğrudan doğrulanmış bir `python.exe` yolu kullanır.

## Windows PowerShell kurulumu

```powershell
Set-Location "C:\Users\DELL\AvITData-Network-Monitor"

$Python312 = "C:\Users\DELL\AppData\Local\Programs\Python\Python312\python.exe"
& $Python312 --version
& $Python312 -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"

& $Python312 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Mevcut `.venv` çalışıyorsa yeniden oluşturmayın; doğrudan içindeki `python.exe` dosyasını kullanın. Sanal ortamı etkinleştirmek zorunlu değildir ve PowerShell güvenlik politikasını değiştirmeye gerek yoktur.

Örnek yapılandırmayı yalnızca `.env` henüz yoksa kopyalayın:

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
```

## Migration ve ilk hesaplar

İlk iki migration korunmuştur. Yeni `20260912_0003`, alarm ve izleme durumu tablolarını, kontrol kaynağını ve hedef sürümünü ekler; audit kaynağına `scheduler` seçeneğini ekler. v2 verileri korunur; eski ölçümler yeni alarmlara tekrar oynatılmaz. Uygulamayı kapatıp kullandığınız SQLite dosyasının yedeğini aldıktan sonra yükseltin:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Uygulama varsayılan hesap oluşturmaz. İlk yöneticiyi kendi terminalinizde oluşturun; parola iki kez ve görünmeden istenir:

```powershell
.\.venv\Scripts\python.exe -m app.cli create-user --username admin --role admin
```

Viewer oluşturmak için:

```powershell
.\.venv\Scripts\python.exe -m app.cli create-user --username izleyici --role viewer
```

Parola sıfırlama ve kullanıcıyı pasife alma:

```powershell
.\.venv\Scripts\python.exe -m app.cli reset-password --username izleyici
.\.venv\Scripts\python.exe -m app.cli deactivate-user --username izleyici
```

Parola hiçbir komut satırı argümanına yazılmaz. Parola sıfırlama ve pasife alma, kullanıcının bütün açık oturumlarını iptal eder. Son aktif yönetici pasife alınamaz. Her CLI işlemi audit kaydında `source=cli` olarak işaretlenir.

## Uygulamayı çalıştırma

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

- Giriş/web ekranı: <http://127.0.0.1:8000/>
- Asgari sağlık kontrolü: <http://127.0.0.1:8000/health>
- Admin API belgesi: <http://127.0.0.1:8000/docs>

Çıkış işlemi üst bölümdeki **Çıkış yap** düğmesinin `POST` formuyla yapılır. Çıkış hem sunucu oturumunu iptal eder hem tarayıcı cookie'lerini temizler.

## Güvenlik yapılandırması

`.env.example` içindeki temel değerler:

```dotenv
APP_BASE_URL=http://127.0.0.1:8000
SESSION_ABSOLUTE_HOURS=8
SESSION_IDLE_MINUTES=30
LOGIN_SESSION_MINUTES=10
LOGIN_MAX_ATTEMPTS=5
LOGIN_WINDOW_SECONDS=300
LOGIN_LOCK_SECONDS=300
```

`APP_BASE_URL`, CSRF origin doğrulamasının tek kaynağıdır ve tarayıcıdaki gerçek adresle aynı olmalıdır. Düz HTTP yalnızca `127.0.0.1`, `localhost` veya `::1` için kabul edilir. HTTPS adresinde oturum ve CSRF cookie'lerinde `Secure=true` otomatik olarak zorunlu olur. Oturum cookie'leri ayrıca `HttpOnly`, `SameSite=Lax` ve `Path=/` kullanır.

Giriş hız sınırı uygulama belleğindedir; Redis gerektirmeyen bu yerel tek-süreç prototipine uygundur ve uygulama yeniden başlatılınca sıfırlanır. Kaynak adresi doğrudan bağlantıdan alınır; `X-Forwarded-For` gibi proxy başlıklarına güvenilmez. Kalıcı hesap kilidi uygulanmaz.

Aynı dosya tabanlı SQLite veritabanıyla ikinci uygulama örneği işletim sistemi dosya kilidiyle engellenir. `.db.lock` dosyası normaldir; çalışırken silmeyin. Süreç kapanırsa/çökerse işletim sistemi kilidi bırakır. Dosyanın kendisi kalabilir. `--reload` yalnızca geliştirmede ve otomatik izleme duraklatılmışken kullanılmalıdır; teslim komutuna eklemeyin.

Bu tasarım JWT kullanmaz. Tarayıcıdaki rastgele oturum kimliğinin yalnızca SHA-256 hash'i SQLite'ta saklanır. Rol ve aktiflik her istekte veritabanından doğrulanır. Oturum ve CSRF değerleri URL, localStorage veya audit metadata alanına yazılmaz.

### CSRF ve Swagger

Giriş, çıkış ve tüm cihaz değişikliklerinde oturuma bağlı CSRF tokenı zorunludur; `SameSite` tek başına koruma olarak kabul edilmez. HTML formları tokenı gizli alanla, uygulamanın JavaScript istekleri `X-CSRF-Token` başlığıyla gönderir. Origin/Referer ayrıca `APP_BASE_URL` ile eşleşmelidir.

Swagger yalnızca admin kullanıcıya açıktır. Yazma uçlarında `X-CSRF-Token` parametresi OpenAPI şemasında belgelenir ve koruma kaldırılmaz. Manuel Swagger denemesinde tokenı giriş yapılmış ana sayfanın `<meta name="csrf-token">` alanından alıp ilgili başlığa girin; tokenı terminal geçmişine veya loglara yazmayın.

## Denetim kayıtları

Admin ana sayfasında en yeni kayıtlar gösterilir. Sayfalı JSON uç noktası:

```text
GET /api/audit-logs?limit=50&offset=0
```

Kayıtlar UTC zamanı, aktör, kaynak, işlem, hedef ve sonucu içerir. Parola, parola hash'i, oturum/CSRF tokenı, cookie ve ham istek gövdesi kaydedilmez. Audit kayıtlarını düzenleyen veya silen uygulama ucu yoktur. Cihaz değişikliği ile ilgili audit kaydı aynı veritabanı transaction'ında yazılır.

## Mock ve izinli ICMP

Varsayılan `MONITOR_MODE=mock` gerçek ağa paket göndermez. `MOCK_DEMO=false` iken son IP baytının 3'e bölümünden kalan değere göre `1=yanıt`, `2=yanıt yok`, `0=kontrol hatası` üretir. Sonuçlar açıkça **MOCK / simülasyon** olarak işaretlenir.

ICMP'yi yalnızca açıkça yetkili olduğunuz hedeflerde etkinleştirin:

```dotenv
MONITOR_MODE=icmp
PING_TIMEOUT_SECONDS=2
ALLOWED_TARGET_CIDRS=127.0.0.1/32,::1/128
```

Admin rolü bile `ALLOWED_TARGET_CIDRS` listesini atlayamaz. Hostname çözümleme, ağ tarama ve keşif yapılmaz. “Yanıt alınamadı” cihazın kesin olarak kapalı olduğu anlamına gelmez.

## Test ve kalite kontrolleri

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .

$env:DATABASE_URL = "sqlite:///./schema-check.db"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic check
Remove-Item -LiteralPath .\schema-check.db
Remove-Item Env:DATABASE_URL
```

Testler yalnızca geçici veritabanlarında kullanıcı oluşturur, kontrol edilebilir saat kullanır ve gerçek ping göndermez. Hem sıfır veritabanı kurulumu hem v1 cihaz/kontrol verisi bulunan veritabanının veri kayıpsız yükseltilmesi sınanır.

## API özeti

| Yöntem | Yol | Yetki |
|---|---|---|
| `GET` | `/health` | Herkes |
| `GET` | `/api/auth/me` | viewer/admin |
| `GET` | `/api/devices` | viewer/admin |
| `POST` | `/api/devices` | admin + CSRF |
| `GET` | `/api/devices/{id}` | viewer/admin |
| `PATCH` | `/api/devices/{id}` | admin + CSRF |
| `POST` | `/api/devices/{id}/check` | admin + CSRF |
| `GET` | `/api/devices/{id}/checks` | viewer/admin |
| `GET` | `/api/audit-logs` | admin |

## Sınırlar

Uygulama yalnızca `127.0.0.1` üzerinde çalıştırılmalıdır. SNMP, TCP taraması, e-posta/Telegram bildirimi, PDF, grafik, raporlama, otomatik geçmiş silme ve internet dağıtımı bu aşamada yoktur. Çok kullanıcılı/çok süreçli dağıtım öncesinde paylaşılan hız sınırlama deposu, HTTPS sonlandırma ve operasyonel anahtar/yedekleme yönetimi ayrıca tasarlanmalıdır.


## Periyodik izleme ve alarm kuralları

Admin, **Genel Durum → Otomatik izlemeyi başlat** ile ilk taramayı hemen başlatır. Sonraki tarama önceki taramanın bitiminden itibaren `MONITOR_INTERVAL_SECONDS` saniye sonra başlar (varsayılan 60; mock için en az 5, ICMP için en az 60). **İzlemeyi durdur** devam eden otomatik kontrolleri iptal eder. Tek tarama işi vardır; gecikmiş aralıklar biriktirilmez. Görevler en fazla `MAX_CONCURRENT_CHECKS` boyutlu gruplar halinde çalışır. Pasif ve meşgul cihazlar atlanır; bir cihazdaki hata diğerlerini durdurmaz.

Manuel ve otomatik ölçümler aynı servis, cihaz kilidi, eşzamanlılık sınırı, ICMP izin listesi ve timeout kurallarını kullanır. Zamanlayıcı HTTP isteği veya kullanıcı oturumu üretmez. Ölçüm kaynağı `manual/scheduled`; otomatik alarm audit aktörü `system/scheduler` olur. Her otomatik ölçüm için fazladan audit yazılmaz; alarm geçişleri yazılır.

| Sonuç | Bekleyen yanıtsızlık serisi | Açık alarm |
|---|---|---|
| `no_reply` | Bir artar | Varsayılan 3. ardışık sonuçta açılır; aynı hedef/modda tek açık alarm |
| `reply` | Sıfırlanır | Aynı hedef/mod alarmı `resolved` olur |
| `error` | Sıfırlanır | Açık kalır; kontrol mekanizması hatası ayrıca gösterilir |
| Atlanan/iptal edilen kontrol | Artmaz | Arıza veya iyileşme kanıtı sayılmaz |

`ALARM_THRESHOLD=3` eşiği yapılandırılabilir. “ICMP yanıtı alınamıyor” alarmı cihazın kesin kapalı olduğunu veya kesin ağ kesintisi süresini söylemez. Ping eksikliği, izin ve ayrıştırma sorunu `error` durumudur. **Görüldü** yalnızca kim/ne zaman bilgisini kaydeder; alarmı çözmez.

Durdurma ve yeniden başlatma bekleyen serileri sıfırlar; açık alarm ve görüldü bilgisi korunur. IP değişikliği veya pasife alma, eski açık alarmları `closed` ve `target_changed/device_deactivated` gerekçesiyle idari kapatır. Başarılı yanıttaki `resolved` durumundan ayrıdır. Hedef sürümü, kontrol sırasında IP değiştirilip geri alınsa bile eski sonucu yeni duruma uygulamaz. Eski hedefin sonucu geçmişte `is_current=false` olarak kalır. Mock sonuçları ICMP alarmına etki etmez.

Sonuç, sayaç ve alarm/audit geçişi kısa `BEGIN IMMEDIATE` transaction'ında işlenir; ağ boyunca yazma transaction'ı açık tutulmaz. Görevler Session paylaşmaz. Aynı sonucun tekrar değerlendirilmesi etkisizdir; SQLite kısmi benzersiz indeksi bir hedef/mod için ikinci açık alarmı engeller. Geçmiş bu aşamada otomatik silinmez.

Panel 5 saniyede bir yalnızca verileri okur. Yenileme kontrol tetiklemez. Sonucu olmayan, pasif veya son ölçümü iki kontrol aralığından eski cihaz **Güncel ölçüm yok** olarak gösterilir. Genel özet yalnızca seçili çalışma modunu ve güncel hedef sürümünü kullanır. Alarm listesinde mod açıkça seçilir; geçmişteki farklı modlar etiketlenir.

| Yöntem | Yol | Yetki |
|---|---|---|
| GET | `/api/monitoring/status` | viewer/admin |
| POST | `/api/monitoring/start`, `/api/monitoring/stop` | admin + CSRF/origin |
| GET | `/api/summary` | viewer/admin |
| GET | `/api/alarms?status=open&probe_mode=mock&device_id=1&limit=20&offset=0` | viewer/admin |
| GET | `/api/alarms/{id}` | viewer/admin |
| POST | `/api/alarms/{id}/acknowledge` | admin + CSRF/origin |

Alarm listesinde status/device_id isteğe bağlıdır; mod verilmezse çalışma modu kullanılır. Limit 1–100, offset sıfır veya daha büyüktür.

## Beş dakikalık mock sunumu

[Ayrı demo veritabanı, hesap ve sunum adımları](docs/demo.md). `python -m app.demo --path .\demo-monitor.db` yalnızca yeni dosya oluşturur, mevcut dosyada hata verir. Demo hesabı mevcut `app.cli create-user` komutuyla, getpass üzerinden oluşturulur; sabit parola yoktur. Normal `network_monitor.db` ile demo dosyasını karıştırmayın. Demo terminalindeki `DATABASE_URL` değişkeni `.env` değerinden önceliklidir.

Uygulama için yeni bağımlılık eklenmedi; mevcut sabitlenmiş gereksinimleri kurmak yeterlidir. Kontroller için `python -m pytest`, `python -m ruff check .` ve ayrı test SQLite üzerinde `python -m alembic check` kullanılır. Starlette/AnyIO deprecation uyarısı genel filtreyle gizlenmemiştir.

Tek döngünün açılış/kapanış yerleşimi [FastAPI lifespan belgesine](https://fastapi.tiangolo.com/advanced/events/), kısmi indeks ve SQLite işlem davranışı [SQLAlchemy SQLite belgesine](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html) dayanır. APScheduler veya paylaşılan job store kullanılmaz.


### Bu teslimde çalıştırılan doğrulamalar (12 Eylül 2026)

- Başlangıç: 46 mevcut test başarılı. Son durum: **73 test başarılı** (27 yeni risk testi).
- `ruff check .`: başarılı; `pip check`: bozuk bağımlılık yok.
- Ayrı SQLite üzerinde üç migration ve Alembic `check`: yeni şema işlemi gerekmiyor. v1/v2 veri koruması test edildi.
- Uvicorn tek worker ile gerçek açılış ve Windows'ta ikinci süreç engellemesi doğrulandı.
- Yerel tarayıcıda geçici mock veritabanıyla başlat/durdur, alarm açılma, tekrarlayan yanıtsızlıkta tek alarm, görüldü ve aynı alarmın çözülmesi doğrulandı. Dar panel görünümü incelendi.
- Tek mevcut Starlette/AnyIO deprecation uyarısı görünür bırakıldı. Gerçek ICMP gönderilmedi; laboratuvar doğrulaması ve diğer işletim sistemleri bu teslimde sınanmadı.
