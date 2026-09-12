# Beş dakikalık mock demo

Bu demo gerçek ağ isteği göndermez. Normal kullanıcı veritabanını değiştirmez. Aşağıdaki komutları proje kökünde, ayrı bir PowerShell terminalinde sırayla çalıştırın.

```powershell
Set-Location "C:\Users\DELL\AvITData-Network-Monitor"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
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
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Parolayı CLI iki kez görünmeden sorar (15–128 karakter). Komut satırına parola yazmayın. Mevcut hesap varsa tekrar oluşturmayın. Tarayıcıda http://127.0.0.1:8000 adresine girin. Otomatik izleme başlangıçta duraklatılmış olmalıdır.

## Gösterim

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
