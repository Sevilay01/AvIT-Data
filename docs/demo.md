# 5–7 dakikalık mock sunumu

Bu grafik/alarm senaryosu korunur. 0.5.0 bildirim/bakım paketi için ayrıca
[sahte saatli ağsız demo ve manuel kontrol listesi](demo-enterprise.md) vardır.

Bu demo gerçek ağ isteği göndermez. Normal kullanıcı veritabanını değiştirmez. Aşağıdaki komutları proje kökünde, ayrı bir PowerShell terminalinde sırayla çalıştırın.

```powershell
Set-Location "C:\Users\DELL\AvITData-Network-Monitor"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.demo --path .\demo-monitor.db
```

Komut migration'ları uygular ve üç cihaz ekler. Dosya varsa hata verir; silmez veya üzerine yazmaz. Tekrar hazırlamak için `demo-monitor-2.db` gibi yeni bir ad seçin ve aşağıdaki yolu da güncelleyin. Hesap/parola otomatik oluşturulmaz.

```powershell
$env:DATABASE_URL = "sqlite:///./demo-monitor.db"
$env:MONITOR_MODE = "mock"
$env:MOCK_DEMO = "true"
$env:MONITOR_INTERVAL_SECONDS = "5"
$env:ALARM_THRESHOLD = "3"
$env:APP_BASE_URL = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m app.cli create-user --username demo-admin --role admin
.\.venv\Scripts\python.exe -m app.cli create-user --username demo-viewer --role viewer
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Parolayı CLI iki kez görünmeden sorar (15–128 karakter). Komut satırına parola yazmayın. Mevcut hesap varsa tekrar oluşturmayın. Tarayıcıda http://127.0.0.1:8000 adresine girin. Otomatik izleme başlangıçta duraklatılmış olmalıdır.

## Süre planı

| Süre | Gösterilecek davranış |
|---|---|
| 0:00–0:45 | Admin girişi; MOCK etiketi, başlangıçta duraklatılmış izleme |
| 0:45–1:30 | Üç cihaz ve envanter alanları; gerçek hedeflere paket gönderilmediği |
| 1:30–2:15 | İzlemeyi başlat; üçüncü ardışık yanıtsızlıkta alarm, görüldü, aynı alarmın çözülmesi |
| 2:15–3:00 | Alarmın kim/zaman bilgisi; error ile no_reply farkı; izlemeyi duraklat |
| 3:00–4:15 | Geçmiş → gecikme grafiği; boşluklar, tam dönem özeti, İstanbul saatleri |
| 4:15–5:00 | CSV indir; somut filtrelerin eşleşmesi, UTF-8/UTC/ondalık virgül |
| 5:00–6:15 | Çıkış yap, demo-viewer ile gir; okuma/CSV var, yazma düğmeleri yok |
| 6:15–6:45 | Duraklatılmış durumu göster; terminalde Ctrl+C ile sunucuyu kapat |

Hesap oluşturma ve paket kurulumunu sunumdan önce tamamlayın. Viewer için ayrı
parola seçin; parolaları slayta/komut satırına yazmayın. 8000 doluysa hem portu
hem APP_BASE_URL değerini değiştirin. Güncel doğrulama sınırları [kabul raporundadır](acceptance.md).

## Alarmı adım adım gösterme

1. **MOCK / simülasyon**, üç aktif cihaz ve **Güncel ölçüm yok** metinlerini gösterin.
2. **Otomatik izlemeyi başlat** düğmesine basın. İlk tarama hemen, sonraki taramalar yaklaşık 5 saniye arayla olur.
3. `192.0.2.1` sürekli yanıt verir. `192.0.2.3` kontrol mekanizması hatası gösterir; bu hata cihaz arızası alarmı açmaz.
4. `192.0.2.2` sırası: **reply → no_reply → no_reply → no_reply → no_reply → reply**. İlk iki yanıtsızlıkta alarm yoktur. Dördüncü ölçümde (yaklaşık 15. saniye) tek alarm açılır. Beşinci ölçüm yeni alarm açmaz.
5. **Alarm detayı → Görüldü** seçin. Alarm hâlâ **Açık** kalır; kullanıcı ve zaman kaydolur. Altıncı ölçümde aynı alarm **Çözüldü** olur. Sıra bittiğinde bu hedef yanıt vermeye devam eder.
6. **İzlemeyi durdur** ile kapatın. Yeniden başlatınca açık/geçmiş alarm ve görüldü bilgisi korunur; bekleyen yanıtsızlık serisi sıfırlanır.

Düğmeleri anlatırken daha fazla zaman gerekiyorsa uygulamayı `Ctrl+C` ile kapatıp `MONITOR_INTERVAL_SECONDS=60` ile yeniden başlatın. İzlemeyi duraklatılmış bırakın; yaşam döngüsü cihazının **Şimdi kontrol et** düğmesine altı kez, sonuçları tek tek inceleyerek basın. Dördüncü kontrolde alarm açılır, beşincide tek kalır; **Görüldü** seçip altıncıda çözülmeyi gösterin. Manuel ve otomatik kontroller aynı diziyi tüketir; birlikte kullanırsanız sıra daha hızlı ilerler.

Mock dizisinin konumu yalnızca süreç belleğindedir: uygulama yeniden başlatıldığında başa döner; durdur/başlat veya sayfa yenilemesi diziyi sıfırlamaz. Yeniden başlatma açık alarmı tek başına çözmez; yeni mock reply çözer. Tamamen temiz sunum için yeni demo dosyası oluşturun. Alarm açık süresi kesin ağ kesintisi süresi değildir.

## Normal veritabanına dönüş

Sunucuyu `Ctrl+C` ile kapatın, demo terminalini kapatın. Yeni terminalde `.env` içindeki normal `DATABASE_URL`, `MOCK_DEMO=false`, `MONITOR_INTERVAL_SECONDS=60` değerlerini kullanın. Normal veritabanı migration'ı gerekiyorsa sunucu kapalıyken yedek aldıktan sonra `python -m alembic upgrade head` çalıştırın.

Teslimde `--reload` kullanmayın. Geliştirme için `--reload` yalnızca otomatik izleme duraklatılmışken kullanılabilir. Aynı SQLite dosyasıyla ikinci uygulama süreç kilidi nedeniyle açılmaz. ICMP kullanımı yalnızca izinli laboratuvarda ayrıca doğrulanacaktır; bu mock sunumu o doğrulamanın yerine geçmez.


## Alarm demosunun ardından grafik ve CSV (aşama 4)

1. Yukarıdaki alarm yaşam döngüsünü gösterip otomatik izlemeyi durdurun. **Demo · Alarm yaşam döngüsü → Geçmiş** seçin.
2. **Gecikme grafiği ve CSV** bölümünde varsayılan **Son 24 saat**, **MOCK / simülasyon**, **Tümü** filtrelerini ve Europe/Istanbul olarak yazılan somut aralığı gösterin. Grafikte reply noktaları RTT taşır; yanıtsızlık/error/eksik RTT 0 ms çizilmez.
3. **Son 1 saat → Filtreleri uygula** seçin. Ölçümler arasındaki gerçek zaman aralıklarını, tam dönemin toplam/sonuç sayılarını ve **Yanıt oranı** değerini anlatın. Hatalar ayrı sayılır; oran SLA veya kesintisiz çalışma süresi değildir.
4. Kaynağı **Zamanlanmış** veya **Manuel** yapıp uygulayın. Demo tamamen otomatik yapıldıysa manuel filtresi boş olabilir. **ICMP** modunu seçince mock kayıtların kaybolduğunu gösterin; bu işlem gerçek ping başlatmaz. **Özel aralık** alanları İstanbul saatidir, en fazla 30 gün seçilebilir.
5. Yeniden **MOCK / Tümü** uygulayıp **CSV indir** seçin. İndirme ekranda yazan başlangıç/bitişi aynen kullanır. Dosyada tarihlerin UTC, RTT ondalığının virgül, ayırıcının noktalı virgül olduğunu gösterin. Excel'e Veri → Metin/CSV'den, UTF-8 ve Türkçe sayı yerel ayarıyla içe aktarma adımları README'dedir. Excel'de fiilî test bu teslimde yapılmadı.
6. Viewer hesabıyla aynı raporu ve CSV düğmesini gösterebilirsiniz; izlemeyi başlatma ve diğer yazma düğmeleri viewer için bulunmaz.

Grafikte en yeni 2.000 ölçüm sınırı ve gerçek ölçüm kapsamı gösterilir; özet tüm filtrelenmiş dönemi kapsar. CSV sınırı varsayılan 50.000 satırdır; aşımda aralık daraltılması istenir. Raporu güncellemek için **Filtreleri uygula** kullanılır, otomatik grafik timer'ı yoktur. Chart.js yerel dosyadan gelir; grafik için internet/CDN gerekmez.
