# Teslim kabul raporu — 12 Eylül 2026

Kapsam: mevcut aşama 4 uygulamasının temiz kurulumu, teslim hatası düzeltmesi,
doğrulama ve belgeler. Yeni ürün özelliği, migration, commit, push veya yayın yoktur.
Yerel mock teslimini engelleyen bilinen bir hata kalmadı. Aşağıdaki
**ÇALIŞTIRILMADI** satırları başarı sayılmamalıdır.

## Ortam ve kopya

| Bileşen | Test edilen değer |
|---|---|
| Sistem | Windows 11, `10.0.26200`, x64 |
| Python / SQLite | Python **3.12.12**, SQLite **3.50.4** |
| Uygulama | 0.4.0; başlangıç commit'i `178520f`, güncel çalışma dosyaları + teslim düzeltmesi |
| Runtime | FastAPI 0.141.1, SQLAlchemy 2.0.52, Alembic 1.19.2, Uvicorn 0.52.4 |
| Transitif örnekleri | Starlette 1.6.0, AnyIO 4.15.1, argon2-cffi 25.1.0 |
| Test araçları | pytest 9.1.1, Ruff 0.16.7, httpx 0.28.1, httpx2 2.12.0 |
| Grafik | Yerel Chart.js 4.5.1; MIT lisansı ve provenance dosyası pakette |
| Şema | `20260912_0003`, tek head |
| Tarayıcı | Codex gömülü tarayıcı; sürüm numarası araç tarafından verilmedi |
| Excel | Microsoft Excel 2010 bulundu/açıldı; pencere başlığı “Ürün Etkinleştirilemedi” |

Kaynaklar `C:\Users\DELL\AvITData-Network-Monitor` içinden dosya izin listesiyle
seçildi; yalnızca HEAD arşivlenmedi. İlk kopya eski `.pytest_cache` izinleri nedeniyle
onaylı paket kurucusuna kapalıydı. İzinler değiştirilmeden proje kökünde benzersiz
`acceptance-20260912-b4441b42` klasörü ve yeni venv oluşturuldu. Kurulum/test burada
yapıldı; tamamlandıktan sonra geçici malzemeler `build/acceptance-20260912-b4441b42`
altına taşındı. Teslim ZIP'i bu klasörün hiçbir içeriğini almaz.

`.env.example` ayrı `.env` olarak kopyalandı; DB, mod, izin listesi ve APP_BASE_URL
yalnızca kabul çocuk süreçlerinin ortamında değiştirildi. Ana `.env`, kullanıcı
veritabanı, `.venv.broken`, sistem Python/PATH ve PowerShell politikası değiştirilmedi.
Gerekli paket indirmeleri sandbox dışında izinli kurulumla yapıldı; çalışma zamanı
bağımsızlığı ile ilk kurulumun internet ihtiyacı ayrı konulardır.

## Sonuçlar

Aşağıdaki kontroller yukarıdaki ortamda yapıldı. Komutlar kopyanın kökünde,
`.venv\Scripts\python.exe` ile çalıştırıldı. Kabul yardımcıları ve DB/CSV gibi ham
malzemeler yalnızca `build/acceptance-20260912-b4441b42` içinde, ZIP dışında tutulur.

| Kontrol | Beklenen | Gerçek gözlem | Sonuç | Kanıt / tekrar | Kalan kullanıcı kontrolü |
|---|---|---|---|---|---|
| Git başlangıcı | Kullanıcı değişikliklerini ve merge durumunu koru | `## main`, başlangıç temiz; U listesi/index boş; MERGE_HEAD yok | GEÇTİ | `git status --short --branch`, `git ls-files -u`, `git diff --name-only --diff-filter=U` | Son diff'i inceleme |
| Git çatışma / açık merge | İki durumu ayrı belirle | Çözülmemiş dosya **yok**; açık merge **yok**; stage/commit yapılmadı | GEÇTİ | `git rev-parse --verify -q MERGE_HEAD`; diff ve cached diff check | Değişiklikleri kullanıcı isterse commitler |
| Kaynak koruması | Güncel seçili kaynak, mevcut veri kopyalanmasın | İlk 55 dosyalık SHA-256 manifest; migrations değişmedi; düzeltme kaynak ve test kopyasında aynı | GEÇTİ | `source-manifest.json`, son arşiv manifesti | Yok |
| Runtime kurulumu | Dev paketleri olmadan gerçek servis çalışsın | Yeni venv; önce yalnız requirements.txt; `pytest` ve `httpx` bulunmadığı ayrıca doğrulandı | GEÇTİ | `python -m pip install -r requirements.txt`, özel `acceptance_run.py` | README'deki kurulum sırasını uygulama |
| Boş DB migration | Tüm şema temiz kurulabilsin | Üç migration yeni SQLite'a uygulandı | GEÇTİ | `python -m alembic upgrade head` | Mevcut DB yükseltmeden önce yedek |
| Uvicorn / başlangıç | Tek worker, boş localhost port, doğru origin; izleme duraklatılmış | Mock `127.0.0.1:61821`; health 200; başlangıç running=false | GEÇTİ | `mock-server.json`, `mock-server.log` | Kendi boş portunu seçme |
| Giriş ve rol | Admin giriş; anonim 401; viewer yazma 403 | Gerçek HTTP ve cookie/CSRF ile geçti; tarayıcı admin girişinden panel açıldı | GEÇTİ | `acceptance_run.py`; `tests/test_auth.py`, `tests/test_users.py` | Sunum hesabıyla giriş |
| Envanter / mock / alarm | Cihaz ekle, 3 no_reply ile tek alarm, görüldü ve reply ile çözülme | 6 manuel sonuç: reply, no_reply×4, reply; aynı alarm görüldü ve resolved | GEÇTİ | `mock-evidence.json`; gerçek admin API | Demo akışı |
| Periyodik izleme | Başlat/durdur; scheduled kayıt | Ayrı yanıt cihazında scheduled ölçüm; durdurma running=false | GEÇTİ | `acceptance_run.py`, `tests/test_phase3.py` | Yok |
| Metrics / CSV HTTP | Aynı veriden özet/ham kayıt, salt okuma | Yaşam döngüsü cihazında 7 nokta ve 7 CSV satırı; viewer okuyabildi | GEÇTİ | `mock-evidence.json`, `representative.csv` | Yok |
| CSV dosyası / kodlama | HTTP yanıtı diske yazılıp yeniden okunabilsin | Python csv okuyucusu: UTF-8 BOM, noktalı virgül, Türkçe ad/alıntı, boş RTT, UTC Z; ayrı yanıtta `1,3` | GEÇTİ | `representative.csv`, `numeric.csv`, `acceptance_run.py` | Excel sayısal tür kontrolü aşağıda |
| Grafik arayüzünün son kopyası | Seçim, filtre ve çizim tarayıcıda doğrulansın | Giriş/panel açıldı; grafik seçimi aşamasından önce bilgisayar kontrolü durduruldu. Önceki aşama 4 gözlemi yeni test sayılmadı | ÇALIŞTIRILMADI | `docs/demo.md`; otomatik `tests/test_reports.py` başarılı | Geçmiş → filtreler → grafik |
| Tarayıcı CSV kaydı | CSV düğmesi dosyayı diske kaydetsin | Bu turda indirme tamamlanmadı. Önceki turda indirme olayı zaman aşımı vardı; tekrar döngüsüne girilmedi | ÇALIŞTIRILMADI | Normal tarayıcıda aşağıdaki adımlar | İndirilen gerçek dosyayı açma |
| Excel fiilî içe aktarma | Türkçe/ayırıcı/boş RTT/UTC ve gerçek sayısal RTT | Excel 2010 açıldı, içe aktarma tamamlanmadı; bilgisayar kontrolü fiziksel Escape ile kullanıcı tarafından durduruldu | ÇALIŞTIRILMADI | Microsoft bağlantısı ve aşağıdaki adımlar | Özellikle BAĞ_DEĞ_SAY/COUNT veya sayısal tür denetimi |
| Dış istek engellenmiş panel/grafik | Yalnız uygulama origin'i açıkken grafik çalışsın | Araçta network route/offline yeteneği ilan edilmedi; arayüz işlemleri de durduruldu. Fiziksel internet kesintisi yapılmadı | ÇALIŞTIRILMADI | Yerel script kaynakları kaynak kodunda; Chart.js provenance | Tarayıcıya özgü dış istek engelleme ile deneme |
| Loopback IPv4 ICMP | Gerçek adaptör/manual/history; sahte RTT yok | `127.0.0.1`: icmp, reply, latency_ms=null, manual; aynı id geçmişte | GEÇTİ | `icmp-evidence.json`; 15:45:45 UTC | Yok |
| Loopback IPv6 ICMP | Destek varsa aynı doğrulama | `::1`: icmp, reply, latency_ms=null, manual; aynı id geçmişte | GEÇTİ | `icmp-evidence.json`; 15:45:45 UTC | Yok |
| ICMP sınırı | Yalnız loopback; mock sonuç üretmesin | IcmpProbeProvider sınıfı doğrulandı; otomatik izleme hep duraklatılmış; allowlist tam olarak 127.0.0.1/32,::1/128 | GEÇTİ | Kabul harness ayarları; izleme durumu ve iki manuel kontrol | Yok |
| Şirket/laboratuvar ICMP | Bu görevde trafik gönderilmesin | Şirket, gateway, özel ağ veya internet hedefi kontrol edilmedi | ÇALIŞTIRILMADI | Görev kapsamı; yalnız iki loopback sonucu | Ayrı yetkilendirilmiş laboratuvar testi |
| Ping hata / yerelleştirme | Eksik/yetkisiz ping error; Türkçe çıktı ve <1 doğru | 5 yeni parametrik regresyon; <1 null, süre=1,25 → 1.25, süre=0 → 0; FileNotFound/Permission → error | GEÇTİ | `tests/test_monitoring.py` | Diğer işletim sistemi/dil ayrı doğrulama |
| Backup / restore | Yeni dosya, tutarlı şema/kayıt; gerçek DB'ye dokunma | Çalışan kabul DB'sinden Backup API; yeni restore; integrity=ok, FK temiz; tüm satırlar eşit | GEÇTİ | `verify_backup.py`, `backup-evidence.json`; README örneği | Gerçek DB için güvenli yedek saklama |
| Restore temsili içerik | Şema ve örnek veriler korunsun | 20260912_0003; 2 cihaz, 8 ölçüm, 1 alarm, 2 sayaç, 22 audit, 2 kullanıcı, 6 oturum | GEÇTİ | Backup ve restore tablolarının tam satır eşitliği | Yok |
| Son pytest | Regresyonlar ve mevcut testler geçsin | **126 passed**, 53.97 sn, **1** mevcut Starlette/AnyIO deprecation uyarısı | GEÇTİ | `python -m pytest -q` | Uyarı bağımlılık güncellemesinde izlenebilir |
| Ruff / pip | Kod ve bağımlılık tutarlı | All checks passed; No broken requirements found | GEÇTİ | `python -m ruff check app tests alembic`; `python -m pip check`; ana kaynakta son `ruff check .` | Yok |
| Alembic tutarlılığı | Model ve head uyumlu | current=head=20260912_0003; No new upgrade operations detected | GEÇTİ | Restore DB çocuk ortamıyla `alembic current`, `heads`, `check` | Yok |
| Teslim ZIP | Güncel dosyalar, gerekli lisanslar; gizli/çalışma verisi yok | Açık dosya listesiyle paketlenir; ZIP yeniden açılır, CRC ve içerik hash'leri doğrulanır | GEÇTİ | `dist` içindeki benzersiz ZIP ve yanındaki manifest/kanıt JSON | ZIP'i ayrı klasöre açıp son demo |

## Düzeltme ve kalan sınırlar

`PingAdapter` daha önce `<1 ms` ifadesinden 0,5 ms uyduruyordu. Artık yanıt başarılı,
RTT `null`; mevcut rapor akışı bunu boş hücre/nokta olarak işler. Windows çıktısı OEM
kod sayfasıyla çözülür; Türkçe `süre` doğru ayrıştırılır. Şema değişmedi; geçmişte
yazılmış 0,5 ms kayıtları geriye dönük dönüştürülmedi, çünkü hangi kayıtların üst
sınırdan türetildiği saklanmıyordu.

İlk kabul harness denemesinde JSON alanı yanlışlıkla `trigger_source` okunmuştu;
API'nin doğru alanı `source` kullanılarak yardımcı düzeltildi ve yeni boş DB ile
tekrar edildi. Bu uygulama hatası değildi. Başarılı sonuçlar son denemeye aittir.

Yerel panel ve grafik script'leri `/static` altındadır. Admin `/docs` sayfası
FastAPI varsayılan Swagger UI CDN kaynaklarını kullanır; bu nedenle “uygulamanın
her ekranı çevrimdışı doğrulandı” denmez. Runtime paketlerinin transitif sürümleri
tam kilit dosyasıyla sabitlenmiş değildir; yukarıdaki sürümler bu kabul ortamıdır.
Tek süreç/SQLite, en çok 30 günlük rapor, grafikte 2.000 ve CSV'de en çok 50.000
satır sınırları geçerlidir. Tarihçe otomatik temizlenmez; bildirim/SNMP yoktur.

## Kullanıcının son 3 kontrolü

1. ZIP'i yeni klasöre açın; README sırasıyla runtime kurulumu, ayrı mock demo ve
   admin/viewer hesaplarını hazırlayın. `docs/demo.md` akışında grafiği ve filtreleri
   gösterin; unknown/no_reply RTT'nin 0 çizilmediğine bakın.
2. Normal tarayıcıda **Geçmiş → Filtreleri uygula → CSV indir** seçin. Tarayıcının
   indirmelerinde dosyanın gerçekten oluştuğunu kontrol edin. Aynı filtrelerde CSV
   satır sayısını özet toplamıyla karşılaştırın (grafik 2.000 ile sınırlı olabilir).
3. Excel'de **Veri → Metin/CSV'den**; UTF-8 65001, noktalı virgül, Türkçe sayı yerel
   ayarı seçin. Excel 2010'da karşılığı **Veri → Metinden** sihirbazıdır. Cihaz adı,
   IP ve UTC sütunlarını Metin, RTT'yi ondalık sayı olarak içe aktarın. Gerekirse
   sihirbazdaki Gelişmiş seçeneklerinde yalnız bu içe aktarımın ondalığını virgül
   seçin. Türkçe karakterleri, boş hücreleri ve Z ile biten UTC metnini inceleyin;
   RTT aralığında BAĞ_DEĞ_SAY/COUNT sonucunun dolu RTT sayısına eşit olduğunu doğrulayın.
   Windows bölge ayarını veya Excel güvenlik ayarlarını değiştirmeyin.

Kaynaklar: [Microsoft ping](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ping),
[Microsoft metin/CSV içe aktarma](https://support.microsoft.com/en-us/excel/get-started/import-or-export-text-txt-or-csv-files),
[Python sqlite3 backup](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup).
