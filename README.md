# AvITData Kurumsal Ağ İzleme ve Arıza Uyarı Sistemi

Depo adı: **AvIT-Data**.

Bu depo, staj projesinin dördüncü çalışan aşamasıdır: cihaz bazlı gecikme grafiği ve CSV ölçüm raporu; cihaz envanteri, manuel ve periyodik kontrol, kalıcı alarm yönetimi ve Türkçe genel durum paneli. Önceki kimlik doğrulama, CSRF, admin/viewer yetkileri ve denetim kayıtları korunur.

Teslim tek süreçli, yerel bir uygulamadır. Varsayılan **MOCK** modu gerçek ağ isteği göndermez. Her açılışta otomatik izleme duraklatılmıştır. Windows loopback ICMP doğrulandı; şirket/laboratuvar cihazları bu görev kapsamında sınanmadı. Güncel teslim sonucu [kabul raporundadır](docs/acceptance.md); çalışma mantığı [mimari belgesinde](docs/architecture.md) açıklanır.

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
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Mevcut `.venv` çalışıyorsa yeniden oluşturmayın; doğrudan içindeki `python.exe` dosyasını kullanın. Sanal ortamı etkinleştirmek zorunlu değildir ve PowerShell güvenlik politikasını değiştirmeye gerek yoktur. Önce yalnızca çalışma bağımlılıklarını kurup uygulamayı aşağıdaki sırayla doğrulayın; test araçları daha sonra `python -m pip install -r requirements-dev.txt` ile kurulur. Sistem Python, PATH ve `.venv.broken` değiştirilmez.

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

## Durdurma, yeniden başlatma ve güvenli SQLite yedeği

Panelde **İzlemeyi durdur** yalnızca otomatik taramayı durdurur; sunucu kapanmaz.
Sunucuyu çalıştırdığınız terminalde `Ctrl+C` kullanın ve kapanışın tamamlanmasını
bekleyin. Aynı yapılandırma ve tek worker komutuyla yeniden başlatın. Envanter,
geçmiş, alarmlar ve görüldü bilgisi korunur; otomatik izleme duraklatılmış açılır.
Port 8000 doluysa başka boş port seçin ve `APP_BASE_URL` değerini aynı adresle eşleştirin.

Çalışan SQLite dosyasının yalnızca ana `.db` dosyasını kopyalamak koşulsuz güvenli
değildir; WAL/journal içeriği eksik kalabilir. Aşağıdaki örnek SQLite Backup API'siyle
tutarlı bir yedek oluşturur. Kaynak yolunu gerçek `DATABASE_URL` dosyanıza göre
kontrol edin. Komut yeni UUID adlı hedef açar; mevcut yedeğin üzerine yazmaz.

```powershell
@'
from contextlib import closing
from pathlib import Path
import sqlite3
import uuid

source = Path("network_monitor.db").resolve(strict=True)
backup = source.with_name("backup-" + uuid.uuid4().hex + ".db")
with backup.open("xb"):
    pass
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(backup)) as dst:
        src.backup(dst)
        assert dst.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not dst.execute("PRAGMA foreign_key_check").fetchall()
        print("Şema:", dst.execute("SELECT version_num FROM alembic_version").fetchone()[0])
print("Yedek:", backup)
'@ | .\.venv\Scripts\python.exe -
```

İşlem hata verirse o hedefi başarılı yedek saymayın; yeni bir dosya adıyla tekrar
çalıştırın. Yedek kullanıcı/parola hash'i ve oturum kayıtları içerir; teslim ZIP'ine
eklemeyin, erişimini veritabanı kadar sınırlayın.

Geri yükleme denemesinde aynı kodun `source` değerini seçtiğiniz yedek dosyası,
`backup` değerini `Path("restore-" + uuid.uuid4().hex + ".db").resolve()` yaparak
**ayrı bir dosya** üretin. Gerçek veritabanını ezmeyin. Şema sürümünü, integrity ve
foreign key kontrollerini, cihaz/ölçüm/alarm sayılarını karşılaştırın. Ayrı PowerShell
terminalinde yalnızca bu deneme dosyasını seçin:

```powershell
$env:DATABASE_URL = "sqlite:///./restore-BURAYA_OLUSAN_AD.db"
$env:MONITOR_MODE = "mock"
$env:APP_BASE_URL = "http://127.0.0.1:8001"
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
```

8001 boş olmalı; gerçek oluşan dosya adını yazın. Başlangıçta otomatik izleme
duraklatılmış kalır. Eski oturumların da yedekte bulunabileceğini unutmayın.
Deneme bitince `Ctrl+C` ve bu terminali kapatma ile normal `.env` ayarlarına dönün.
Bu görevde geri yükleme yalnızca kabul veritabanında denendi.
[Python SQLite Backup API belgesi](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).

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

Uygulama yalnızca `127.0.0.1` üzerinde çalıştırılmalıdır. SNMP, TCP taraması, e-posta/Telegram bildirimi, PDF, toplu rapor tasarımcısı, otomatik geçmiş silme ve internet dağıtımı bu aşamada yoktur. Çok kullanıcılı/çok süreçli dağıtım öncesinde paylaşılan hız sınırlama deposu, HTTPS sonlandırma ve operasyonel anahtar/yedekleme yönetimi ayrıca tasarlanmalıdır.


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


### Tarihsel aşama 3 doğrulaması (güncel sonuç değildir)

- Başlangıç: 46 mevcut test başarılı. Son durum: **73 test başarılı** (27 yeni risk testi).
- `ruff check .`: başarılı; `pip check`: bozuk bağımlılık yok.
- Ayrı SQLite üzerinde üç migration ve Alembic `check`: yeni şema işlemi gerekmiyor. v1/v2 veri koruması test edildi.
- Uvicorn tek worker ile gerçek açılış ve Windows'ta ikinci süreç engellemesi doğrulandı.
- Yerel tarayıcıda geçici mock veritabanıyla başlat/durdur, alarm açılma, tekrarlayan yanıtsızlıkta tek alarm, görüldü ve aynı alarmın çözülmesi doğrulandı. Dar panel görünümü incelendi.
- Tek mevcut Starlette/AnyIO deprecation uyarısı görünür bırakıldı. Gerçek ICMP gönderilmedi; laboratuvar doğrulaması ve diğer işletim sistemleri bu teslimde sınanmadı.


## Cihaz bazlı gecikme grafiği ve CSV (aşama 4)

Admin ve viewer bir cihazın **Geçmiş** düğmesine basınca **Gecikme grafiği ve CSV** bölümü açılır. Pasif cihaz geçmişi de okunabilir. Varsayılan dönem son 24 saattir; son 1 saat, son 7 gün veya özel aralık seçilebilir. **Filtreleri uygula** verileri yeniden okur; ölçüm veya alarm başlatmaz. Yeni ölçümleri rapora almak için filtreleri yeniden uygulayın.

Arayüzde tüm rapor tarihleri **Europe/Istanbul** olarak etiketlenir; bilgisayarın yerel saat diliminden bağımsızdır. Özel tarih alanları da İstanbul yerel saatidir. Seçili somut başlangıç/bitiş, mod ve kaynak ekranda yazılır. Başlangıç dahil, bitiş hariçtir (`start <= checked_at < end`). CSV indirme, ekranda son başarıyla uygulanan **aynı somut filtreleri** kullanır; indirme sırasında son 24 saat tekrar hesaplanmaz. Filtre düzenlenince eski rapor/CSV seçimi geçersizleşir. Eski veya iptal edilmiş HTTP yanıtları yeni seçimi ezemez.

### Salt okunur API

```text
GET /api/devices/{id}/metrics
GET /api/devices/{id}/measurements.csv
```

Ortak query parametreleri: `start`, `end`, `probe_mode=mock|icmp`, `source=manual|scheduled|all`. `start/end` birlikte verilmelidir; ikisi de yoksa sunucu saatine göre son 24 saat kullanılır. Mod verilmezse çalışma modu, kaynak verilmezse `all` kullanılır. Örnek tarih: `2026-09-12T09:00:00+03:00` veya `2026-09-12T06:00:00Z`; URL içinde `+` işareti `%2B` olarak kodlanmalıdır. Saniye ve saat dilimi zorunludur; en fazla altı basamak saniye kesri desteklenir. Karşılaştırma UTC üzerinde yapılır. Saat dilimsiz/hatalı tarih, eksik tarih çifti, ters/eşit aralık ve **30 günü aşan dönem** `422` döndürür. Bilinmeyen cihaz `404`, anonim erişim `401` alır. Rapor uçları yazma/CSRF kurallarını değiştirmez.

`metrics` yanıtı `filters`, `device`, `summary` ve `graph` içerir. `graph` alanında `total_count`, `shown_count`, `limit`, `truncated`, `first_checked_at`, `last_checked_at`, `gap_threshold_seconds` ve noktalar bulunur. Grafik en yeni **2.000 ölçümü** `(checked_at, measurement_id)` sırasıyla gösterir; sınır aşımı ekranda açıkça yazılır. Grafik ekseni gösterilen ölçümlerin gerçek zaman aralığına odaklanır; özet yine seçilen dönemin **tamamını** kapsar. Geçmiş sonuçların kayıtlı hedef IP ve hedef sürümleri kullanılır; bugünkü cihaz IP'si geçmişe yazılmaz.

Grafik sayısal zaman ekseni kullanır: düzensiz ölçümler eşit aralıklı yerleştirilmez. `no_reply`, `error`, eksik/geçersiz RTT `null` noktadır, 0 ms değildir. Bu noktalar arasında, hedef/sürüm değişiminde veya mevcut `MONITOR_INTERVAL_SECONDS` değerinin iki katından uzun gözlem boşluğunda çizgi çizilmez. Boşluk eşiği ekranda yazılır; geçmişte kullanılan kontrol aralığının tarihsel kaydı olmadığı için mevcut ayar esas alınır. Tek RTT bir nokta, boş veri açık mesaj olarak gösterilir. RTT noktalarının tooltip'lerinde zaman, hedef, sonuç, mod, kaynak ve RTT bulunur; RTT'si olmayan kayıtlar alt ayrıntı tablosundan görülebilir.

### Özetin anlamı

RTT sayısı/ortalaması/minimumu/maksimumu yalnızca `reply` olan, sonlu ve sıfır veya pozitif RTT taşıyan ölçümlerden hesaplanır. Başarılı ama RTT'si bilinmeyen yanıt, reply sayısına dahil olur fakat RTT istatistiklerine dahil olmaz.

**Yanıt oranı = reply / (reply + no_reply) × 100.** Örneğin 3 reply, 1 no_reply ve 2 error için toplam 6, yanıt oranı %75, kontrol hatası 2'dir. `error` paydadan çıkarılır ve ayrı gösterilir. Bu oran SLA, kesintisiz çalışma süresi, paket kaybı veya gerçek ağ kullanılabilirliği değildir. Payda sıfırsa oran, geçerli RTT yoksa istatistikler `null` olur; arayüzde `—` gösterilir. Modlar raporda birleşmez; **MOCK / SİMÜLASYON** etiketi görünürdür.

### Türkçe CSV ve Excel içe aktarma

**CSV indir**, filtrelere uyan ham kayıtları tarih sırasıyla üretir. Varsayılan üst sınır **50.000 satır**dır. `CSV_MAX_ROWS` ile 1–50.000 aralığında daha düşük sınır belirlenebilir. Sınır aşılırsa dosya başlamadan `422` ve tarih aralığını daraltma mesajı döner; kısmi başarılı dosya verilmez. En fazla sınır + 1 kayıt okunur ve dosya yanıt öncesinde bellekte tamamlanır; sınırsız veri yüklenmez. Raporlar static dizinine veya sunucuda kalıcı dosyaya yazılmaz; yalnızca kısa okuma transaction'ı kullanılır.

Sütunlar: `measurement_id`, `device_id`, `device_name`, `target_ip`, `checked_at_utc`, `probe_mode`, `trigger_source`, `outcome`, `latency_ms`. `device_name` için tarihsel snapshot yoktur; **güncel cihaz adı** kullanılır. `target_ip` ölçüm anındaki hedef, tarihler **UTC ISO 8601** değeridir. CSV ham RTT alanını korur; eksik veya sonlu olmayan RTT boş hücredir.

Dosya Python `csv` modülüyle **UTF-8 BOM**, **noktalı virgül** ayırıcı ve CRLF satır sonuyla üretilir. Ondalık ayırıcı **virgül**dür (örn. `1,25`); null boş hücredir. Tırnak, noktalı virgül ve satır sonu içeren metinler CSV kurallarıyla kaçırılır. Dosya adı sunucu tarafından oluşturulur; istemciden dosya yolu alınmaz.

Excel'de **Veri → Metin/CSV'den** ile dosyayı seçin; kodlamayı **65001: UTF-8**, ayırıcıyı **noktalı virgül**, sayı yerel ayarını **Türkçe (Türkiye)** olarak seçin. Cihaz adı/IP ve UTC tarih sütunlarını gerekirse **Metin** türünde içe aktarın; Excel'in otomatik tarih/sayı dönüşümüne bırakmayın.

Kullanıcı kaynaklı metinler başlangıçtaki boşluk, tab ve kontrol/biçim karakterleri atlanarak denetlenir. `=`, `+`, `-`, `@` ile başlayan riskli hücrelerin başına tek tırnak (`'`) eklenir; yalnızca CSV çift tırnağına güvenilmez. Bu işlem yalnızca dışa aktarım içindir; veritabanı değişmez. Amaç Excel'in hücreyi formül yerine metin olarak ele almasıdır. CSV okuyucuda bu koruyucu tek tırnak görünür olabilir. Excel uygulamasında fiilî test yapılmadı; diğer tablo uygulamaları ve dosyanın yeniden kaydedilip açılması için evrensel güvenlik garantisi verilmez.

### Yerel grafik bağımlılığı

Teslim kabulünde Windows `<1 ms` yanıtlarının kesin RTT olmadığı doğrulandı:
`reply` sayılır, gecikme `null` kalır; özet RTT hesabına girmez, CSV boş hücre üretir.
Windows ping çıktısı OEM kod sayfasıyla çözülür. Geçmişte türetilmiş sayısal kayıtlar
otomatik değiştirilmez. [Microsoft ping belgesi](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ping).

Chart.js **4.5.1** tarayıcı UMD dağıtımı `app/static/vendor/chartjs-4.5.1/chart.umd.js` içinde yereldir. MIT lisansı `LICENSE.md`, npm tarball kaynağı, SHA-512 paket bütünlüğü ve dosyanın SHA-256 değeri `provenance.json` içinde saklanır. Paket bütünlüğü indirme sırasında doğrulandı. Çalışırken CDN, Node.js, frontend derlemesi veya tarih adaptörü gerekmez. Python bağımlılıkları ve şema değişmedi; bu aşama için yeni migration yoktur. Mevcut son migration `20260912_0003` kalır.

Panel/grafik yerel kaynaklıdır; admin `/docs` ekranındaki varsayılan Swagger UI
ise dış CDN kaynakları kullanır. Dış istekler engellenmiş tarayıcı doğrulaması
tamamlanmadı. Güncel **126 test** sonucu ve arayüz/Excel/indirme sınırları
[teslim kabul raporunda](docs/acceptance.md) ayrı ayrı kayıtlıdır.

Geliştirme testleri ve güncelleme için mevcut sanal ortamı kullanın, `.env` dosyasını ezmeyin.
Yalnız uygulamayı çalıştıracak kullanıcı için yukarıdaki `requirements.txt` kurulumu yeterlidir:

```powershell
Set-Location "C:\Users\DELL\AvITData-Network-Monitor"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Önceki aşamanın şeması henüz uygulanmadıysa, yedek alıp sunucu kapalıyken `python -m alembic upgrade head` çalıştırın. Bu aşamada yeni migration eklenmedi.

Uygulama kararlarının kaynakları: [Chart.js entegrasyonu](https://www.chartjs.org/docs/latest/getting-started/integration.html), [çizgi grafikleri ve spanGaps](https://www.chartjs.org/docs/latest/charts/line.html), [Python csv](https://docs.python.org/3/library/csv.html), [CWE-1236](https://cwe.mitre.org/data/definitions/1236.html).


### Tarihsel aşama 4 doğrulaması (teslim kabulünden önce)

- Başlangıçtaki 73 test korundu; **121 test başarılı** (48 yeni raporlama testi). Ruff ve pip check başarılı. Mevcut Starlette/AnyIO deprecation uyarısı gizlenmedi.
- Tarih/offset ve mikrosaniye sınırları, tüm dönem özeti, null/sonlu olmayan RTT, kaynak/mod ayrımı, CSV biçimi/formül koruması ve satır sınırı geçici SQLite veritabanlarında test edildi. CSV standart Python okuyucuyla yeniden açıldı.
- Yerel Uvicorn üzerinde admin ve viewer ile grafik, kaynak/mod filtresi, İstanbul özel aralığı, boş veri ve 2.005 kayıttan en yeni 2.000'inin gösterildiği mesaj doğrulandı. Grafik boşlukları görsel olarak incelendi; tarayıcı hata günlüğü temizdi.
- Gerçek yerel HTTP CSV yanıtı dosyaya yazılıp `csv.DictReader` ile yeniden açıldı: 2.005 ham satır, Türkçe cihaz adı, tarihsel hedef ve ondalık virgül doğrulandı; grafik aynı filtrelerde 2.000 noktaydı.
- Gömülü tarayıcı CSV'nin hazırlanmasını gösterdi ancak indirme olayı bildirmedi; tarayıcının dosyayı diske kaydetmesi doğrulanamadı. Excel ve başka tablo uygulamalarında fiilî test yapılmadı.
- Tüm grafik script'lerinin localhost'tan geldiği doğrulandı; makinenin internet bağlantısı fiziksel olarak kesilmedi. Gerçek ICMP/laboratuvar trafiği üretilmedi.
